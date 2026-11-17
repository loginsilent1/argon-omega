import os
import time

from engine import answer_query
from cognitive_core import get_model_tier_info

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")

# A small, deterministic accuracy battery. Each check has a pass predicate so the
# score is reproducible instead of a vibe: arithmetic, units, a fact lookup,
# two code recipes, two knowledge entries, and one honesty check (the brain must
# refuse an unknown topic instead of inventing an answer).
CHECKS = [
    ("arithmetic", "what is 17*24+9", lambda r: "417" in r),
    ("percentage", "what is 15% of 240", lambda r: "36" in r),
    ("units", "12 km to miles", lambda r: "7.45" in r),
    ("fact lookup", "What is the capital of India? Reply in 3 words.", lambda r: "delhi" in r.lower()),
    ("code: add()", "Write a Python function add(a, b) that returns a + b.", lambda r: "def add" in r),
    ("code: reverse", "write a python function to reverse a string", lambda r: "def reverse" in r),
    ("knowledge: deadlock", "explain deadlock", lambda r: "circular wait" in r.lower()),
    ("knowledge: mutex", "difference between mutex and semaphore", lambda r: "ownership" in r.lower()),
    ("honesty: unknown", "explain xyzzyplugh quux", lambda r: "don't have" in r.lower() or "no model weights" in r.lower()),
    ("decision: sqlite/postgres", "should I use postgres or sqlite", lambda r: "one writer" in r.lower() or "postgres" in r.lower()),
    ("debug: indexerror", "fix this IndexError list index out of range", lambda r: "len(seq) - 1" in r or "empty" in r.lower()),
    ("debug: keyerror", "how do i fix a KeyError", lambda r: "d.get(" in r or "get(key" in r.lower()),
]


def list_benchmark_models() -> list[dict]:
    items = []
    if not os.path.isdir(MODELS_DIR):
        return items
    for f in sorted(os.listdir(MODELS_DIR)):
        low = f.lower()
        if low.endswith(".gguf") and not low.endswith(".part") and "sd_turbo" not in low and "flux" not in low and "dreamshaper" not in low:
            mb = round(os.path.getsize(os.path.join(MODELS_DIR, f)) / (1024 ** 2), 1)
            sz = f"{round(mb / 1024, 2)} GB" if mb >= 1024 else f"{mb} MB"
            tier, _ = get_model_tier_info(f)
            use_case = "Instant Chat" if "LOW" in tier else ("Daily Coding" if "MID" in tier else "Deep Architecture")
            items.append({"filename": f, "size": sz, "size_mb": mb, "tier": tier, "use_case": use_case})
    return items


def _accuracy(model: str = "auto") -> tuple[int, str, int]:
    """Run the battery; return (score, 'passed/total', passed_count)."""
    passed = 0
    for _name, prompt, predicate in CHECKS:
        reply = answer_query(prompt, model=model, swarm_enabled=False).get("reply", "")
        try:
            if predicate(reply):
                passed += 1
        except Exception:
            pass
    total = len(CHECKS)
    return round(100 * passed / total), f"{passed}/{total}", passed


def _demo_benchmark() -> dict | None:
    """Measured demo rows so the Benchmark tab is meaningful with no GGUF."""
    try:
        import demo_brain
    except Exception:
        return None
    if not demo_brain.enabled():
        return None

    # Speed: measure the honest path the UI uses (one chat turn, no cache trickery).
    timings = []
    for prompt in ("What is the capital of India? Reply in 3 words.",
                   "Write a Python function add(a, b) that returns a + b."):
        t0 = time.perf_counter()
        answer_query(prompt, model="auto", swarm_enabled=False)
        timings.append((time.perf_counter() - t0) * 1000.0)

    score, frac, passed = _accuracy("auto")
    sample = answer_query("explain deadlock", model="auto", swarm_enabled=False)
    tokens = max(1, len(sample.get("reply", "").split()))
    total_ms = round(sum(timings) / max(1, len(timings)), 2)
    tps = round(tokens / max(total_ms / 1000.0, 1e-6), 1)
    row = {
        "filename": "Argon Demo Brain (template + retrieval)",
        "tier": "DEMO",
        "use_case": "UI preview · no weights",
        "size": "0 MB",
        "size_mb": 0.0,
        "ttft_ms": total_ms,
        "tok_per_sec": tps,
        "ms_per_token": round(total_ms / tokens, 4),
        "accuracy_score": score,
        "checks": frac,
        "checks_passed": passed,
        "checks_total": len(CHECKS),
        "demo": True,
    }
    return {"results": [row], "models": [row], "demo": True}


def run_benchmark(target_model: str = "all") -> dict:
    """Benchmark the selected brain (or all installed GGUFs) on measured speed + accuracy."""
    models = list_benchmark_models()
    demo = _demo_benchmark()
    if not models and demo and target_model in ("all", "demo", "auto", ""):
        return demo
    if target_model and target_model != "all":
        models = [m for m in models if m["filename"] == target_model]

    results = []
    for m in models:
        fname = m["filename"]
        t0 = time.perf_counter()
        answer_query("What is the capital of India? Reply in 3 words.", model=fname, swarm_enabled=False)
        ttft = (time.perf_counter() - t0) * 1000.0
        score, frac, passed = _accuracy(fname)
        r_code = answer_query("Write a Python function add(a, b) that returns a + b.", model=fname, swarm_enabled=False)
        tokens = max(1, len(r_code.get("reply", "").split()))
        tps = round(tokens / max(ttft / 1000.0, 1e-6), 1)
        results.append({
            "filename": fname,
            "tier": m["tier"],
            "use_case": m.get("use_case", "General"),
            "size": m["size"],
            "ttft_ms": round(ttft, 1),
            "tok_per_sec": tps,
            "ms_per_token": round(ttft / tokens, 2),
            "accuracy_score": score,
            "checks": frac,
            "checks_passed": passed,
            "checks_total": len(CHECKS),
        })
    return {"results": results, "models": list_benchmark_models()}


if __name__ == "__main__":
    print(run_benchmark("all"))
