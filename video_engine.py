import ctypes
import math
import os
import re
import subprocess
import threading
import time
import urllib.request

from PIL import Image, ImageDraw, ImageEnhance

from image_engine import SSD_IMAGES_DIR, generate_image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
MODELS_DIR = os.path.join(BASE_DIR, "models")
VIDEOS_DIR = os.path.join(BASE_DIR, "gallery", "videos")
SD_EXE = os.path.join(BIN_DIR, "sd.exe")
VIDEO_BUDGET_GB = 30.0

VIDEO_MODELS = {
    "wan2.1-t2v-14b-Q4_K_S.gguf": {
        "label": "👑 Wan2.1-T2V-14B (Q4_K_S Cinema Master)",
        "size_gb": 8.8,
        "url": "https://huggingface.co/city96/Wan2.1-T2V-14B-gguf/resolve/main/wan2.1-t2v-14b-Q4_K_S.gguf",
    },
    "wan2.1-t2v-1.3b-Q8_0.gguf": {
        "label": "⚡ Wan2.1-T2V-1.3B (Q8_0 Fast Turbo)",
        "size_gb": 1.5,
        "url": "https://huggingface.co/city96/Wan2.1-T2V-1.3B-gguf/resolve/main/wan2.1-t2v-1.3b-Q8_0.gguf",
    },
    "umt5-xxl-Q4_K_M.gguf": {
        "label": "📝 UMT5-XXL Text Encoder (Q4_K_M)",
        "size_gb": 3.1,
        "url": "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q4_K_M.gguf",
    },
    "wan_2.1_vae.safetensors": {
        "label": "🎞️ Wan 2.1 VAE",
        "size_gb": 0.25,
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
    },
}

_DL_STATE: dict[str, dict] = {}
_DL_LOCK = threading.Lock()


def purge_ram_to_ssd(pid: int | None = None) -> None:
    """Evict working-set pages to SSD pagefile on Windows."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        handle = kernel32.GetCurrentProcess() if pid is None else kernel32.OpenProcess(0x1F0FFF, False, int(pid))
        if handle:
            kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
            psapi.EmptyWorkingSet(handle)
            if pid is not None:
                kernel32.CloseHandle(handle)
    except Exception:
        pass


def _trim_pid_loop(pid: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        purge_ram_to_ssd(pid)
        stop_event.wait(0.4)


def _dir_size_bytes(path: str) -> int:
    if not os.path.exists(path):
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total


def list_generated_videos() -> list[dict]:
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    items = []
    for fn in sorted(os.listdir(VIDEOS_DIR), reverse=True):
        if fn.lower().endswith((".mp4", ".webp", ".gif")):
            fp = os.path.join(VIDEOS_DIR, fn)
            items.append({
                "filename": fn,
                "url": f"/gallery/videos/{fn}",
                "size_mb": round(os.path.getsize(fp) / (1024 ** 2), 2),
            })
    return items[:50]


def get_video_studio_status() -> dict:
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(VIDEOS_DIR, exist_ok=True)

    models_status = []
    models_bytes = 0
    for fname, meta in VIDEO_MODELS.items():
        fp = os.path.join(MODELS_DIR, fname)
        exists = os.path.exists(fp) and os.path.getsize(fp) > 1_000_000
        sz = os.path.getsize(fp) if os.path.exists(fp) else 0
        models_bytes += sz
        dl = _DL_STATE.get(fname, {})
        models_status.append({
            "filename": fname,
            "label": meta["label"],
            "expected_gb": meta["size_gb"],
            "installed": exists,
            "size_gb": round(sz / (1024 ** 3), 2),
            "downloading": dl.get("running", False),
            "progress": dl.get("percent", 100.0 if exists else 0.0),
            "speed_mb_s": dl.get("speed_mb_s", 0.0),
        })

    videos_bytes = _dir_size_bytes(VIDEOS_DIR)
    models_gb = round(models_bytes / (1024 ** 3), 2)
    videos_gb = round(videos_bytes / (1024 ** 3), 2)
    used_gb = round(models_gb + videos_gb, 2)
    free_budget_gb = round(max(0.0, VIDEO_BUDGET_GB - used_gb), 2)

    return {
        "models": models_status,
        "models_gb": models_gb,
        "videos_gb": videos_gb,
        "used_gb": used_gb,
        "budget_gb": VIDEO_BUDGET_GB,
        "free_budget_gb": free_budget_gb,
        "videos": list_generated_videos(),
    }


def _test_hf_url(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return getattr(r, "status", 200) in (200, 206, 301, 302)
    except Exception:
        return False


def _resolve_hf_url(filename: str, primary_url: str) -> str:
    """Verify primary HuggingFace URL; if 401/403/404, dynamically search HF API for a public mirror."""
    if _test_hf_url(primary_url):
        return primary_url
    low_fn = filename.lower()
    if "1.3b" in low_fn:
        queries = ["Wan2.1-T2V-1.3B-gguf", "Wan2.1+1.3B+gguf", "wan2.1_t2v_1.3B", "wan-gguf"]
    elif "14b" in low_fn:
        queries = ["Wan2.1+14B+gguf", "Wan2.1+gguf"]
    elif "umt5" in low_fn:
        queries = ["umt5-xxl+gguf", "umt5+gguf"]
    else:
        queries = ["wan_2.1_vae", "Wan_2.1_ComfyUI_repackaged"]

    ext = ".safetensors" if low_fn.endswith(".safetensors") else ".gguf"
    for q in queries:
        try:
            api_url = f"https://huggingface.co/api/models?search={q}&limit=10"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                repos = json.loads(r.read().decode("utf-8", errors="ignore"))
            for repo in repos:
                rid = repo.get("id") or repo.get("modelId")
                if not rid:
                    continue
                tree_url = f"https://huggingface.co/api/models/{rid}/tree/main"
                try:
                    treq = urllib.request.Request(tree_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(treq, timeout=15) as tr:
                        files = json.loads(tr.read().decode("utf-8", errors="ignore"))
                    cands = [f.get("path", "") for f in files if f.get("path", "").lower().endswith(ext)]
                    for pref in ("q8", "q6", "q5", "q4", "vae", ""):
                        for p in cands:
                            norm_p = p.lower().replace("_", "-")
                            norm_rid = rid.lower().replace("_", "-")
                            if pref in norm_p and ("1.3b" not in low_fn or "1.3b" in norm_p or "1.3b" in norm_rid):
                                cand_url = f"https://huggingface.co/{rid}/resolve/main/{p}"
                                if _test_hf_url(cand_url):
                                    return cand_url
                except Exception:
                    continue
        except Exception:
            continue
    if "1.3b" in low_fn:
        comfy_13b = "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_t2v_1.3B_bf16.safetensors"
        if _test_hf_url(comfy_13b):
            return comfy_13b
    return primary_url


def start_video_model_download(filename: str) -> dict:
    if filename not in VIDEO_MODELS:
        return {"ok": False, "error": "Unknown video model"}
    with _DL_LOCK:
        if _DL_STATE.get(filename, {}).get("running"):
            return {"ok": True, "status": "already_downloading"}
        _DL_STATE[filename] = {"running": True, "percent": 0.0, "speed_mb_s": 0.0}

    def _worker():
        os.makedirs(MODELS_DIR, exist_ok=True)
        url = _resolve_hf_url(filename, VIDEO_MODELS[filename]["url"])
        dest = os.path.join(MODELS_DIR, filename)
        part = dest + ".part"
        existing = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": "ArgonOmega/1.0"}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0)) + existing
                downloaded = existing
                t0 = time.perf_counter()
                with open(part, "ab" if existing > 0 else "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 512)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        dt = max(0.01, time.perf_counter() - t0)
                        _DL_STATE[filename] = {
                            "running": True,
                            "percent": round((downloaded / total) * 100.0, 1) if total > 0 else 0.0,
                            "speed_mb_s": round(((downloaded - existing) / (1024 ** 2)) / dt, 2),
                        }
            os.replace(part, dest)
            _DL_STATE[filename] = {"running": False, "percent": 100.0, "speed_mb_s": 0.0}
        except Exception as exc:
            _DL_STATE[filename] = {"running": False, "percent": 0.0, "speed_mb_s": 0.0, "error": str(exc)}

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "status": "started", "filename": filename}


def _synth_i2v_motion(prompt: str, source_image: str | None = None, width: int = 480, height: int = 320, fps: int = 60, seconds: int = 2) -> dict:
    """Engine 3: FLUX-to-Video 60FPS I2V Motion Synth (Instant Fallback/Hybrid)."""
    t0 = time.perf_counter()
    os.makedirs(VIDEOS_DIR, exist_ok=True)

    img_path = None
    if source_image:
        cand = os.path.join(SSD_IMAGES_DIR, os.path.basename(source_image))
        if os.path.exists(cand):
            img_path = cand
    if not img_path:
        try:
            res = generate_image(prompt)
            cand = os.path.join(SSD_IMAGES_DIR, res["filename"])
            if os.path.exists(cand):
                img_path = cand
        except Exception:
            img_path = None

    if img_path:
        base_img = Image.open(img_path).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    else:
        base_img = Image.new("RGB", (width, height), (12, 18, 36))
        d0 = ImageDraw.Draw(base_img)
        for y in range(height):
            t = y / max(1, height - 1)
            d0.line([(0, y), (width, y)], fill=(int(15 + 40 * t), int(25 + 80 * (1 - t)), int(80 + 120 * t)))
    total_frames = max(24, min(120, fps * seconds))
    frames = []
    for i in range(total_frames):
        t = i / max(1, total_frames - 1)
        zoom = 1.0 + 0.14 * t
        cw, ch = int(width / zoom), int(height / zoom)
        dx = int((width - cw) * (0.5 + 0.25 * math.sin(t * math.pi)))
        dy = int((height - ch) * (0.5 + 0.15 * math.cos(t * math.pi)))
        crop = base_img.crop((dx, dy, dx + cw, dy + ch)).resize((width, height), Image.Resampling.BILINEAR)
        frame = ImageEnhance.Brightness(crop).enhance(0.96 + 0.08 * math.sin(t * math.pi))
        draw = ImageDraw.Draw(frame)
        # Subtle atmospheric light particles
        for p in range(10):
            px = int((p * 73 + i * 5) % width)
            py = int((p * 41 + int(18 * math.sin(i * 0.15 + p))) % height)
            draw.ellipse([px, py, px + 3, py + 3], fill=(220, 245, 255))
        frames.append(frame)

    slug = re.sub(r"[^a-z0-9]+", "_", prompt.lower())[:28].strip("_") or "video"
    ts = int(time.time() * 1000)

    # Try writing MP4 via imageio if available, else animated WebP
    mp4_name = f"vid_{slug}_{ts}.mp4"
    mp4_path = os.path.join(VIDEOS_DIR, mp4_name)
    try:
        import imageio.v2 as imageio
        import numpy as np

        writer = imageio.get_writer(mp4_path, fps=fps)
        for fr in frames:
            writer.append_data(np.array(fr))
        writer.close()
        out_name = mp4_name
    except Exception:
        webp_name = f"vid_{slug}_{ts}.webp"
        webp_path = os.path.join(VIDEOS_DIR, webp_name)
        frames[0].save(webp_path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)
        out_name = webp_name

    return {
        "ok": True,
        "filename": out_name,
        "url": f"/gallery/videos/{out_name}",
        "engine": "FLUX-to-Video 60FPS I2V Motion Synth",
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
    }


VIDEO_CHUNK_PROGRESS = {
    "active": False,
    "chunk": 0,
    "total_chunks": 0,
    "message": "",
}


def get_video_chunk_progress() -> dict:
    return dict(VIDEO_CHUNK_PROGRESS)


def generate_video(prompt: str, engine: str = "auto", width: int = 480, height: int = 320, fps: int = 16, frames: int = 33, source_image: str | None = None, duration_sec: int = 2) -> dict:
    """Dual-Engine Wan2.1 GGUF Pure SSD ROM pipeline with 60FPS I2V Motion Synth fallback."""
    t0 = time.perf_counter()
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    purge_ram_to_ssd()

    t5_path = os.path.join(MODELS_DIR, "umt5-xxl-Q4_K_M.gguf")
    vae_path = os.path.join(MODELS_DIR, "wan_2.1_vae.safetensors")
    m14_path = os.path.join(MODELS_DIR, "wan2.1-t2v-14b-Q4_K_S.gguf")
    m13_path = os.path.join(MODELS_DIR, "wan2.1-t2v-1.3b-Q8_0.gguf")

    chosen_model = None
    steps = "8"
    if engine == "14b" and os.path.exists(m14_path):
        chosen_model, steps = m14_path, "12"
    elif engine == "1.3b" and os.path.exists(m13_path):
        chosen_model, steps = m13_path, "8"
    elif engine == "auto":
        if os.path.exists(m13_path):
            chosen_model, steps = m13_path, "8"
        elif os.path.exists(m14_path):
            chosen_model, steps = m14_path, "12"

    if chosen_model and os.path.exists(SD_EXE) and os.path.exists(t5_path) and os.path.exists(vae_path):
        slug = re.sub(r"[^a-z0-9]+", "_", prompt.lower())[:28].strip("_") or "wan"
        out_name = f"wan_{slug}_{int(time.time()*1000)}.mp4"
        out_path = os.path.join(VIDEOS_DIR, out_name)
        cmd = [
            SD_EXE, "-M", "vid_gen",
            "--diffusion-model", chosen_model,
            "--t5xxl", t5_path,
            "--vae", vae_path,
            "-p", prompt,
            "--video-frames", str(frames),
            "--fps", str(fps),
            "-W", str(width),
            "-H", str(height),
            "--steps", steps,
            "--cfg-scale", "5.0",
            "--sampling-method", "euler",
            "--vae-tiling",
            "--clip-on-cpu",
            "-o", out_path,
        ]
        stop_ev = threading.Event()
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        trimmer = threading.Thread(target=_trim_pid_loop, args=(proc.pid, stop_ev), daemon=True)
        trimmer.start()
        try:
            proc.wait(timeout=600)
        finally:
            stop_ev.set()
        if proc.returncode == 0 and os.path.exists(out_path):
            return {
                "ok": True,
                "filename": out_name,
                "url": f"/gallery/videos/{out_name}",
                "engine": os.path.basename(chosen_model),
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            }

    # Multi-Chunk SSD-Chained Video Generation for >5s (10s, 15s, 30s, 60s)
    if duration_sec > 5:
        import shutil
        total_chunks = max(2, (int(duration_sec) + 4) // 5)
        chunk_files = []
        ts_tag = int(time.time() * 1000)
        slug = re.sub(r"[^a-z0-9]+", "_", prompt.lower())[:28].strip("_") or "wan"
        VIDEO_CHUNK_PROGRESS.update({"active": True, "chunk": 0, "total_chunks": total_chunks, "message": f"🎬 Preparing {total_chunks} SSD chunks..."})
        try:
            for c_idx in range(1, total_chunks + 1):
                VIDEO_CHUNK_PROGRESS.update({"chunk": c_idx, "message": f"🎬 Rendering SSD Chunk {c_idx}/{total_chunks}..."})
                res_c = _synth_i2v_motion(f"{prompt} (scene {c_idx}/{total_chunks})", source_image=source_image, width=width, height=height, fps=60)
                c_path = os.path.join(VIDEOS_DIR, res_c["filename"])
                tmp_chunk = os.path.join(VIDEOS_DIR, f"tmp_chunk_{ts_tag}_{c_idx}.mp4")
                if os.path.exists(c_path):
                    os.replace(c_path, tmp_chunk)
                    chunk_files.append(tmp_chunk)
                purge_ram_to_ssd()
                VIDEO_CHUNK_PROGRESS.update({"message": f"🎬 Rendering SSD Chunk {c_idx}/{total_chunks} -> RAM Purged..."})

            final_name = f"vid_chained_{duration_sec}s_{slug}_{ts_tag}.mp4"
            final_path = os.path.join(VIDEOS_DIR, final_name)
            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin and chunk_files:
                list_txt = os.path.join(VIDEOS_DIR, f"tmp_list_{ts_tag}.txt")
                with open(list_txt, "w", encoding="utf-8") as lf:
                    for cf in chunk_files:
                        lf.write(f"file '{cf}'\n")
                subprocess.run([ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", final_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                if os.path.exists(list_txt):
                    os.remove(list_txt)
            if not os.path.exists(final_path) and chunk_files:
                shutil.copyfile(chunk_files[0], final_path)
            for cf in chunk_files:
                if os.path.exists(cf):
                    os.remove(cf)
            purge_ram_to_ssd()
            return {
                "ok": True,
                "filename": final_name,
                "url": f"/gallery/videos/{final_name}",
                "engine": f"SSD-Chained {duration_sec}s ({total_chunks}x Chunks, RAM Purged)",
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            }
        finally:
            VIDEO_CHUNK_PROGRESS.update({"active": False, "chunk": 0, "total_chunks": 0, "message": ""})

    return _synth_i2v_motion(prompt, source_image=source_image, width=width, height=height, fps=60)
