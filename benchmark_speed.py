import os
import time
from engine import answer_query
from cognitive_core import get_model_tier_info

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")


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


def run_benchmark(target_model: str = "all") -> dict:
    """Benchmark selected brain or all installed GGUF brains on Speed & Coding/Logic Accuracy."""
    models = list_benchmark_models()
    if target_model and target_model != "all":
        models = [m for m in models if m["filename"] == target_model]

    results = []
    for m in models:
        fname = m["filename"]
        r_chat = answer_query("What is the capital of India? Reply in 3 words.", model=fname, swarm_enabled=False)
        r_code = answer_query("Write a Python function add(a, b) that returns a + b.", model=fname, swarm_enabled=False)
        acc_score = 100 if ("new delhi" in r_chat.get("reply", "").lower() or "delhi" in r_chat.get("reply", "").lower()) else 50
        if "def add" in r_code.get("reply", ""):
            acc_score = min(100, acc_score + 0)
        else:
            acc_score = max(0, acc_score - 30)
        results.append({
            "filename": fname,
            "tier": m["tier"],
            "use_case": m.get("use_case", "General"),
            "size": m["size"],
            "ttft_ms": r_chat.get("latency_ms", 0.0),
            "tok_per_sec": r_chat.get("tok_per_sec", 0.0),
            "ms_per_token": r_chat.get("ms_per_token", 0.0),
            "accuracy_score": acc_score,
        })
    return {"results": results, "models": list_benchmark_models()}


if __name__ == "__main__":
    print(run_benchmark("all"))
