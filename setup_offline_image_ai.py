import json
import os
import shutil
import sys
import threading
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
SD_BIN_DIR = os.path.join(BIN_DIR, "sd")
MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(BIN_DIR, exist_ok=True)
os.makedirs(SD_BIN_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

SD_EXE_PATH = os.path.join(SD_BIN_DIR, "sd.exe")

SD_MODEL_MIRRORS = [
    ("sd_turbo.safetensors", "https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors", 1_500_000_000),
    ("v1-5-pruned-emaonly.safetensors", "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors", 3_000_000_000),
]

DOWNLOAD_STATUS = {
    "downloading": False,
    "progress_pct": 0.0,
    "downloaded_mb": 0.0,
    "total_mb": 0.0,
    "model_name": "sd_turbo.safetensors",
    "ready": False,
    "error": None,
}


def get_installed_sd_model():
    """Returns the path to the first valid (>500MB) Stable Diffusion model in `models/`."""
    if not os.path.isdir(MODELS_DIR):
        return None
    candidates = []
    for fname in os.listdir(MODELS_DIR):
        low = fname.lower()
        if low.endswith((".safetensors", ".gguf", ".ckpt")) and not low.endswith(".part"):
            if "qwen" in low or "llama" in low or "dolphin" in low or "deepseek" in low:
                continue
            fpath = os.path.join(MODELS_DIR, fname)
            if os.path.getsize(fpath) > 500_000_000:
                candidates.append(fpath)
    if candidates:
        candidates.sort(
            key=lambda p: (
                0 if ("q8_0" in os.path.basename(p).lower() and "turbo" in os.path.basename(p).lower())
                else (1 if ("turbo" in os.path.basename(p).lower() and p.lower().endswith(".gguf"))
                else (2 if "turbo" in os.path.basename(p).lower() else 3))
            )
        )
        return candidates[0]
    return None


def has_safe_ssd_space(min_free_gb: float = 45.0) -> bool:
    _, _, free = shutil.disk_usage(BASE_DIR)
    return (free / (1024 ** 3)) >= min_free_gb


def ensure_sd_cpp_binary() -> bool:
    """Downloads and extracts `bin/sd/sd.exe` (Windows x64 AVX2) from `leejet/stable-diffusion.cpp`."""
    cli_path = os.path.join(SD_BIN_DIR, "sd-cli.exe")
    if os.path.exists(SD_EXE_PATH) and os.path.getsize(SD_EXE_PATH) > 100_000:
        return True
    if os.path.exists(cli_path) and os.path.getsize(cli_path) > 100_000:
        shutil.copy2(cli_path, SD_EXE_PATH)
        return True
    if not has_safe_ssd_space(45.0):
        DOWNLOAD_STATUS["error"] = "SSD Guard: Less than 45GB free."
        return False

    print("[SD Setup] Downloading official `stable-diffusion.cpp` AVX2 binary (`sd.exe`)...")
    api_url = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        release = json.loads(resp.read().decode("utf-8"))

    asset_url = None
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if "win" in name and "avx2" in name and "x64" in name and name.endswith(".zip") and "cuda" not in name and "vulkan" not in name:
            asset_url = asset["browser_download_url"]
            break
    if not asset_url:
        for asset in release.get("assets", []):
            name = asset.get("name", "").lower()
            if "win" in name and "x64" in name and name.endswith(".zip") and "cuda" not in name and "vulkan" not in name:
                asset_url = asset["browser_download_url"]
                break

    if not asset_url:
        raise RuntimeError("Could not find Windows x64 zip in stable-diffusion.cpp release.")

    zip_path = os.path.join(SD_BIN_DIR, "sd_avx2.zip")
    req_zip = urllib.request.Request(asset_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_zip, timeout=60) as r, open(zip_path, "wb") as out_f:
        shutil.copyfileobj(r, out_f)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            base = os.path.basename(member)
            low = base.lower()
            if low.endswith(".exe") or low.endswith(".dll"):
                target_name = "sd.exe" if low in ("sd.exe", "sd-cli.exe") else base
                target_file = os.path.join(SD_BIN_DIR, target_name)
                with zf.open(member) as src, open(target_file, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    os.remove(zip_path)
    print(f"[SD Setup] `bin/sd/sd.exe` installed: {os.path.exists(SD_EXE_PATH)}")
    return os.path.exists(SD_EXE_PATH)


def download_offline_sd_model_sync() -> bool:
    """Downloads `models/sd_turbo.safetensors` with resume support and console progress bar."""
    existing = get_installed_sd_model()
    if existing:
        DOWNLOAD_STATUS["ready"] = True
        DOWNLOAD_STATUS["progress_pct"] = 100.0
        DOWNLOAD_STATUS["downloading"] = False
        print(f"[SD Setup] Offline model already installed: {existing}")
        return True

    if not has_safe_ssd_space(48.0):
        DOWNLOAD_STATUS["error"] = "SSD Guard: Need at least 48GB free space to download offline image model."
        DOWNLOAD_STATUS["downloading"] = False
        return False

    DOWNLOAD_STATUS["downloading"] = True
    DOWNLOAD_STATUS["error"] = None
    ensure_sd_cpp_binary()

    fname, url, min_bytes = SD_MODEL_MIRRORS[0]
    DOWNLOAD_STATUS["model_name"] = fname
    final_path = os.path.join(MODELS_DIR, fname)
    part_path = final_path + ".part"

    downloaded = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if downloaded > 0:
        headers["Range"] = f"bytes={downloaded}-"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=45) as resp:
            content_len = int(resp.info().get("Content-Length", 0))
            total_size = content_len + downloaded if content_len else 2_500_000_000
            DOWNLOAD_STATUS["total_mb"] = round(total_size / (1024 ** 2), 1)
            mode = "ab" if downloaded > 0 else "wb"
            with open(part_path, mode) as f:
                while True:
                    chunk = resp.read(2 * 1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    DOWNLOAD_STATUS["downloaded_mb"] = round(downloaded / (1024 ** 2), 1)
                    DOWNLOAD_STATUS["progress_pct"] = round((downloaded / total_size) * 100, 1)
                    sys.stdout.write(
                        f"\r[Offline SD-Turbo Download] {DOWNLOAD_STATUS['downloaded_mb']} MB / "
                        f"{DOWNLOAD_STATUS['total_mb']} MB ({DOWNLOAD_STATUS['progress_pct']}%)"
                    )
                    sys.stdout.flush()

        if os.path.getsize(part_path) >= min_bytes:
            os.replace(part_path, final_path)
            DOWNLOAD_STATUS["ready"] = True
            DOWNLOAD_STATUS["progress_pct"] = 100.0
            print(f"\n[SD Setup] SUCCESS! Offline model ready at: {final_path}")
            return True
    except Exception as e:
        DOWNLOAD_STATUS["error"] = str(e)
        print(f"\n[SD Setup Error] {e}")
    finally:
        DOWNLOAD_STATUS["downloading"] = False
    return False


def start_background_offline_model_install() -> None:
    if get_installed_sd_model():
        DOWNLOAD_STATUS["ready"] = True
        DOWNLOAD_STATUS["progress_pct"] = 100.0
        ensure_sd_cpp_binary()
        return
    if not DOWNLOAD_STATUS["downloading"]:
        t = threading.Thread(target=download_offline_sd_model_sync, daemon=True)
        t.start()


if __name__ == "__main__":
    ensure_sd_cpp_binary()
    download_offline_sd_model_sync()
