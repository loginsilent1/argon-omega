"""speed_profile.py - one place that decides how llama-server is launched.

The problem this fixes
---------------------
The engine hard-codes `-c 32768` for every model. For a 32B model with
64 layers x 8 KV heads x 128 head-dim the KV cache is 256 KB *per token*, so a
32k context asks for ~8 GB of RAM on top of a 13.4 GB file -- the machine swaps
and decode collapses to ~1 tok/s. Launch flags must follow the model size and
what the box can actually do (flash-attn, VNNI, RAM).

Everything here is a pure function so it can be unit-tested without a model.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess

# profile -> (context tokens, kv cache type or None)
PROFILES = {
    # name        ctx    kv      intent
    "speed":   (2048, "q4_0"),   # smallest RAM, fastest on CPU, shortest prompts
    "fast":    (4096, "q8_0"),   # recommended default
    "balanced": (8192, "q8_0"),  # longer prompts, still sane KV
    "quality": (16384, "f16"),   # only if RAM is plentiful
}

# Below this much RAM (MB) we never ask for a big context.
RAM_FLOOR_MB = 6000


def model_size_mb(path_or_name: str) -> float:
    """Best-effort model size in MB; 0 if unknown."""
    try:
        if os.path.exists(path_or_name):
            return os.path.getsize(path_or_name) / 2**20
    except OSError:
        pass
    name = os.path.basename(path_or_name).lower()
    # common quant suffixes -> bytes per parameter
    bytes_per_param = {"iq3_xs": 0.42, "iq3_m": 0.44, "q3_k_m": 0.48, "q4_k_m": 0.61,
                       "q4_0": 0.56, "q5_k_m": 0.71, "q8_0": 1.06, "f16": 2.0}
    params = 0.0
    for p in (0.5, 1.5, 3, 7, 8, 13, 14, 32, 34, 70):
        if re.search(r"(?<!\d)%g ?b\b" % p, name):
            params = p
            break
    for q, bpp in bytes_per_param.items():
        if q in name and params:
            return params * bpp * 1024
    return 0.0


def size_class(mb: float) -> str:
    if mb <= 0:
        return "unknown"
    if mb < 1500:
        return "small"      # <=1.5B: everything fits, max context
    if mb < 6000:
        return "medium"     # 7B-class
    return "large"          # 13B+: KV cache is the enemy


def detect_capabilities() -> dict:
    """What can this machine actually honour? Never guess silently."""
    caps = {"ram_mb": 0, "avx2": False, "avx512": False, "vnni": False,
            "llama_server": None, "flash_attn_flag": False, "cache_type_flag": False,
            "ngl_default": 0}
    try:
        mem = open("/proc/meminfo", encoding="utf-8").read()
        caps["ram_mb"] = int(re.search(r"MemAvailable:\s+(\d+) kB", mem).group(1)) // 1024
    except Exception:
        pass
    try:
        cpu = open("/proc/cpuinfo", encoding="utf-8", errors="replace").read()
        caps["avx2"] = " avx2" in cpu
        caps["avx512"] = " avx512f" in cpu
        caps["vnni"] = ("avx512vnni" in cpu) or ("avx_vnni" in cpu) or ("avxvnni" in cpu)
    except Exception:
        pass
    exe = shutil.which("llama-server") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "llama-server")
    if os.path.exists(exe):
        caps["llama_server"] = exe
        try:
            help_text = subprocess.run([exe, "--help"], capture_output=True, text=True,
                                       timeout=8).stdout
            caps["flash_attn_flag"] = "--flash-attn" in help_text
            caps["cache_type_flag"] = "--cache-type-k" in help_text
            m = re.search(r"--n-gpu-layers", help_text)
            if m:
                caps["ngl_default"] = 0    # flags exist; caller may set ARGON_NGL
        except Exception:
            pass
    return caps


def plan_launch(model: str, caps: dict | None = None, profile: str | None = None,
                kwargs: dict | None = None, threads: int | None = None) -> dict:
    """Return the exact flags to launch with, plus why. Pure function."""
    caps = caps or detect_capabilities()
    kwargs = kwargs or {}
    mb = kwargs.get("model_mb") or model_size_mb(model)
    cls = size_class(mb)
    profile = (profile or os.environ.get("ARGON_SPEED_PROFILE") or "fast").lower()
    if profile not in PROFILES:
        profile = "fast"
    ctx, kv = PROFILES[profile]

    # Big models must not ask for a big context: KV cache grows linearly with ctx.
    reasons = [f"profile={profile}", f"model≈{mb:.0f} MB ({cls})"]
    if cls == "large" and ctx > 4096:
        ctx = 4096
        reasons.append("large model: capped ctx 4096 to keep KV in RAM")
    if cls == "small" or cls == "unknown":
        pass
    ram = caps.get("ram_mb") or 0
    if ram and ram < RAM_FLOOR_MB and ctx > 2048:
        ctx = 2048
        reasons.append(f"only {ram} MB free: ctx 2048")
    # model does not fit in RAM -> leave mmap on, it is the only way to run at all
    no_mmap = bool(ram and mb and (mb * 1.15 + ctx * 0.00025 * 1024) < ram * 0.85)
    if no_mmap:
        reasons.append("model fits: --no-mmap keeps weights resident (faster decode)")
    else:
        reasons.append("model tight: --mmap streams from disk")
    # KV cache quantisation only if the build supports it
    if kv and not caps.get("cache_type_flag", False):
        reasons.append(f"build lacks --cache-type-k: KV falls back to f16 (was {kv})")
        kv = None
    flash = bool(caps.get("flash_attn_flag"))
    if kv and not flash:
        reasons.append("q8/q4 KV without flash-attn is slow: KV f16")
        kv = None
    threads = int(threads or kwargs.get("threads") or max(1, (os.cpu_count() or 4) // 2))
    ngl = int(os.environ.get("ARGON_NGL", "0") or 0)
    if ngl:
        reasons.append(f"offloading {ngl} layers to GPU")

    argv = ["--host", "127.0.0.1", "--port", "8081", "--no-repack", "-np", "1",
            "-c", str(ctx), "-b", str(min(2048, ctx)), "-ub", "512",
            "-t", str(threads), "-tb", str(threads), "--context-shift"]
    argv += ["--mmap"] if not no_mmap else ["--no-mmap"]
    if flash:
        argv.append("--flash-attn")
    if kv:
        argv += ["--cache-type-k", kv, "--cache-type-v", kv]
    if ngl:
        argv += ["-ngl", str(ngl)]
    slot_dir = kwargs.get("slot_dir")
    if slot_dir:
        os.makedirs(slot_dir, exist_ok=True)
        argv += ["--slot-save-path", slot_dir]
    if os.path.exists(model):
        argv = ["-m", model] + argv

    return {
        "model": os.path.basename(model),
        "model_mb": round(mb, 1),
        "size_class": cls,
        "profile": profile,
        "context": ctx,
        "kv_cache": kv or "f16",
        "flash_attn": flash,
        "mmap": not no_mmap,
        "threads": threads,
        "ngl": ngl,
        "argv": argv,
        "reasons": reasons,
        "trim_working_set": (mb < 6000),   # never evict a big model 4x/second
    }


def kv_mb(ctx: int, kv: str = "f16", layers=64, heads=8, dim=128) -> float:
    """KV cache size in MB for a given context."""
    per_token = 2 * layers * heads * dim * 2
    div = {"f16": 1, "q8_0": 2, "q4_0": 4}[kv]
    return per_token * ctx / div / 2**20


if __name__ == "__main__":
    caps = detect_capabilities()
    print(json.dumps(caps, indent=2))
    for name in ("qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
                 "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
                 "qwen2.5-coder-32b-instruct-IQ3_XS.gguf"):
        p = plan_launch(name, caps)
        print(f"\n{name}  ctx={p['context']} kv={p['kv_cache']} mmap={p['mmap']} "
              f"threads={p['threads']} trim={p['trim_working_set']}")
        print(" ", " ".join(p["argv"]))
        for r in p["reasons"]:
            print("   -", r)
