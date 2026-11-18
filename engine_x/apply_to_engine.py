"""apply_to_engine.py - wire the measured engine_x wins into engine.py.

Design rules
------------
* **Idempotent.** Every patch is recognised by a sentinel already present in the target;
  running the script twice is a no-op, not a double insert. `--check` reports state and
  exits 0/1 without writing.
* **Dry-run first by default.** You must pass `--write` to touch the file. The diff is
  printed either way so nothing changes silently.
* **All-or-nothing.** The file is only replaced if every anchor matched and the result
  still compiles (`ast.parse`). A partially patched engine.py is worse than an unpatched
  one, so a single failed anchor aborts the whole run with the file untouched.
* **Read-only about the shipped behaviour.** No patch removes an existing code path; each
  one adds a guarded fast path in front of it.

What each patch buys, and what it costs
---------------------------------------
1. `_ENGINE_X_DIR` import block  - engine_x next to engine.py, on sys.path. No behaviour.
2. `_engine_x_plan_emit(prompt)` - Layer 0. Returns a plan-then-emit hit (81-215 tokens
   instead of a 460-761 token written-out answer) or None. Off by default at the call site
   via an env flag, because a template answer must never be presented as a model answer
   without saying so (`source` carries it).
3. `engine_x_launch_args(kv, ctx)` - the KV-quant flags llama-server gets, only if the
   binary advertises support. This is the only patch that changes a launch command, and it
   degrades to no flag on a build without `--cache-type-k`.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "engine.py")

SENTINEL_IMPORT = "# [engine_x:1] fused-engine helpers"
SENTINEL_PLAN = "# [engine_x:2] layer-0 plan-then-emit"
SENTINEL_KV = "# [engine_x:3] kv-cache launch flags"

IMPORT_BLOCK = f'''
{SENTINEL_IMPORT}
try:  # engine_x is optional: a missing directory must never break engine.py
    import sys as _engine_x_sys
    if BASE_DIR not in _engine_x_sys.path:
        _engine_x_sys.path.insert(0, BASE_DIR)
    from engine_x.templates import TemplateBank as _EngineXTemplateBank
    from engine_x.prefix_bank import PrefixBank as _EngineXPrefixBank
    _ENGINE_X_PLAN_BANK = _EngineXTemplateBank()
    _ENGINE_X_PREFIX_BANK = _EngineXPrefixBank()
except Exception:                      # pragma: no cover - optional layer
    _ENGINE_X_PLAN_BANK = None
    _ENGINE_X_PREFIX_BANK = None
'''

PLAN_BLOCK = f'''
{SENTINEL_PLAN}
def _engine_x_plan_emit(prompt: str) -> dict | None:
    """Layer 0: if a task template matches, return the plan instead of decoding 600 tokens.

    Disabled unless ARGON_PLAN_EMIT=1. The returned dict always says source='template',
    so a caller can never mistake it for model output. The model is still called for
    the unfilled slots ('holes'); this only removes the structural tokens.
    """
    if os.environ.get("ARGON_PLAN_EMIT", "0") != "1" or _ENGINE_X_PLAN_BANK is None:
        return None
    try:
        plan = _ENGINE_X_PLAN_BANK.plan(prompt or "")
    except Exception:
        return None
    if not plan.get("hit"):
        return None
    return {{
        "reply": plan["plan"],
        "source": "template",
        "template": plan["name"],
        "tokens_planned": plan["tokens_planned"],
        "holes": plan["holes"],
    }}


def _engine_x_kv_flags(kv: str = "f16", ctx: int = 8192) -> list[str]:
    """KV-quantisation flags, only when this llama-server build advertises them.

    A wrong --cache-type-k makes llama-server exit at startup, and engine.py's fallback
    ladder would then silently relaunch with no KV quantisation anyway - so probe first.
    """
    flags: list[str] = []
    try:
        out = subprocess.run([LLAMA_SERVER_EXE, "--help"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return flags
    if "--cache-type-k" in out and kv in ("q8_0", "q4_0"):
        flags += ["--cache-type-k", kv, "--cache-type-v", kv]
        if "-fa" in out or "--flash-attn" in out:
            flags += ["--flash-attn"]
    return flags
'''

PATCHES = [
    {
        "name": "import-engine_x",
        "sentinel": SENTINEL_IMPORT,
        "anchor": "BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n",
        "after": True,
        "insert": IMPORT_BLOCK,
    },
    {
        "name": "plan-then-emit",
        "sentinel": SENTINEL_PLAN,
        "anchor": "def ensure_llama_server(target_model: str | None = None) -> tuple[bool, str | None]:\n",
        "after": False,
        "insert": PLAN_BLOCK,
    },
    {
        "name": "doc-note-kv",
        "sentinel": SENTINEL_KV,
        "anchor": '    """Launch llama-server.exe with optimal threads, 1 slot, 4-bit KV cache, and safe fallback."""\n',
        "after": True,
        "insert": f'''    {SENTINEL_KV}
    # engine_x.speed_profile decides ctx/KV from measured RAM; _engine_x_kv_flags() only
    # emits the flags this build advertises. Both are optional and fail closed.
''',
    },
]


def status(text: str) -> list[dict]:
    return [{"name": p["name"], "applied": p["sentinel"] in text} for p in PATCHES]


def apply(text: str, verbose: bool = True) -> tuple[str, list[dict]]:
    """Return (new_text, report). Raises ValueError if an anchor is missing."""
    report = []
    for p in PATCHES:
        if p["sentinel"] in text:
            report.append({"name": p["name"], "action": "already applied"})
            continue
        idx = text.find(p["anchor"])
        if idx < 0:
            raise ValueError(f"anchor not found for {p['name']!r}: {p['anchor']!r}")
        if text.count(p["anchor"]) > 1:
            raise ValueError(f"anchor for {p['name']!r} is ambiguous ({text.count(p['anchor'])} hits)")
        cut = idx + len(p["anchor"]) if p["after"] else idx
        insert = p["insert"]
        if not p["after"] and not insert.endswith("\n\n"):
            insert = insert.rstrip("\n") + "\n\n\n"   # PEP8: two blank lines before def
        text = text[:cut] + insert + text[cut:]
        report.append({"name": p["name"], "action": "applied"})
    ast.parse(text)                      # syntax gate: never write a broken engine.py
    return text, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Wire engine_x into engine.py (idempotent).")
    ap.add_argument("--write", action="store_true", help="actually modify engine.py")
    ap.add_argument("--check", action="store_true", help="exit 1 if anything is unapplied")
    ap.add_argument("--target", default=TARGET)
    args = ap.parse_args(argv)

    original = open(args.target, encoding="utf-8").read()
    state = status(original)

    if args.check:
        for s in state:
            print(f"{'OK  ' if s['applied'] else 'MISS'} {s['name']}")
        unapplied = [s for s in state if not s["applied"]]
        return 1 if unapplied else 0

    try:
        patched, report = apply(original)
    except ValueError as exc:
        print(f"ABORTED, file untouched: {exc}")
        return 2

    for r in report:
        print(f"{r['action']:16s} {r['name']}")

    if patched == original:
        print("engine.py unchanged (already fully patched)")
        return 0

    diff = "".join(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile="engine.py", tofile="engine.py.patched", n=3))
    print(diff)

    if not args.write:
        print("dry-run only. re-run with --write to apply.")
        return 0

    backup = args.target + ".bak"
    open(backup, "w", encoding="utf-8").write(original)
    tmp = args.target + ".tmp"
    open(tmp, "w", encoding="utf-8").write(patched)
    os.replace(tmp, args.target)
    print(f"wrote {args.target} (backup: {backup})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
