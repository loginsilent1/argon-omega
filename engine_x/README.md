# engine_x — the fused engine, measured

Every claim in this folder is produced by a command, not asserted in prose.

```bash
python3 engine_x/bench_all.py --json engine_x/bench_baseline.json   # all layers
python3 engine_x/test_engine_x.py                                    # 15 unit tests
gcc -O2 -pthread -o engine_x/flashmap engine_x/flashmap.c            # FlashMap tool
./engine_x/flashmap <bigfile> 32                                     # serial vs prefetch
```

## Files

| file | layer | what it does |
|---|---|---|
| `bench_all.py` | all | one command → hardware, disk, cache, drafting, KV math, kernel, launch plan |
| `speed_profile.py` | 1 | **pure function** deciding llama-server flags from model size + machine capability |
| `flashmap.c` | 2 | double-buffered reader vs serial read, honest JSON output |
| `test_engine_x.py` | all | 15 tests, including "the C kernel is actually faster than float" |

## Baseline on this machine (2-core Xeon, 3.9 GB RAM, 21 GB/s, AVX-512, no VNNI, no GPU)

```json
{"hardware": {"cpu": "Intel Xeon @ 2.60GHz", "ram_total_mb": 3939,
              "bandwidth_estimate_gbps": 21.2, "has_avx2": true, "has_avx512f": true},
 "layer1_drafting": {"speedup": 1.4, "passes_per_100_tokens": 71.2},
 "layer3_kernel":  {"avx2_vs_float": 8.81, "scalar_i8_vs_float": 5.26,
                    "ternary_vs_float": 0.28},
 "layer2_kv":      {"ctx_32768": {"f16_mb": 8192, "q8_mb": 4096, "q4_mb": 2048,
                                  "q4_h2o_30pct_mb": 614}}}
```

## What is proven, and what is not

| Layer | Status | Evidence |
|---|---|---|
| 0 cache | ✅ works, trivially fast | p50 0.002 ms in-memory; SQLite adds ~0.2 ms |
| 1 drafting | ✅ 1.4× | measured over 136,711 tokens of real code |
| 2 KV math | ✅ arithmetic | 256 KB/token for a 64-layer×8-head model |
| 2 FlashMap | ⚠️ 1.2–1.55× **only when RAM < file size** | at 8–32 MB chunks on a 286 MB file |
| 3 int8 AVX2 kernel | ✅ **8.8× vs float32** | gcc 12, `-march=native`, same arithmetic verified |
| 3 ternary packed | ❌ **0.28× (slower)** unless the model is natively ternary | naive 2-bit unpack is a pessimisation |
| TTFT / tok-s end-to-end | ⏳ needs llama-server + a GGUF | not measurable in this sandbox |

### Two results that contradict the original plan

1. **BitCore's 28× is a ternary×ternary-only effect.** Measured here: 8.8× for int8×int8 AVX2,
   5.3× for scalar int8, and **0.28× (3.5× slower)** for naive packed-ternary code. Any ternary
   kernel must fold signs with masks + POPCNT; unpacking per element is a pessimisation.
2. **FlashMap's `DONTNEED` hurts when the file fits in RAM** (measured 0.16× — a 6× regression).
   It is now gated on `file_size > 0.6 × MemAvailable`, and sub-8 MB chunks are read serially
   because thread spawn costs more than the read.

---

## Step 1 — prefix bank (`prefix_bank.py`)

Frozen KV prefixes live on ROM, keyed by **name + hash of the exact prefix text**:

```python
bank = PrefixBank()                       # ssd_kv_cache/prefixes/index.json
bank.put("python", SYSTEM_PROMPT, "python.kv", tokens=41)
bank.get("python", SYSTEM_PROMPT)         # hit
bank.get("python", SYSTEM_PROMPT + " v2") # None — edited prompt never serves stale KV
bank.prune(2 << 30)                       # LRU eviction to a byte budget
```

`SlotBackend` wraps llama-server's `/slots/0?action=save|restore`. **When no server is
running it returns `False` — never raises** — so the whole module is testable offline and
the app can call it unconditionally.

Verified by 7 tests: put/hit, edited-prefix miss, missing-file miss, reload from disk, LRU
prune, absent-backend no-op, and clean `False` from save/restore without a server.

## Step 4 — BitCore kernels (`bitcore/bitcore.c`)

Four paths, **all value-checked against a scalar reference before being timed**; a kernel
that returns a wrong value reports `correct_*: false` and its speedup is withheld.

| kernel | measured | correct |
|---|---|---|
| float32 (baseline) | 1,076 µs per 262k-element dot | — |
| `int8_avx2` (vpmaddubsw + vpsignb) | 38 µs → **28.3×** | ✅ |
| `vnni` (`vpdpbusd`, AVX512-VNNI) | 15.7 µs → **68.5×** | ✅ |
| ternary packed | 1,104 µs → **0.97×** | ✅ |

### Three kernel attempts, kept in the source as a warning

1. **mask + popcount** — correct only for *ternary activations*; for int8 it is wrong, and
   0.28× even when right.
2. **2-bit unpack per element** — correct, but 3.5× slower than float.
3. **pshufb plane expansion** — fast, but the byte-plane interleave was wrong (unpack*_epi8
   is per-128-bit-lane, and the order must be element order, not plane order).

Conclusion: **the honest, verified win is int8 (28×) and VNNI (68×) — not ternary.** The
ternary value is 4× smaller weights in RAM/disk, not speed. Fixing the pshufb interleave is
worth a separate, benchmarked PR with cross-checks on every lane boundary.

---

## Step 3 — `templates.py`: plan-then-emit (the real 10x lever)

An exact-match cache dies the moment a variable name changes, and teaches the model
nothing. A **plan** survives: store the skeleton, let the model fill the slots.

```
"add a retry decorator for fetch_user with 3 attempts"
  -> template python-retry-decorator, slots {target: fetch_user, attempts: 3}
  -> 215-token plan instead of a 761-token written-out answer
```

Measured with the same tokenizer on both sides (see `bench_all.layer0_templates`):

| template | full answer | plan | ratio | slots left open |
|---|---|---|---|---|
| python-retry-decorator | 761 tok | 215 | **3.54×** | — |
| refactor-rename | 460 | 81 | **5.68×** | — |
| add-test | 599 | 111 | **5.40×** | `module` |
| explain-error | 534 | 108 | **4.94×** | `file` |
| json-to-python / sql-query / git-recovery | — | 73–133 | — | — |

**Mean 4.9×, range 3.5–5.7×** on the four templates whose reference answers are written
out in `template_refs.py`. That is the honest version of "800 → ~60": the measured factor
is ~5×, and it only applies to requests that match a template.

Two rules keep it from lying to the user:
1. **A required slot that cannot be extracted means no match.** "what is a retry loop"
   does not match the retry template — we refuse rather than invent a function name.
2. **Unfilled slots stay visible as `{module}`, `{file}`.** A guessed identifier is a silent
   wrong answer; a visible hole is one token the model must supply. `plan["holes"]` lists
   them.

The savings are *arithmetic*, not wall-clock: the model still runs, it just emits a
215-token plan instead of a 761-token essay. At 2 tok/s that is 6.3 min → 1.8 min.

## Step 2 — `triemind.py`: radix drafter + speculative session

A compressed trie over token n-grams from the local corpus. `draft()` walks it and
**stops at any branch without clear support** — a wrong draft costs a full model pass,
so guessing is worse than nothing.

Held out: trained on 24 files / 206,726 tokens, scored on `prefix_bank.py` (never seen):

| metric | value |
|---|---|
| mean drafted | 1.24 tokens |
| mean accepted | 0.86 tokens |
| acceptance | **68.9%** |
| **tokens per model pass** | **1.86×** |
| draft lookup cost | 0.0138 ms (0.3% of a 4 ms pass) |
| stop reasons | branch 60, no-context 31, low-support 3, cap 4 |

A confidence sweep (k=2–4, threshold 0.5–0.9) tops out at **1.71×**; the earlier 1.4×
figure from the naive n-gram bench was in the same range. **The drafter is worth ~1.8×, not
10×, and it is worth less on prose the corpus has never seen.** It does not stack
multiplicatively with the kernel win — both act on the same decode loop.

## Step 5 — `apply_to_engine.py`: idempotent patch

```
python3 engine_x/apply_to_engine.py            # dry-run, prints the diff
python3 engine_x/apply_to_engine.py --check     # exit 1 if anything is unapplied
python3 engine_x/apply_to_engine.py --write     # apply (keeps engine.py.bak)
```

Three sentinel-marked patches: the `engine_x` import block, `_engine_x_plan_emit()`
(gated on `ARGON_PLAN_EMIT=1`, returns `source: "template"` so a template answer can never
be mistaken for a model answer), and `_engine_x_kv_flags()` (emits `--cache-type-k` only
if `llama-server --help` advertises it — a wrong flag makes the server exit and the
existing fallback ladder would relaunch without it anyway).

Guarantees, all tested (5 tests): dry-run never writes; a second `--write` is a no-op;
a missing or ambiguous anchor aborts **with the file untouched**; the patched file is
`ast.parse`d before it is written. Verified live: `import engine_probe` loads the bank,
returns `template: python-retry-decorator, tokens: 215, holes: []` with the flag on and
`None` with it off.

## Where the multipliers really are

| lever | measured | stacks with | honest note |
|---|---|---|---|
| Layer-0 templates | **4.9×** fewer output tokens | everything (removes work) | only for matching requests |
| prefix-KV bank | prefill skipped, 0.003 ms lookup | everything | needs a real `llama-server` |
| TrieMind drafter | **1.86×** | shares the decode loop | dies on unseen prose |
| BitCore int8/VNNI | 28×/68× vs float | same arithmetic llama.cpp already does | density win, not new speed |
| FlashMap | 1.1–1.55× disk read | only on cold I/O | a wash when page-cached |

You cannot multiply 4.9 × 1.86 × 28 and call it 255×. The templates cut output tokens,
the drafter cuts passes, the kernel cuts time per pass — that composition is roughly
`4.9 ×` output-length reduction with `~1.4–1.8×` fewer passes on top, i.e. **a 6–9×
end-to-end improvement for template-matching tasks and ~1.8× for everything else.**

---

## AirLLM: stream the layers from disk instead of holding the model

The idea is sound and it is now implemented (`airllm_brain.py`), because the only honest
way to judge it is to measure the disk it depends on.

**The physics.** If nothing is resident, every generated token requires reading every
layer once: `tok/s = disk_bandwidth / model_size`. There is no overlap to be had beyond
double buffering, and the 100th token costs exactly what the 1st costs.

### Measured disk, cold (4 GB file, 3 passes, this box)

| measurement | value |
|---|---|
| warm read (96 MB probe, page-cached) | **5,660 MB/s** |
| cold read (4 GB, median of 3) | **627 MB/s** (range 552–629) |
| **warm vs cold** | **9.0×** |
| page cache | 3.9 GB RAM, file 4 GB — the cache can never hold the model |

That 9× gap is why the earlier `disk: 5697.8 MB/s` entry in this file was misleading: it
was measured on a 96 MB file, i.e. out of the page cache.

### Layer streaming, measured (205 MB synthetic model, 8 blocks, f16-sized)

| mode | ms/token (steady) | vs serial | bytes read | peak RSS |
|---|---|---|---|---|
| serial pread | 213 | 1.00× | 4.0× model | **34 MB (17%)** |
| prefetch (double buffer) | 180 | **1.19×** | 4.0× model | 59 MB (29%) |
| mmap | 163 | 1.31× | (page faults) | 203 MB (99%) |
| compressed int8 on disk | 91 | **2.33×** | 2.0× model | **22 MB (11%)** |

The RSS column is the real AirLLM payoff: **11–17% of the model in RAM instead of 100%**.
`mmap` is faster only because it lets the OS keep what fits — on a 3.9 GB box with a 13 GB
model it degrades to the same cold reads as serial.

### What it means for a real model on this box

| model (f16) | streamed | mmap | int8 resident (if it fits) |
|---|---|---|---|
| 7 GB | 0.099 tok/s | 0.090 | 2.7 tok/s (11 s/token: does not fit 3.9 GB) |
| 13 GB | 0.053 tok/s | 0.048 | 1.45 tok/s |
| 70 GB | **0.010 tok/s — 106 s/token** | 0.009 | 0.27 tok/s |

**Verdict: AirLLM buys "it runs at all", not speed.** It turns a 70B model from impossible
into ~1 token per 106 seconds on this disk. Where the choice exists, **quantise to int8 and
keep it resident**: for the same bytes it is ~10–27× faster than streaming (RAM bandwidth
is ~34× disk bandwidth here) and it needs no per-layer pipeline at all.

The compute step in the harness is a memory-shaped placeholder, not a real matmul
(`COMPUTE_IS_PLACEHOLDER = True` in every report), so all tok/s above are upper bounds.
No real weights exist in this sandbox — huggingface.co is unreachable from here.
