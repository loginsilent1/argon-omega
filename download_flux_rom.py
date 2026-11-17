"""
download_flux_rom.py - Resumable HuggingFace Downloader for FLUX.1-schnell Pure SSD ROM Mode
Downloads flux1-schnell-Q4_K_S.gguf and its 3 companion files into models/flux/ with live progress.
"""
import os
import threading
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FLUX_DIR = os.path.join(BASE_DIR, "models", "flux")
os.makedirs(FLUX_DIR, exist_ok=True)

FLUX_FILES = [
    (
        "flux1-schnell-Q4_K_S.gguf",
        "https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q4_K_S.gguf",
    ),
    (
        "t5xxl_q4_k.gguf",
        "https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q4_K_M.gguf",
    ),
    (
        "clip_l.safetensors",
        "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors",
    ),
    (
        "ae.safetensors",
        "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/ae.safetensors",
    ),
]

FLUX_DOWNLOAD_STATUS = {
    "downloading": False,
    "current_file": "",
    "file_index": 0,
    "total_files": len(FLUX_FILES),
    "progress_pct": 0.0,
    "downloaded_mb": 0.0,
    "total_mb": 0.0,
    "ready": False,
    "error": None,
}
_DL_LOCK = threading.Lock()


def _download_with_resume(url: str, dest_path: str, file_idx: int):
    part_path = dest_path + ".part"
    existing = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    headers = {"User-Agent": "ArgonOmega/1.0"}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        status = getattr(resp, "status", 200)
        if status == 200 and existing > 0:
            existing = 0
            mode = "wb"
        elif status == 206:
            mode = "ab"
        else:
            mode = "wb"

        cl = resp.headers.get("Content-Length")
        total = (existing + int(cl)) if cl and cl.isdigit() else 0
        downloaded = existing

        with open(part_path, mode) as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                FLUX_DOWNLOAD_STATUS["downloaded_mb"] = round(downloaded / (1024 * 1024), 1)
                FLUX_DOWNLOAD_STATUS["total_mb"] = round(total / (1024 * 1024), 1) if total else 0.0
                if total > 0:
                    FLUX_DOWNLOAD_STATUS["progress_pct"] = round((downloaded / total) * 100.0, 1)

    os.replace(part_path, dest_path)


def _worker():
    try:
        FLUX_DOWNLOAD_STATUS["error"] = None
        for idx, (fname, url) in enumerate(FLUX_FILES, start=1):
            dest = os.path.join(FLUX_DIR, fname)
            if os.path.exists(dest) and os.path.getsize(dest) > 10_000_000:
                continue
            FLUX_DOWNLOAD_STATUS["current_file"] = fname
            FLUX_DOWNLOAD_STATUS["file_index"] = idx
            FLUX_DOWNLOAD_STATUS["progress_pct"] = 0.0
            _download_with_resume(url, dest, idx)
        FLUX_DOWNLOAD_STATUS["ready"] = True
        FLUX_DOWNLOAD_STATUS["progress_pct"] = 100.0
        FLUX_DOWNLOAD_STATUS["current_file"] = "All FLUX.1-schnell files ready on SSD"
    except Exception as e:
        FLUX_DOWNLOAD_STATUS["error"] = str(e)
    finally:
        FLUX_DOWNLOAD_STATUS["downloading"] = False


def start_flux_download() -> dict:
    with _DL_LOCK:
        if FLUX_DOWNLOAD_STATUS["downloading"]:
            return {"ok": True, "started": False, "status": dict(FLUX_DOWNLOAD_STATUS)}
        FLUX_DOWNLOAD_STATUS["downloading"] = True
        FLUX_DOWNLOAD_STATUS["error"] = None
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"ok": True, "started": True, "status": dict(FLUX_DOWNLOAD_STATUS)}


def get_flux_download_status() -> dict:
    return dict(FLUX_DOWNLOAD_STATUS)
