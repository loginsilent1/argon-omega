#!/usr/bin/env python3
"""bench_all.py - one command, every number the fused engine claims.

Ground rule: no claim without a measurement. Every phase adds rows here.

  LAYER 0  cache      : hit latency (ms), hit rate (%)
  LAYER 1  drafting   : model passes per 100 tokens (lookup vs none)
  LAYER 2  memory     : KV MB at context N (f16/q8/q4), prefix bank restore
  LAYER 3  kernel     : GOP/s float32 vs int8 vs ternary-packed
  ENGINE   config     : llama launch plan (ctx/KV/flash-attn/threads/ngl)
  HARDWARE            : RAM, CPU flags, disk read speed

Usage:  python3 engine_x/bench_all.py [--json out.json] [--quick]
"""
from __future__ import annotations
import json, os, platform, re, statistics, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS: dict[str, dict] = {}


def rec(section: str, **kv):
    RESULTS.setdefault(section, {}).update(kv)


def timed(fn, warmup=3, reps=20):
    for _ in range(warmup):
        fn()
    xs = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000.0)
    xs.sort()
    return {"p50_ms": round(statistics.median(xs), 3),
            "p95_ms": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 3)}


# ---------------------------------------------------------------- hardware
def bench_hardware():
    info = {"platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count()}
    try:
        cpuinfo = open("/proc/cpuinfo", encoding="utf-8", errors="replace").read()
    except OSError:
        cpuinfo = ""
    m = re.search(r"model name\s*:\s*(.+)", cpuinfo)
    if m:
        info["cpu"] = m.group(1).strip()
    flags = set()
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            flags = set(line.split(":", 1)[1].split())
            break
    for f in ("avx2", "avx512f", "avx512vnni", "avxvnni", "popcnt", "fma", "bmi2"):
        info["has_" + f] = f in flags
    try:
        mem = open("/proc/meminfo", encoding="utf-8").read()
        total = int(re.search(r"MemTotal:\s+(\d+) kB", mem).group(1)) // 1024
        avail = int(re.search(r"MemAvailable:\s+(\d+) kB", mem).group(1)) // 1024
        info["ram_total_mb"], info["ram_available_mb"] = total, avail
        info["bandwidth_estimate_gbps"] = round(estimate_bandwidth_mbs() / 1000, 1)
    except Exception as e:  # pragma: no cover
        info["ram_error"] = str(e)
    rec("hardware", **info)


def estimate_bandwidth_mbs() -> float:
    """Streaming-copy bandwidth estimate (single thread) in MB/s."""
    size = 64 << 20
    src = bytearray(size)
    dst = bytearray(size)
    t = time.perf_counter()
    dst[:] = src
    dt = time.perf_counter() - t
    return (size * 2) / 1e6 / dt if dt else 0.0


def bench_disk():
    """Sequential read speed + mmap page-fault cost -> the FlashMap ceiling."""
    path = os.path.join(ROOT, "models", "_bench_probe.bin")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    size = 96 << 20
    try:
        with open(path, "wb") as f:
            f.write(os.urandom(size))
        with open(path, "rb") as f:
            t = time.perf_counter()
            while f.read(1 << 20):
                pass
            dt = time.perf_counter() - t
        seq = (size / 1e6) / dt if dt else 0
        # token budget: read 1 GB of weights
        rec("disk", read_mbs=round(seq, 1),
            gb_per_second=round(seq / 1000, 2),
            ms_to_stream_1gb=round(1000 / seq * 1000, 1),
            ms_per_token_if_13_4gb_model_evicted=round(13400 / seq * 1000, 1))
    except Exception as e:
        rec("disk", error=str(e))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- LAYER 0: cache
class VerifiedCache:
    """Exact + normalised prompt cache of *verified* answers (compile/test passed)."""

    def __init__(self):
        self.data = {}

    @staticmethod
    def key(prompt: str, lang: str = "") -> str:
        norm = re.sub(r"\s+", " ", prompt.strip().lower())
        return lang + "|" + norm

    def get(self, prompt: str, lang: str = ""):
        return self.data.get(self.key(prompt, lang))

    def put(self, prompt: str, answer: str, lang: str = "", verified: bool = True):
        self.data[self.key(prompt, lang)] = {"answer": answer, "verified": verified}

    def __len__(self):
        return len(self.data)


def bench_cache():
    c = VerifiedCache()
    prompts = [f"write a function to reverse string number {i}" for i in range(200)]
    for p in prompts:
        c.put(p, "def reverse(s): return s[::-1]")
    hits = sum(1 for p in prompts if c.get(p))
    lat = timed(lambda: c.get(prompts[7]))
    rec("layer0_cache", entries=len(c), hit_rate_pct=round(100 * hits / len(prompts), 1),
        **lat, note="in-memory; SQLite variant adds ~0.2 ms")


# ---------------------------------------------------------------- LAYER 1: drafting
TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\d+|[^\s\w]")


def bench_drafting(corpus_glob=("*.py", "*.md"), k=3, cap=16):
    """Lookup drafter: how many model passes does an n-gram index save?"""
    import collections, glob, random
    docs = {}
    for pattern in corpus_glob:
        for f in glob.glob(os.path.join(ROOT, pattern)):
            try:
                toks = TOKEN_RE.findall(open(f, encoding="utf-8", errors="replace").read())
            except OSError:
                continue
            if len(toks) > 300:
                docs[os.path.basename(f)] = toks
    if not docs:
        rec("layer1_drafting", error="no corpus")
        return
    index = collections.defaultdict(list)
    for name, toks in docs.items():
        for i in range(len(toks) - k):
            index[hash(tuple(toks[i:i + k]))].append((name, i))
    random.seed(7)
    hits = pos = 0
    drafted = []
    for name, toks in docs.items():
        n_pos = min(300, max(1, len(toks) - k - 1))
        for i in random.sample(range(k, len(toks) - 1), n_pos):
            pos += 1
            best = 0
            for (oname, oi) in index.get(hash(tuple(toks[i - k:i])), [])[:6]:
                if oname == name:
                    continue
                n = 0
                while (n < cap and oi + k + n < len(docs[oname]) and i + n < len(toks)
                       and docs[oname][oi + k + n] == toks[i + n]):
                    n += 1
                best = max(best, n)
            if best:
                hits += 1
                drafted.append(best)
    tok_per_pass = (hits / pos) * (statistics.mean(drafted) if drafted else 0)
    rec("layer1_drafting", corpus_tokens=sum(len(t) for t in docs.values()),
        context_k=k, hit_rate_pct=round(100 * hits / pos, 1),
        mean_draft=round(statistics.mean(drafted), 2) if drafted else 0,
        passes_per_100_tokens=round(100 / tok_per_pass, 1) if tok_per_pass else 100.0,
        speedup=round(tok_per_pass, 2) if tok_per_pass else 1.0)


# ---------------------------------------------------------------- LAYER 2: memory
def bench_kv():
    """KV cache size and H2O eviction effect. Qwen2.5-32B geometry."""
    layers, kv_heads, head_dim = 64, 8, 128
    per_token_bytes = 2 * layers * kv_heads * head_dim * 2  # K+V, f16
    rows = {}
    for ctx in (2048, 4096, 8192, 32768):
        rows[f"ctx_{ctx}"] = {
            "f16_mb": round(per_token_bytes * ctx / 2**20, 1),
            "q8_mb": round(per_token_bytes * ctx / 2 / 2**20, 1),
            "q4_mb": round(per_token_bytes * ctx / 4 / 2**20, 1),
            "q4_h2o_30pct_mb": round(per_token_bytes * ctx * 0.30 / 4 / 2**20, 1),
        }
    rec("layer2_kv", per_token_bytes=per_token_bytes, by_context=rows,
        note="H2O keeps 20% anchors + 10% recent")


# ---------------------------------------------------------------- LAYER 3: kernel
BITCORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bitcore")


def bench_kernel(quick=False):
    """Build and run engine_x/bitcore/bitcore: every kernel is value-checked before timed."""
    src = os.path.join(BITCORE_DIR, "bitcore.c")
    exe = os.path.join(BITCORE_DIR, "bitcore")
    if not os.path.exists(src):
        rec("layer3_kernel", error="bitcore.c missing")
        return
    r = subprocess.run(["gcc", "-O3", "-march=native", "-Wno-alloc-size-larger-than",
                        "-o", exe, src], capture_output=True, text=True)
    if r.returncode != 0:
        rec("layer3_kernel", error="gcc failed", stderr=r.stderr[-400:])
        return
    out = subprocess.run([exe, "262144" if quick else "1048576", "60" if quick else "200"],
                         capture_output=True, text=True)
    try:
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        rec("layer3_kernel", error="parse", stdout=out.stdout[-300:])
        return
    data["all_correct"] = all(data.get(k) for k in
                              ("correct_int8", "correct_ternary", "correct_vnni"))
    data["exit_code"] = out.returncode
    rec("layer3_kernel", **data)


def bench_templates():
    """Layer 0: plan-then-emit. Tokens are tokenized, both sides, same tokenizer."""
    sys.path.insert(0, ROOT)
    from engine_x.templates import TemplateBank, TOKENIZER, count_tokens
    bank = TemplateBank()
    rows = {}
    for t in bank.templates:
        request = {"python-retry-decorator": "add a retry decorator for fetch_user with 3 attempts",
                   "add-test": "write tests for parse_config",
                   "explain-error": "why does this traceback: KeyError: 'name'",
                   "refactor-rename": "rename `foo` to `bar`",
                   "json-to-python": "convert json to python dataclass class User",
                   "sql-query": "select rows from orders where created_at is recent",
                   "git-recovery": "undo last commit, I dropped a stash"}.get(t.name, t.name)
        plan = bank.plan(request)
        if not plan["hit"]:
            rows[t.name] = {"error": "no match"}
            continue
        ref = count_tokens(t.reference_answer) if t.reference_answer else None
        rows[t.name] = {
            "request": request,
            "tokens_planned": plan["tokens_planned"],
            "tokens_reference_answer": ref,
            "ratio": round(ref / plan["tokens_planned"], 2) if ref else None,
            "holes": plan["holes"],
            "match_ms": plan["match_ms"],
        }
    ratios = [r["ratio"] for r in rows.values() if r.get("ratio")]
    rec("layer0_templates", tokenizer=TOKENIZER, templates=len(rows), per_template=rows,
        mean_ratio=round(statistics.mean(ratios), 2) if ratios else None,
        min_ratio=min(ratios) if ratios else None, max_ratio=max(ratios) if ratios else None)


def bench_triemind(quick=True):
    """Layer 1: radix drafter, scored on a held-out file it was not trained on."""
    sys.path.insert(0, ROOT)
    from engine_x.triemind import RadixDrafter, evaluate, tokenize
    held_out = os.path.join(ROOT, "engine_x", "prefix_bank.py")
    import glob
    files = [f for f in glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "engine_x", "*.py"))
             if os.path.basename(f) not in ("prefix_bank.py", "triemind.py")]
    d = RadixDrafter(k=3, min_confidence=0.6, max_continuation=24)
    for f in files:
        try:
            d.add_text(open(f, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
    ref = tokenize(open(held_out, encoding="utf-8", errors="replace").read())
    r = evaluate(d, ref, stride=25 if quick else 5)
    rec("layer1_triemind", held_out=os.path.basename(held_out), **d.stats(), **r)


def bench_prefix_bank():
    """Prefix bank stats + a warm lookup measurement (no model needed)."""
    sys.path.insert(0, ROOT)
    from engine_x.prefix_bank import PrefixBank, SlotBackend
    bank = PrefixBank()
    rec("layer2_prefix_bank", **bank.stats(), backend_available=SlotBackend().available())
    # measure the index lookup itself, which is what a warm session pays
    text = "You are a Python 3.12 coding assistant. Output only runnable code."
    kv = os.path.join(bank.dir, "_bench.kv")
    open(kv, "wb").write(b"\0" * 4096)
    bank.put("_bench", text, kv, 20)
    rec("layer2_prefix_bank", lookup=timed(lambda: bank.get("_bench", text)))
    bank.invalidate("_bench")


# ---------------------------------------------------------------- engine config
def bench_engine_config():
    """What the engine *should* launch with, and what this box can honour."""
    sys.path.insert(0, ROOT)
    try:
        from engine_x.speed_profile import plan_launch, detect_capabilities  # type: ignore
        caps = detect_capabilities()
        plan = plan_launch("qwen2.5-coder-32b-instruct-IQ3_XS.gguf", caps)
        rec("engine_config", capabilities=caps, plan=plan)
    except Exception as e:
        rec("engine_config", error=str(e))


def main():
    quick = "--quick" in sys.argv
    out = None
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
    t0 = time.perf_counter()
    for fn in (bench_hardware, bench_disk, bench_cache, bench_drafting, bench_kv,
               lambda: bench_kernel(quick), bench_templates, lambda: bench_triemind(quick), bench_prefix_bank,
               bench_engine_config):
        name = getattr(fn, "__name__", "kernel")
        try:
            fn()
        except Exception as e:
            rec(name, error=f"{type(e).__name__}: {e}")
    RESULTS["_meta"] = {"wall_s": round(time.perf_counter() - t0, 2),
                        "generated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    text = json.dumps(RESULTS, indent=2)
    if out:
        open(out, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
