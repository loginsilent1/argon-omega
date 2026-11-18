"""Self-test for the offline Demo Brain. No dependencies, no network.

    python selftest.py

Checks three things that matter for answer quality:
  1. the routing battery (arithmetic, units, facts, code, knowledge, honesty),
  2. every Python recipe actually executes cleanly in a temp directory,
  3. measured latency and follow-up handling.

Exit code is 0 only when everything passes, so it can gate a release.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

import demo_brain
import benchmark_speed


def _battery() -> tuple[int, list[str]]:
    failures: list[str] = []
    for name, prompt, predicate in benchmark_speed.CHECKS:
        reply = demo_brain.compose(prompt)
        try:
            ok = bool(predicate(reply))
        except Exception as exc:                      # noqa: BLE001 - report, don't crash
            failures.append(f"{name}: predicate raised {exc!r}")
            continue
        if not ok:
            failures.append(f"{name}: wrong answer -> {reply.splitlines()[0][:90]}")
    return len(benchmark_speed.CHECKS) - len(failures), failures


def _recipes() -> tuple[int, list[str]]:
    failures: list[str] = []
    python_recipes = {k: r for k, r in demo_brain._R.items() if r["lang"] == "python"}
    for key, recipe in python_recipes.items():
        with tempfile.TemporaryDirectory() as tmp:
            if key == "csv":                          # this recipe expects an input file
                with open(os.path.join(tmp, "data.csv"), "w", encoding="utf-8") as fh:
                    fh.write("name,score\na,3\nb,9\n")
            proc = subprocess.run(
                [sys.executable, "-c", recipe["code"]],
                cwd=tmp, capture_output=True, text=True, timeout=30,
            )
            # `argparse` intentionally exits 2 when required arguments are missing.
            expected_arg_error = key == "argparse" and proc.returncode == 2
            if proc.returncode != 0 and not expected_arg_error:
                tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
                failures.append(f"{key}: {tail[:110]}")
    return len(python_recipes) - len(failures), failures


def main() -> int:
    if not demo_brain.enabled():
        print("Demo brain disabled via ARGON_DEMO_BRAIN — nothing to test.")
        return 0

    started = time.perf_counter()
    reply = demo_brain.compose("explain deadlock")
    first_ms = (time.perf_counter() - started) * 1000.0

    cached = time.perf_counter()
    demo_brain.compose("explain deadlock")
    cached_ms = (time.perf_counter() - cached) * 1000.0

    # Follow-ups must build on the previous turn without hijacking new questions.
    demo_brain.compose("write a python function to reverse a string")
    follow = demo_brain.compose("add tests")
    hijack = demo_brain.compose("explain tcp vs udp")

    battery_ok, battery_fail = _battery()
    recipes_ok, recipe_fail = _recipes()

    print(f"knowledge topics : {len(demo_brain._K)}")
    print(f"code recipes     : {len(demo_brain._R)}")
    print(f"facts            : {len(demo_brain._FACTS)}")
    print(f"accuracy battery : {battery_ok}/{len(benchmark_speed.CHECKS)}")
    print(f"python recipes   : {recipes_ok} executed cleanly")
    print(f"latency          : {first_ms:.2f} ms cold, {cached_ms:.3f} ms cached")
    print(f"follow-up        : {'ok' if 'test' in follow.lower() else 'FAILED'}")
    print(f"no hijack        : {'ok' if 'tcp' in hijack.lower() else 'FAILED'}")

    for line in battery_fail + recipe_fail:
        print(f"  FAIL {line}")

    ok = not battery_fail and not recipe_fail and "test" in follow.lower() and "tcp" in hijack.lower()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
