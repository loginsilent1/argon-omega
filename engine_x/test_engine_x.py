#!/usr/bin/env python3
"""Tests for engine_x. Run: python3 engine_x/test_engine_x.py"""
import json, os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine_x.speed_profile import (PROFILES, detect_capabilities, kv_mb, model_size_mb,
                                    plan_launch, size_class)
from engine_x.bench_all import VerifiedCache, TOKEN_RE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSizeClass(unittest.TestCase):
    def test_name_inference(self):
        self.assertAlmostEqual(model_size_mb("qwen2.5-coder-1.5b-instruct-q4_k_m.gguf") / 937, 1, delta=0.1)
        self.assertGreater(model_size_mb("x-7b-instruct-q4_k_m.gguf"), 4000)
        self.assertGreater(model_size_mb("x-32b-instruct-IQ3_XS.gguf"), 13000)
        self.assertEqual(model_size_mb("no-size-here.gguf"), 0.0)

    def test_classes(self):
        self.assertEqual(size_class(900), "small")
        self.assertEqual(size_class(4700), "medium")
        self.assertEqual(size_class(13700), "large")
        self.assertEqual(size_class(0), "unknown")


class TestLaunchPlan(unittest.TestCase):
    def test_large_model_never_gets_a_huge_context(self):
        caps = {"ram_mb": 32000, "avx2": True, "flash_attn_flag": True,
                "cache_type_flag": True}
        for profile in PROFILES:
            p = plan_launch("m-32b-iq3_xs.gguf", caps, profile=profile)
            self.assertLessEqual(p["context"], 4096, f"{profile} ctx too big for a 13 GB model")
            self.assertLess(kv_mb(p["context"], p["kv_cache"]), 1100)

    def test_unknown_build_degrades_gracefully(self):
        caps = {"ram_mb": 8000, "flash_attn_flag": False, "cache_type_flag": False}
        p = plan_launch("m-7b-q4_k_m.gguf", caps, profile="fast")
        self.assertEqual(p["kv_cache"], "f16")
        self.assertNotIn("--cache-type-k", p["argv"])
        self.assertNotIn("--flash-attn", p["argv"])

    def test_kv_flags_only_with_support(self):
        caps = {"ram_mb": 16000, "flash_attn_flag": True, "cache_type_flag": True}
        p = plan_launch("m-7b-q4_k_m.gguf", caps, profile="fast")
        self.assertEqual(p["kv_cache"], "q8_0")
        self.assertIn("--cache-type-k", p["argv"])
        self.assertIn("--flash-attn", p["argv"])

    def test_low_ram_shrinks_context(self):
        caps = {"ram_mb": 3000, "flash_attn_flag": False, "cache_type_flag": False}
        p = plan_launch("m-7b-q4_k_m.gguf", caps, profile="quality")
        self.assertEqual(p["context"], 2048)

    def test_mmap_decision(self):
        big = {"ram_mb": 4000, "flash_attn_flag": False, "cache_type_flag": False}
        self.assertTrue(plan_launch("m-32b-iq3_xs.gguf", big)["mmap"])   # cannot fit
        roomy = {"ram_mb": 48000, "flash_attn_flag": False, "cache_type_flag": False}
        self.assertFalse(plan_launch("m-7b-q4_k_m.gguf", roomy)["mmap"])  # fits: keep resident

    def test_trimmer_never_runs_on_big_models(self):
        caps = {"ram_mb": 48000, "flash_attn_flag": False, "cache_type_flag": False}
        self.assertFalse(plan_launch("m-32b-iq3_xs.gguf", caps)["trim_working_set"])
        self.assertTrue(plan_launch("m-1.5b-q4_k_m.gguf", caps)["trim_working_set"])

    def test_threads_are_physical_not_logical(self):
        caps = {"ram_mb": 16000, "flash_attn_flag": False, "cache_type_flag": False}
        p = plan_launch("m-7b-q4_k_m.gguf", caps, threads=4)
        self.assertEqual(p["threads"], 4)
        self.assertIn("-t", p["argv"])

    def test_argv_is_wellformed(self):
        caps = {"ram_mb": 16000, "flash_attn_flag": True, "cache_type_flag": True}
        p = plan_launch("m-7b-q4_k_m.gguf", caps)
        argv = p["argv"]
        self.assertIn("-c", argv)
        self.assertEqual(argv[argv.index("-c") + 1], str(p["context"]))
        self.assertTrue(all(isinstance(a, str) for a in argv))

    def test_kv_math_matches_arithmetic(self):
        # 64 layers, 8 KV heads, 128 dim -> 256 KB/token at f16
        self.assertAlmostEqual(kv_mb(4096, "f16"), 1024.0, delta=1)
        self.assertAlmostEqual(kv_mb(4096, "q8_0"), 512.0, delta=1)
        self.assertAlmostEqual(kv_mb(4096, "q4_0"), 256.0, delta=1)


class TestCache(unittest.TestCase):
    def test_exact_and_normalised(self):
        c = VerifiedCache()
        c.put("Write  a  Function", "code")
        self.assertIsNotNone(c.get("write a function"))       # normalised hit
        self.assertIsNone(c.get("write a different function"))
        self.assertEqual(len(c), 1)

    def test_language_is_part_of_the_key(self):
        c = VerifiedCache()
        c.put("reverse a string", "py", lang="python")
        self.assertIsNone(c.get("reverse a string", lang="js"))
        self.assertIsNotNone(c.get("reverse a string", lang="python"))


class TestKernel(unittest.TestCase):
    def test_kernel_measures_and_is_faster_than_float(self):
        from engine_x.bench_all import bench_kernel, RESULTS
        bench_kernel(quick=True)
        k = RESULTS["layer3_kernel"]
        self.assertNotIn("error", k)
        self.assertGreater(k["avx2_vs_float"], 2.0, "AVX2 int8 path should beat float32")
        self.assertEqual(k["bytes_int8"] * 4, k["bytes_float32"])


class TestFlashmap(unittest.TestCase):
    def test_flashmap_beats_serial_io(self):
        src = os.path.join(ROOT, "engine_x", "flashmap.c")
        exe = os.path.join(tempfile.gettempdir(), "flashmap_test")
        r = subprocess.run(["gcc", "-O2", "-pthread", "-o", exe, src],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        blob = os.path.join(tempfile.gettempdir(), "flashmap_blob.bin")
        with open(blob, "wb") as f:
            f.write(os.urandom(160 << 20))
        # 32 MB chunks: at 8 MB the thread spawn/join costs more than the read
        # (the tool reports that honestly as mode=serial-small-chunk).
        out = subprocess.run([exe, blob, "32"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        data = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertGreater(data["speedup"], 1.0, f"async IO regressed: {data}")
        self.assertGreater(data["async_mbs"], 100)
        os.remove(blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
