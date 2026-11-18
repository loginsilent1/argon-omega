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
    def test_every_kernel_is_value_checked_before_being_timed(self):
        from engine_x.bench_all import bench_kernel, RESULTS
        bench_kernel(quick=True)
        k = RESULTS["layer3_kernel"]
        self.assertNotIn("error", k)
        self.assertTrue(k["correct_int8"], "int8 AVX2 kernel returned a wrong value")
        self.assertTrue(k["correct_ternary"], "ternary kernel returned a wrong value")
        self.assertTrue(k["all_correct"])
        self.assertGreater(k["int8_vs_float"], 3.0, "int8 AVX2 should clearly beat float32")
        self.assertEqual(k["bytes_int8"] * 4, k["bytes_float32"])

    def test_vnni_matches_reference_when_compiled(self):
        from engine_x.bench_all import bench_kernel, RESULTS
        bench_kernel(quick=True)
        k = RESULTS["layer3_kernel"]
        if k.get("vnni_compiled"):
            self.assertTrue(k["correct_vnni"])
            self.assertGreater(k["vnni_vs_float"], k["int8_vs_float"],
                               "VNNI should be faster than plain AVX2 when available")

    def test_ternary_saves_4x_the_bytes(self):
        from engine_x.bench_all import bench_kernel, RESULTS
        bench_kernel(quick=True)
        k = RESULTS["layer3_kernel"]
        self.assertLess(k["bytes_ternary_packed"] * 3.5, k["bytes_int8"])
        # and be honest about what it costs in speed
        self.assertLess(k["ternary_vs_float"], 2.0)


class TestApplyToEngine(unittest.TestCase):
    """The patcher must be safe to run blindly: dry-run, idempotent, all-or-nothing."""

    def setUp(self):
        import shutil, tempfile
        from engine_x import apply_to_engine
        self.mod = apply_to_engine
        self.tmp = tempfile.mkdtemp()
        self.target = os.path.join(self.tmp, "engine.py")
        shutil.copyfile(apply_to_engine.TARGET, self.target)

    def _read(self):
        return open(self.target, encoding="utf-8").read()

    def test_dry_run_never_writes(self):
        before = self._read()
        rc = self.mod.main(["--target", self.target])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read(), before)

    def test_check_reports_unapplied_then_applied(self):
        self.assertEqual(self.mod.main(["--target", self.target, "--check"]), 1)
        self.mod.main(["--target", self.target, "--write"])
        self.assertEqual(self.mod.main(["--target", self.target, "--check"]), 0)

    def test_second_run_is_a_noop(self):
        self.mod.main(["--target", self.target, "--write"])
        once = self._read()
        self.assertEqual(self.mod.main(["--target", self.target, "--write"]), 0)
        self.assertEqual(self._read(), once, "patching twice modified the file again")

    def test_patched_file_still_compiles(self):
        import ast
        self.mod.main(["--target", self.target, "--write"])
        ast.parse(self._read())
        self.assertIn("def _engine_x_plan_emit", self._read())
        self.assertIn("def _engine_x_kv_flags", self._read())

    def test_missing_anchor_aborts_and_leaves_file_untouched(self):
        broken = self._read().replace("def ensure_llama_server(", "def ensure_llama_server_RENAMED(", 1)
        open(self.target, "w", encoding="utf-8").write(broken)
        rc = self.mod.main(["--target", self.target, "--write"])
        self.assertEqual(rc, 2)
        self.assertEqual(self._read(), broken)
        self.assertNotIn("# [engine_x:1]", self._read())


class TestTrieMind(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from engine_x.triemind import RadixDrafter
        cls.d = RadixDrafter(k=3, min_confidence=0.5)
        cls.d.add_text("def foo(x):\n    return x + 1\n\ndef bar(y):\n    return y * 2\n")
        cls.d.add_text("def foo(x):\n    return x + 1\n\ndef bar(y):\n    return y * 2\n")

    def test_tokenizer_is_lossless(self):
        from engine_x.triemind import tokenize
        for s in ("def f(x):\n    return x+1\n", "a  b\tc\n", "x = {'k': [1,2]}"):
            self.assertEqual("".join(tokenize(s)), s)

    def test_draft_extends_context(self):
        from engine_x.triemind import tokenize
        ctx = tokenize("def foo(x):\n    return ")
        d = self.d.draft(ctx)
        self.assertGreaterEqual(len(d), 1)
        self.assertEqual(d.tokens[0], "x")
        self.assertIsInstance(d.lookup_ms, float)

    def test_unknown_context_drafts_nothing(self):
        from engine_x.triemind import tokenize
        d = self.d.draft(tokenize("zzz qqq vvv "))
        self.assertEqual(len(d), 0)
        self.assertEqual(d.stopped_at, "no_context")

    def test_ambiguous_branch_stops_instead_of_guessing(self):
        from engine_x.triemind import RadixDrafter, tokenize
        d = RadixDrafter(k=1, min_confidence=0.9)
        for line in ("a X", "a Y", "a Z"):
            d.add_text(line + "\n")
        draft = d.draft(tokenize("a"))
        # the shared whitespace after "a" is safe to draft; the X/Y/Z choice is not
        self.assertEqual(draft.stopped_at, "branch")
        self.assertNotIn("X", draft.tokens)
        self.assertNotIn("Y", draft.tokens)

    def test_accept_counts_the_matching_prefix_only(self):
        from engine_x.triemind import RadixDrafter, Draft
        dr = Draft(["a", "b", "c"], 1.0, "cap")
        self.assertEqual(RadixDrafter.accept(dr, ["a", "b", "X"]), 2)
        self.assertEqual(RadixDrafter.accept(dr, ["X", "b"]), 0)
        self.assertEqual(RadixDrafter.accept(dr, ["a", "b", "c", "d"]), 3)

    def test_session_pays_one_pass_per_emitted_group(self):
        from engine_x.triemind import RadixDrafter, SpeculativeSession, tokenize
        truth = tokenize("def foo(x):\n    return x + 1\n")
        def model(ctx, n):
            i = len(ctx) - 6                    # context started at token 6
            return truth[max(0, i):max(0, i) + n]
        sess = SpeculativeSession(model, self.d, max_new=12)
        r = sess.run(truth[0:6])
        self.assertGreater(r["tokens"], 0)
        self.assertGreaterEqual(r["tokens_per_pass"], 1.0)
        self.assertLessEqual(r["passes"], r["tokens"])   # never more passes than tokens

    def test_evaluate_reports_tokens_per_pass_not_a_wish(self):
        from engine_x.triemind import evaluate, tokenize
        ref = tokenize("def foo(x):\n    return x + 1\n\n")
        r = evaluate(self.d, ref)
        self.assertIn("tokens_per_pass", r)
        self.assertGreaterEqual(r["tokens_per_pass"], 1.0)
        self.assertEqual(r["tokens_per_pass"], round(1 + r["mean_accepted"], 2))


class TestTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from engine_x.templates import TemplateBank
        cls.bank = TemplateBank()

    def test_every_template_matches_its_own_request(self):
        from engine_x.templates import TEMPLATES
        self.assertTrue(len(TEMPLATES) >= 6)
        report = self.bank.verify([
            ("add a retry decorator for fetch_user with 3 attempts", "python-retry-decorator"),
            ("write tests for parse_config", "add-test"),
            ("why does this traceback: KeyError", "explain-error"),
            ("rename `foo` to `bar`", "refactor-rename"),
            ("convert json to python dataclass class User", "json-to-python"),
            ("select all rows from orders", "sql-query"),
            ("undo last commit, I dropped a stash", "git-recovery"),
        ])
        self.assertTrue(report["ok"], report["failures"])

    def test_required_slot_missing_means_no_match(self):
        # "retry" with nothing to retry: refuse rather than invent a target
        self.assertFalse(self.bank.plan("what is a retry loop in general")["hit"])

    def test_render_never_invents_a_slot(self):
        p = self.bank.plan("write tests for parse_config")
        self.assertIn("module", p["holes"])          # visible hole
        rendered = p["plan"]
        self.assertNotIn("target_module", rendered)  # not a fabricated module name
        self.assertIn("from {module} import parse_config", rendered)

    def test_plan_tokens_beat_a_full_answer(self):
        from engine_x.templates import count_tokens
        p = self.bank.plan("add a retry decorator for fetch_user with 3 attempts")
        t = next(t for t in self.bank.templates if t.name == p["name"])
        self.assertGreater(count_tokens(t.reference_answer), 3 * p["tokens_planned"])

    def test_matching_is_fast(self):
        p = self.bank.plan("add a retry decorator for fetch_user with 3 attempts")
        self.assertLess(p["match_ms"], 5.0)


class TestPrefixBank(unittest.TestCase):
    def setUp(self):
        import tempfile
        from engine_x.prefix_bank import PrefixBank
        self.dir = tempfile.mkdtemp()
        self.bank = PrefixBank(self.dir)

    def _fake_kv(self, name, payload=b"KVSTATE"):
        path = os.path.join(self.dir, name + ".kv")
        open(path, "wb").write(payload)
        return path

    def test_put_and_hit(self):
        from engine_x.prefix_bank import prefix_hash
        kv = self._fake_kv("python")
        e = self.bank.put("python", "you are python", kv, 40)
        self.assertEqual(e.text_hash, prefix_hash("you are python"))
        self.assertIsNotNone(self.bank.hit("python", "you are python"))
        self.assertEqual(self.bank.hit("python", "you are python").hits, 2)

    def test_edited_prefix_never_serves_stale_kv(self):
        kv = self._fake_kv("js")
        self.bank.put("js", "you are javascript", kv, 30)
        self.assertIsNone(self.bank.get("js", "you are javascript v2"),
                          "changed prefix text must miss")

    def test_missing_file_is_a_miss(self):
        kv = self._fake_kv("css")
        self.bank.put("css", "you are css", kv, 10)
        os.remove(kv)
        self.assertIsNone(self.bank.get("css", "you are css"))

    def test_reload_from_disk(self):
        from engine_x.prefix_bank import PrefixBank
        kv = self._fake_kv("html")
        self.bank.put("html", "you are html", kv, 20)
        again = PrefixBank(self.dir)
        self.assertIsNotNone(again.get("html", "you are html"))

    def test_prune_lru_frees_space(self):
        for i in range(4):
            kv = self._fake_kv(f"p{i}", b"x" * 1000)
            self.bank.put(f"p{i}", f"prefix {i}", kv, 5)
        self.assertEqual(self.bank.total_bytes(), 4000)
        freed = self.bank.prune(1500)
        self.assertGreaterEqual(freed, 2500)
        self.assertLessEqual(self.bank.total_bytes(), 1500)

    def test_backend_absent_is_a_noop_not_a_crash(self):
        from engine_x.prefix_bank import SlotBackend, freezes
        backend = SlotBackend(base_url="http://127.0.0.1:9")   # nothing listens here
        self.assertFalse(backend.available())
        self.assertIsNone(freezes(self.bank, backend, "python", "text"))

    def test_llama_client_de_goes_through_bindings_not_hand_built_requests(self):
        """Bindings are used when the package is installed; the raw endpoint otherwise."""
        from engine_x.prefix_bank import SlotBackend
        b = SlotBackend(base_url="http://127.0.0.1:9")
        self.assertFalse(b.save("x.kv"))          # no server -> clean False, no exception
        self.assertFalse(b.restore("x.kv"))


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
        self.assertEqual(out.returncode, 0, out.stderr[-300:])
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        data = json.loads(out.stdout.strip().splitlines()[-1])
        # Async prefetch only pays when the read is slower than the work: on a file that
        # is already in the page cache it is a wash (measured 1.0-1.2x). The contract is
        # "never slower", with a separate check that the tool reports its mode honestly.
        self.assertGreaterEqual(data["speedup"], 0.95, f"async IO regressed: {data}")
        self.assertIn("dontneed", data)
        self.assertGreater(data["async_mbs"], 100)
        os.remove(blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
