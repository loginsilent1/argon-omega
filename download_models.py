#!/usr/bin/env python3
"""AR GON-OMEGA cross-platform model downloader.

Portable replacement for download_models.ps1 (no PowerShell required).
Downloads the GGUF "brains" the app expects into models/, with resumable
transfers, progress reporting and size verification.

Examples
--------
    python download_models.py                # everything (~33 GB)
    python download_models.py --recommended  # Gemma-3 1B + Qwen2.5-Coder 7B (~5.5 GB)
    python download_models.py --only gemma-3-1b-it-Q4_K_M.gguf
    python download_models.py --list
    python download_models.py --hf-mirror https://hf-mirror.com   # alternate mirror

Filenames match engine.py's ELITE_BRAINS keys, so the UI detects them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")

# Keep these names in sync with ELITE_BRAINS in engine.py.
MODELS = [
    {
        "name": "gemma-3-1b-it-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/google_gemma-3-1b-it-GGUF/resolve/main/google_gemma-3-1b-it-Q4_K_M.gguf",
        "size_gb": 0.79,
        "label": "Fast general chat (default brain)",
        "recommended": True,
    },
    {
        "name": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "size_gb": 4.68,
        "label": "Master coder",
        "recommended": True,
    },
    {
        "name": "gemma-3-4b-it-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/google_gemma-3-4b-it-Q4_K_M.gguf",
        "size_gb": 2.49,
        "label": "Balanced general chat",
        "recommended": False,
    },
    {
        "name": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF/resolve/main/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
        "size_gb": 2.49,
        "label": "Fast reasoning / math",
        "recommended": False,
    },
    {
        "name": "phi-4-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/phi-4-GGUF/resolve/main/phi-4-Q4_K_M.gguf",
        "size_gb": 9.05,
        "label": "Deep reasoning (14B)",
        "recommended": False,
    },
    {
        "name": "qwen2.5-coder-32b-instruct-IQ3_XS.gguf",
        "url": "https://huggingface.co/bartowski/Qwen2.5-Coder-32B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-32B-Instruct-IQ3_XS.gguf",
        "size_gb": 13.2,
        "label": "Architect coder (32B, needs lots of RAM)",
        "recommended": False,
    },
]

CHUNK = 1024 * 256
MIN_VALID_MB = 100  # engine.py only accepts GGUFs larger than 100 MB


def human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024.0 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} GB"


def rewrite_host(url: str, mirror: str | None) -> str:
    """Point a Hugging Face URL at a mirror host (e.g. https://hf-mirror.com)."""
    if not mirror:
        return url
    parsed = urllib.parse.urlsplit(url)
    mirror = mirror.rstrip("/")
    if "://" not in mirror:
        mirror = "https://" + mirror
    m = urllib.parse.urlsplit(mirror)
    return urllib.parse.urlunsplit((m.scheme, m.netloc, parsed.path, parsed.query, ""))


def remote_size(url: str, timeout: float = 20.0) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "argon-omega-downloader"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:
        return None


def download_one(model: dict, dest_dir: str, min_mb: float, retries: int, mirror: str | None) -> bool:
    name = model["name"]
    url = rewrite_host(model["url"], mirror)
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, name)
    part = target + ".part"
    min_bytes = int(min_mb * 1024 * 1024)

    if os.path.exists(target) and os.path.getsize(target) > min_bytes:
        print(f"  [skip] {name} already installed ({human(os.path.getsize(target))})")
        return True

    expected = remote_size(url)
    if expected:
        print(f"  [find] {name} — {human(expected)} expected")
    else:
        print(f"  [find] {name} — size unknown (server did not report Content-Length)")

    for attempt in range(1, retries + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": "argon-omega-downloader"}
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"  [cont] resuming at {human(have)} (attempt {attempt}/{retries})")
        elif attempt > 1:
            print(f"  [retry] attempt {attempt}/{retries}")

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                if have and status == 200:
                    # Server ignored Range: start over rather than corrupt the file.
                    print("  [warn] server ignored Range header — restarting from zero")
                    have = 0
                    mode = "wb"
                else:
                    mode = "ab" if have else "wb"
                total = have + int(resp.headers.get("Content-Length") or 0)
                mode_flag = "wb" if status == 200 else mode
                with open(part, mode_flag) as fh:
                    written = have
                    started = time.time()
                    last_print = 0.0
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        written += len(chunk)
                        now = time.time()
                        if now - last_print > 0.2:
                            last_print = now
                            speed = (written - have) / max(now - started, 1e-6)
                            if total:
                                pct = 100.0 * written / total
                                print(
                                    f"\r  [get ] {name} {pct:5.1f}%  {human(written)} / {human(total)}  {human(speed)}/s   ",
                                    end="",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"\r  [get ] {name} {human(written)}  {human(speed)}/s   ",
                                    end="",
                                    flush=True,
                                )
                    print()

            final = os.path.getsize(part)
            if expected and final != expected:
                raise IOError(f"incomplete transfer: {human(final)} of {human(expected)}")
            if final <= min_bytes:
                raise IOError(
                    f"downloaded file is {human(final)}, below the {min_mb:.0f} MB minimum "
                    "(did the server return an error page?)"
                )
            os.replace(part, target)
            print(f"  [ ok ] saved {target} ({human(final)})")
            return True
        except Exception as exc:
            print(f"  [fail] {name}: {exc}")
            if attempt == retries:
                break
            time.sleep(min(2 * attempt, 10))
    return False


def load_manifest(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    models = payload.get("models", payload) if isinstance(payload, dict) else payload
    for entry in models:
        entry.setdefault("label", "")
        entry.setdefault("size_gb", 0.0)
        entry.setdefault("recommended", False)
    return models


def select(models: list[dict], only: str | None, recommended: bool) -> list[dict]:
    if only:
        wanted = [n.strip().casefold() for n in only.split(",") if n.strip()]
        chosen = [m for m in models if m["name"].casefold() in wanted]
        missing = sorted(set(wanted) - {m["name"].casefold() for m in chosen})
        if missing:
            print(f"error: unknown model(s): {', '.join(missing)}", file=sys.stderr)
            print("run with --list to see valid names", file=sys.stderr)
            raise SystemExit(2)
        return chosen
    if recommended:
        return [m for m in models if m.get("recommended")]
    return models


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download ARGON-OMEGA GGUF brains (cross-platform).")
    ap.add_argument("--list", action="store_true", help="list available models and exit")
    ap.add_argument("--only", metavar="NAMES", help="comma-separated model filenames")
    ap.add_argument("--recommended", action="store_true", help="only the recommended starter set")
    ap.add_argument("--dest", default=MODELS_DIR, help=f"target directory (default: {MODELS_DIR})")
    ap.add_argument("--min-mb", type=float, default=MIN_VALID_MB, help="minimum valid size in MB")
    ap.add_argument("--retries", type=int, default=4, help="attempts per model (default: 4)")
    ap.add_argument("--hf-mirror", metavar="URL", help="mirror host for huggingface.co URLs")
    ap.add_argument("--manifest", metavar="FILE", help="JSON manifest instead of the built-in list")
    args = ap.parse_args(argv)

    models = load_manifest(args.manifest) if args.manifest else MODELS

    if args.list:
        print(f"{'model':46} {'size':>8}  label")
        print("-" * 92)
        for m in models:
            star = "*" if m.get("recommended") else " "
            size = f"{m['size_gb']:.2f} GB" if m.get("size_gb") else "?"
            print(f"{star} {m['name']:44} {size:>8}  {m.get('label', '')}")
        print("\n* = included in --recommended")
        return 0

    chosen = select(models, args.only, args.recommended)
    total = sum(float(m.get("size_gb") or 0) for m in chosen)
    os.makedirs(args.dest, exist_ok=True)

    print(f"ARGON-OMEGA model downloader → {args.dest}")
    print(f"{len(chosen)} model(s), ~{total:.2f} GB total")
    if args.hf_mirror:
        print(f"using mirror: {args.hf_mirror}")

    ok = 0
    failed: list[str] = []
    for model in chosen:
        if download_one(model, args.dest, args.min_mb, args.retries, args.hf_mirror):
            ok += 1
        else:
            failed.append(model["name"])

    print(f"\ndone: {ok}/{len(chosen)} downloaded")
    if failed:
        print("failed: " + ", ".join(failed))
        print("partial transfers are kept as *.part and will resume on the next run")
        return 1
    print("start the app with: python app.py  → http://127.0.0.1:8080/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
