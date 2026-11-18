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
