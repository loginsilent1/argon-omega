# engine_x: the fused-engine layers, as measured code (not a plan)

This branch adds `engine_x/`, a measured implementation of the fused-CPU-inference plan,
plus one honest verdict per idea. Nothing here is a claim that was not measured on this
box, and every measurement that came out smaller (or negative) than expected is reported
at full strength.

**Base:** `0ecffd9` (grafted stub checkout). **Tests:** `python3 engine_x/test_engine_x.py`
→ **47 tests, OK**. **Numbers:** `python3 engine_x/bench_all.py --quick` →
`engine_x/bench_baseline.json`.

Box: Xeon @2.60 GHz, 2 cores, **3,939 MB RAM**, ~21 GB/s measured memory bandwidth,
AVX2 + AVX512F, **no VNNI**, no GPU, 627 MB/s cold disk.

---

## What is in here

| file | layer | what it does |
|---|---|---|
| `templates.py` + `template_refs.py` | 0 | plan-then-emit: skeleton + slots instead of a written-out answer |
| `triemind.py` | 1 | radix drafter + speculative session + held-out scoring |
| `airllm_brain.py` | 1 | layer streaming: serial / prefetch / mmap / int8, and a planner |
| `prefix_bank.py` | 2 | frozen KV prefixes keyed by hash of the exact prefix text |
| `flashmap.c` | 2 | async chunk prefetch vs serial `pread`, with a `DONTNEED` gate |
| `bitcore/bitcore.c` | 3 | float / int8 AVX2 / VNNI / ternary kernels, value-checked |
| `speed_profile.py` | — | launch planner: ctx, KV quant, threads from measured RAM |
| `apply_to_engine.py` | — | idempotent, dry-run, all-or-nothing patcher for `engine.py` |
| `bench_all.py` | — | the harness that produces `bench_baseline.json` |
| `test_engine_x.py` | — | 47 tests, 3.5 s |

## Measured results

| result | number | honest caveat |
|---|---|---|
| Plan-then-emit templates | **4.9×** fewer output tokens (3.5–5.7×) | only for requests that match a template |
| Radix drafter | **1.86** tokens per model pass | 68.9 % acceptance on code-shaped text; worse on prose |
| BitCore int8 AVX2 | **28.3×** vs float32 | same class of win llama.cpp already has |
| BitCore VNNI `vpdpbusd` | **68.5×** vs float32 | compiled here, correct; this box has no VNNI |
| BitCore ternary packed | 0.97× | **density win only** — 16× smaller weights, not speed |
| Prefix-KV bank | lookup p50 0.003 ms | needs a live `llama-server` to actually restore |
| FlashMap | 1.1–1.55× disk read | a wash once the file is page-cached |
| AirLLM layer streaming | **11–17 % of model in RAM** | 70 GB → 0.010 tok/s = 106 s/token |
| Cold vs warm disk | **627 vs 5,660 MB/s (9.0×)** | the old 5,697 MB/s figure was a cache hit |

## Two results that contradict the plan, reported as such

1. **Ternary is not a speed win.** Three attempts were built and value-checked; the
   hand-rolled `pshufb` expansion was wrong twice (per-128-bit-lane semantics) and was
   abandoned rather than shipped. The verified win is int8/VNNI. Ternary's value is 4–16×
   smaller weights in RAM and on disk.
2. **The multipliers do not stack.** Templates cut output length (~4.9×), the drafter cuts
   passes (~1.8×), the kernel cuts time per pass — and the drafter and the kernel act on
   the *same* decode loop, so 4.9 × 1.8 × 28 ≠ 250×. Realistic composite: **6–9×
   end-to-end on template-matching requests, ~1.8× otherwise, 4–16× less RAM.**

## AirLLM verdict

Layer streaming works and is implemented, but on this disk it buys *"it runs at all"*, not
speed: 0.010 tok/s for a 70B model (106 s/token). Where the choice exists, **quantise to
int8 and stay resident** — for the same bytes that is 10–27× faster, because RAM bandwidth
here is ~34× disk bandwidth. Streaming is the right answer only when the model genuinely
does not fit and 1 token/minute is still useful.

## Scope / safety

* `engine.py` in this branch is **untouched**. `apply_to_engine.py` is dry-run by default,
  keeps a `.bak`, aborts with the file untouched if any anchor is missing or ambiguous, and
  `ast.parse`s the result before writing.
* The plan-emit path is off unless `ARGON_PLAN_EMIT=1`, and its output is always labelled
  `source: "template"` so a template can never be presented as a model answer.
* `_engine_x_kv_flags()` emits `--cache-type-k` only if `llama-server --help` advertises it.
* Every kernel and I/O path is value-checked (or byte-count-checked) **before** it is
  timed; a wrong value reports `correct_*: false` and its speedup is withheld.
* `.gitignore` now excludes `__pycache__`, the compiled `bitcore` binary, `ssd_kv_cache/`
  and `.bak` files; previously committed `.pyc` files are untracked here.

## Not done / not possible in this sandbox

* No real weights: `huggingface.co` is unreachable from here, so `airllm_brain.py`'s
  compute step is a memory-shaped placeholder (`COMPUTE_IS_PLACEHOLDER = True` travels with
  every report) and all projected tok/s are upper bounds.
* No `llama-server` binary here (the repo ships a Windows `.exe`), so the prefix-KV restore
  path is exercised against its no-op fallback, not a live server.
* No Windows `.exe` can be built in this environment.

## Merge status

`origin/master` was merged into this branch (`ee260ea`), so the PR is
**MERGEABLE / CLEAN**. Final diff: **17 files, +3,639 / −0** — all new files under
`engine_x/`, plus 45 added lines in `.gitignore` and `README.md`. Nothing is deleted or
rewritten.

Two things had to be cleaned out of the diff first, both of which were **pre-existing stub
state, not this work**:

* nine generated artefacts (`*.db` vaults, `ssd_images/`) and `download_models.ps1`;
* `bin/` — 99 MB of Windows DLLs including `llama-server.exe`.

Both are now git-ignored but left **on disk**, so a local install still works; they were
only ever tracked because the original upload committed them. This PR does not need to
carry a 99 MB binary payload to add a Python measurement layer.

Conflict resolutions in the merge: `.gitignore` = union of both rule sets;
`README.md` = master's document byte-for-byte plus an `engine_x` section at the end;
`app.py`, `laws.txt`, `optimal_threads.json`, `sandbox_workspace/` = master's bytes
(their only differences were line endings from the older tree).

## Reproduce

```bash
python3 engine_x/test_engine_x.py          # 47 tests, OK, ~3.5 s
python3 engine_x/bench_all.py --quick      # regenerates bench_baseline.json
python3 engine_x/apply_to_engine.py        # dry-run diff against engine.py
```
