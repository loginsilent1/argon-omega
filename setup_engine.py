import io
import os
import shutil
import subprocess
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
MODELS_DIR = os.path.join(BASE_DIR, "models")

BRAIN_CARTRIDGES = [
    {
        "cartridge": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "label": "⚡ Fast Pro Coder (Qwen2.5-Coder-3B)",
        "repo_id": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "filename": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
    },
    {
        "cartridge": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "label": "👑 Master Coder (Qwen2.5-Coder-7B)",
        "repo_id": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    },
    {
        "cartridge": "qwen2.5-1.5b-instruct.gguf",
        "label": "PocketPal Official Qwen2.5-1.5B-Instruct",
        "repo_id": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "fallback_repo": "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        "fallback_file": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
    },
    {
        "cartridge": "qwen-0.5b-fast.gguf",
        "label": "⚡ Hyper-Speed Brain (3x Faster)",
        "repo_id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "fallback_repo": "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        "fallback_file": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
    },
    {
        "cartridge": "dolphin-1b.gguf",
        "label": "Fast Chat Brain",
        "repo_id": "bartowski/Dolphin3.0-Llama3.2-1B-GGUF",
        "filename": "Dolphin3.0-Llama3.2-1B-Q4_K_M.gguf",
    },
    {
        "cartridge": "qwen-1.5b-uncensored.gguf",
        "label": "Uncensored General & Fact Brain",
        "repo_id": "huihui-ai/Qwen2.5-1.5B-Instruct-abliterated-GGUF",
        "filename": "qwen2.5-1.5b-instruct-abliterated-q4_k_m.gguf",
        "fallback_repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "fallback_file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    },
    {
        "cartridge": "qwen-coder-1.5b.gguf",
        "label": "Master Coding & Security Brain",
        "repo_id": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "filename": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
    },
    {
        "cartridge": "deepseek-r1-1.5b.gguf",
        "label": "Deep Logic & Reasoning Brain",
        "repo_id": "bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF",
        "filename": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
    },
]


def install_dependencies() -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "huggingface_hub", "requests", "deep-translator"])


def download_llama_server(force: bool = False) -> str:
    import requests

    os.makedirs(BIN_DIR, exist_ok=True)
    exe_path = os.path.join(BIN_DIR, "llama-server.exe")
    dll_path = os.path.join(BIN_DIR, "llama.dll")
    if not force and os.path.exists(exe_path) and os.path.getsize(exe_path) > 4096 and os.path.exists(dll_path):
        print(f"[ARGON-OMEGA] llama-server.exe ready: {exe_path}")
        return exe_path

    print("[ARGON-OMEGA] Fetching ggml-org/llama.cpp release metadata...")
    resp = requests.get("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", timeout=20)
    resp.raise_for_status()
    assets = resp.json().get("assets", [])

    target_asset = None
    for preferred in ("bin-win-avx2-x64.zip", "bin-win-cpu-x64.zip", "bin-win-x64.zip"):
        for asset in assets:
            if preferred in asset.get("name", "").lower():
                target_asset = asset
                break
        if target_asset:
            break

    if not target_asset:
        for asset in assets:
            n = asset.get("name", "").lower()
            if "bin-win" in n and "x64" in n and n.endswith(".zip") and "cuda" not in n and "rocm" not in n:
                target_asset = asset
                break

    if not target_asset:
        raise RuntimeError("Could not find Windows CPU binary asset.")

    print(f"[ARGON-OMEGA] Downloading {target_asset['name']}...")
    zresp = requests.get(target_asset["browser_download_url"], timeout=120)
    zresp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(zresp.content)) as zf:
        for member in zf.namelist():
            fname = os.path.basename(member)
            if fname and (fname.lower() == "llama-server.exe" or fname.lower().endswith(".dll")):
                with zf.open(member) as src, open(os.path.join(BIN_DIR, fname), "wb") as dst:
                    dst.write(src.read())
    return exe_path


def _link_or_download(repo_id: str, filename: str, target_path: str) -> bool:
    from huggingface_hub import hf_hub_download

    os.makedirs(MODELS_DIR, exist_ok=True)
    # Check if raw filename already exists in models/
    existing_raw = os.path.join(MODELS_DIR, filename)
    if os.path.exists(existing_raw) and os.path.getsize(existing_raw) > 100_000_000:
        if os.path.abspath(existing_raw) != os.path.abspath(target_path) and not os.path.exists(target_path):
            try:
                os.link(existing_raw, target_path)
            except OSError:
                shutil.copy2(existing_raw, target_path)
        return True

    print(f"[ARGON-OMEGA] Downloading {repo_id}/{filename} -> {os.path.basename(target_path)}...")
    dl_path = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=MODELS_DIR)
    if os.path.abspath(dl_path) != os.path.abspath(target_path) and not os.path.exists(target_path):
        try:
            os.link(dl_path, target_path)
        except OSError:
            shutil.copy2(dl_path, target_path)
    return True


def download_all_cartridges() -> list[dict]:
    """Ensure all 4 specialist GGUF cartridges exist in models/."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    results = []
    for item in BRAIN_CARTRIDGES:
        cart_name = item["cartridge"]
        target_path = os.path.join(MODELS_DIR, cart_name)
        if os.path.exists(target_path) and os.path.getsize(target_path) > 100_000_000:
            size_mb = round(os.path.getsize(target_path) / (1024 ** 2), 1)
            print(f"[ARGON-OMEGA] Ready: {cart_name} ({size_mb} MB)")
            results.append({"cartridge": cart_name, "status": "ready", "size_mb": size_mb})
            continue

        ok = False
        try:
            ok = _link_or_download(item["repo_id"], item["filename"], target_path)
        except Exception as exc:
            print(f"[ARGON-OMEGA] Primary failed for {cart_name}: {exc}")
            if "fallback_repo" in item:
                try:
                    ok = _link_or_download(item["fallback_repo"], item["fallback_file"], target_path)
                except Exception as exc2:
                    print(f"[ARGON-OMEGA] Fallback failed for {cart_name}: {exc2}")

        if ok and os.path.exists(target_path):
            size_mb = round(os.path.getsize(target_path) / (1024 ** 2), 1)
            results.append({"cartridge": cart_name, "status": "downloaded", "size_mb": size_mb})
        else:
            results.append({"cartridge": cart_name, "status": "failed", "size_mb": 0.0})
    return results


def download_realesrgan() -> str | None:
    """Download Real-ESRGAN Windows NCNN-Vulkan binary into bin/."""
    import requests

    os.makedirs(BIN_DIR, exist_ok=True)
    exe_path = os.path.join(BIN_DIR, "realesrgan-ncnn-vulkan.exe")
    if os.path.exists(exe_path) and os.path.getsize(exe_path) > 1_000_000:
        print(f"[ARGON-OMEGA] realesrgan-ncnn-vulkan.exe already present: {exe_path}")
        return exe_path

    try:
        print("[ARGON-OMEGA] Fetching Real-ESRGAN release metadata...")
        resp = requests.get("https://api.github.com/repos/xinntao/Real-ESRGAN/releases/latest", timeout=20)
        resp.raise_for_status()
        assets = resp.json().get("assets", [])
        target_asset = None
        for asset in assets:
            n = asset.get("name", "").lower()
            if "realesrgan-ncnn-vulkan" in n and "windows" in n and n.endswith(".zip"):
                target_asset = asset
                break
        if not target_asset:
            print("[ARGON-OMEGA] Could not find Windows Real-ESRGAN asset.")
            return None

        print(f"[ARGON-OMEGA] Downloading {target_asset['name']}...")
        zresp = requests.get(target_asset["browser_download_url"], timeout=120)
        zresp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(zresp.content)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                rel = member.lstrip("/")
                # Preserve models/ subfolder next to realesrgan-ncnn-vulkan.exe
                if "/models/" in member or member.startswith("models/"):
                    sub = member[member.index("models/"):]
                    dest = os.path.join(BIN_DIR, sub)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                else:
                    dest = os.path.join(BIN_DIR, os.path.basename(rel))
                with zf.open(member) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
        print(f"[ARGON-OMEGA] Extracted Real-ESRGAN to {BIN_DIR}")
        return exe_path if os.path.exists(exe_path) else None
    except Exception as exc:
        print(f"[ARGON-OMEGA] Real-ESRGAN download failed: {exc}")
        return None


def install_image_editing_toolkit() -> None:
    print("[ARGON-OMEGA] Installing rembg, onnxruntime, and pillow...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "rembg", "onnxruntime", "pillow"])
    download_realesrgan()


if __name__ == "__main__":
    install_dependencies()
    download_llama_server()
    res = download_all_cartridges()
    print("[ARGON-OMEGA] Cartridge Summary:", res)
