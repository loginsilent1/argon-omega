"""Deterministic coding agent for the ARGON-OMEGA IDE.

No model weights are installed on this machine, so the agent does not pretend to
"think". It is a planner over real file operations: it reads the target file,
picks the operation the request asks for, performs it, and returns a unified diff
for every edit. Three modes mirror how agentic editors behave:

    auto    plan → edit → (optionally) run, applying immediately
    turbo   apply immediately, minimal narration
    review  stage the diff and wait for an explicit approval

Supported operations (all real, all reversible from the diff):
    create      write a new file from the recipe library / a typed scaffold
    tests       add a unittest file covering the functions in the target file
    docstrings  insert docstrings for definitions that lack one
    cleanup     CRLF→LF, trailing whitespace, tabs, blank-line runs, final newline
    review      static findings: bare except, mutable defaults, long lines, TODOs…
    run         execute the file and capture stdout/stderr
    explain     describe the file's structure (imports, classes, functions)

Staged changes live in memory keyed by a token; nothing is written until the
client approves, and `revert` writes the previous content back.
"""

from __future__ import annotations

import difflib
import os
import re
import secrets
import subprocess
import sys
import time

import demo_brain

MAX_BYTES = 1_000_000
_STAGED: dict[str, dict] = {}
_STAGE_TTL = 900  # seconds

CODE_EXT = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
            ".md", ".sh", ".sql", ".txt", ".yml", ".yaml", ".toml", ".ini", ".rs", ".go"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _diff(before: str, after: str, path: str, limit: int = 160) -> str:
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{os.path.basename(path)}", tofile=f"b/{os.path.basename(path)}",
        lineterm="", n=2,
    ))
    if len(lines) > limit:
        lines = lines[:limit] + [f"… {len(lines) - limit} more diff lines"]
    return "\n".join(lines)


def _safe_target(path: str, cwd: str | None) -> str:
    """Resolve a path, refusing traversal out of the workspace root."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("No file selected — open one in the editor first.")
    base = os.path.abspath(cwd or os.getcwd())
    full = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    if ".." in raw.split(os.sep):
        raise ValueError("Path traversal is not allowed.")
    if os.path.isdir(full):
        raise ValueError("That is a folder, not a file.")
    if os.path.exists(full) and os.path.getsize(full) > MAX_BYTES:
        raise ValueError("File is larger than 1 MB — the agent will not rewrite it.")
    return full


def _functions(text: str) -> list[str]:
    return re.findall(r"^def\s+([A-Za-z_]\w*)\s*\(", text, re.M)


def _classes(text: str) -> list[str]:
    return re.findall(r"^class\s+([A-Za-z_]\w*)", text, re.M)


def _imports(text: str) -> list[str]:
    found = re.findall(r"^(?:import\s+\w+|from\s+[\w.]+\s+import\s+\w+)", text, re.M)
    return sorted(set(found))[:12]


def _slug(text: str, fallback: str = "agent_note") -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    while words and words[0] in {"a", "an", "the", "your", "my"}:
        words.pop(0)
    return "_".join(words[:5]) or fallback


# --------------------------------------------------------------------------
# operations — each returns (title, after_text, extra_notes, kind)
# --------------------------------------------------------------------------
def _op_create(prompt: str, cwd: str) -> tuple[str, str, str, str]:
    explicit = re.search(
        r"(?:create|make|write|new)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:file\s+)?"
        r"(?:named\s+|called\s+)?[\"'`]?([\w./\-]+\.(?:py|js|mjs|ts|html|css|json|md|sh|sql|txt|yml|yaml|rs|go))[\"'`]?",
        prompt, re.I,
    )
    key = demo_brain._pick_code_key(prompt)
    recipe = demo_brain._R.get(key) if key else None

    if explicit:
        name = explicit.group(1).replace("\\", "/")
    elif recipe:
        ext = {"javascript": "js", "html": "html"}.get(recipe["lang"], "py")
        name = f"{_slug(recipe['title'], 'agent_file')}.{ext}"
    else:
        ext = "html" if re.search(r"\b(html|page|website)\b", prompt, re.I) else "py"
        name = f"{_slug(prompt)}.{ext}"

    target = os.path.abspath(os.path.join(cwd, name))
    if os.path.exists(target):
        raise ValueError(f"{os.path.basename(target)} already exists — open it and ask for a change instead.")

    if recipe:
        body = recipe["code"].rstrip() + "\n"
        notes = f"Generated from the **{recipe['title']}** recipe; it runs as-is."
    elif target.endswith(".py"):
        body = (
            '"""Created by the ARGON agent."""\n\n'
            "def main() -> None:\n"
            '    """TODO: describe what this program does."""\n'
            "    raise NotImplementedError\n\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        )
        notes = "Typed Python scaffold — fill in `main()`; run it with the ▶ Run button."
    elif target.endswith(".html"):
        body = demo_brain._R["html"]["code"].rstrip() + "\n"
        notes = "Self-contained page — switch the right pane to **Live Preview** to render it."
    else:
        body = f"# {os.path.basename(target)}\n"
        notes = "Empty file with a header comment."
    return f"Create {os.path.basename(target)}", body, notes, target


def _op_tests(path: str, text: str) -> tuple[str, str, str, str]:
    funcs = _functions(text)
    if not funcs:
        raise ValueError("No top-level `def` found in this file — nothing to test yet.")
    stem = os.path.splitext(os.path.basename(path))[0]
    test_path = os.path.join(os.path.dirname(path), f"test_{stem}.py")
    existing = _read(test_path) if os.path.exists(test_path) else ""

    imports = "\n".join(f"    {name}," for name in funcs)
    methods = []
    for name in funcs:
        if f"def test_{name}" in existing:
            continue
        methods.append(
            f"    def test_{name}_is_callable(self):\n"
            f"        self.assertTrue(callable({name}))\n\n"
            f"    def test_{name}_handles_empty_input(self):\n"
            f"        # TODO: replace with the real contract for {name}()\n"
            f"        with self.assertRaises((TypeError, ValueError)):\n"
            f"            {name}()\n"
        )
    if not methods:
        raise ValueError(f"{os.path.basename(test_path)} already covers every function.")

    header = "" if existing else (
        '"""Generated by the ARGON agent — unittest, runs with pytest too."""\n'
        "import unittest\n\n"
        f"from {stem} import (\n{imports}\n)\n\n\n"
    )
    if existing:
        after = existing.rstrip() + "\n\n" + f"class Test{stem.title().replace('_', '')}More(unittest.TestCase):\n" + "\n".join(methods)
    else:
        after = header + f"class Test{stem.title().replace('_', '')}(unittest.TestCase):\n" + "\n".join(methods)
    if not after.endswith("\n"):
        after += "\n"
    if not existing:
        after += '\n\nif __name__ == "__main__":\n    unittest.main()\n'
    notes = (f"Covered {len(funcs)} function(s): {', '.join(funcs)}. "
             "The edge-case bodies are TODO — the agent will not invent expected values.")
    return f"Write {os.path.basename(test_path)}", after, notes, test_path


def _op_docstrings(text: str) -> tuple[str, str, str, str]:
    lines = text.splitlines()
    out: list[str] = []
    inserted: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        match = re.match(r"^(\s*)def\s+([A-Za-z_]\w*)\s*\(", line)
        if match and line.rstrip().endswith(":"):
            indent, name = match.group(1), match.group(2)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not nxt.strip().startswith(('"""', "'''")):
                out.append(f'{indent}    """TODO: one sentence describing {name}."""')
                inserted.append(name)
        i += 1
    if not inserted:
        return "", "", "Every definition already has a docstring.", ""
    notes = f"Added docstring stubs to: {', '.join(inserted)}."
    return f"Add docstrings ({len(inserted)})", "\n".join(out) + "\n", notes, ""


def _op_cleanup(text: str) -> tuple[str, str, str, str]:
    notes = []
    cleaned = text
    if "\r\n" in cleaned:
        cleaned = cleaned.replace("\r\n", "\n")
        notes.append("normalised CRLF line endings")
    stripped = "\n".join(line.rstrip() for line in cleaned.split("\n"))
    if stripped != cleaned:
        notes.append("removed trailing whitespace")
    cleaned = stripped
    if "\t" in cleaned:
        cleaned = re.sub(r"(?m)^(\t+)", lambda m: "    " * len(m.group(1)), cleaned)
        notes.append("tabs → 4 spaces")
    collapsed = re.sub(r"\n{3,}", "\n\n\n", cleaned)
    if collapsed != cleaned:
        notes.append("collapsed blank-line runs")
    cleaned = collapsed.rstrip("\n") + "\n"
    if not notes:
        return "", "", "File is already clean — nothing to change.", ""
    return "Clean up formatting", cleaned, "Applied: " + ", ".join(notes) + ".", ""


def _op_review(path: str, text: str) -> tuple[str, str, str, str]:
    lines = text.splitlines()
    findings: list[str] = []
    if not text.endswith("\n"):
        findings.append("No trailing newline (POSIX tools complain).")
    if "\r\n" in text:
        findings.append("CRLF line endings mixed into a Unix file.")
    long_lines = [i + 1 for i, line in enumerate(lines) if len(line) > 100]
    if long_lines:
        findings.append(f"{len(long_lines)} line(s) over 100 chars, first at line {long_lines[0]}.")
    bare = [i + 1 for i, line in enumerate(lines) if re.match(r"^\s*except\s*:", line)]
    if bare:
        findings.append(f"Bare `except:` at line(s) {', '.join(map(str, bare[:5]))} — catches KeyboardInterrupt too.")
    mutable = re.findall(r"def\s+(\w+)\([^)]*?=\s*(\[\]|\{\})", text)
    if mutable:
        findings.append("Mutable default argument(s): " +
                        ", ".join(f"{n}({d})" for n, d in mutable[:5]) + " — use `None` + a factory.")
    todos = re.findall(r"#\s*(TODO|FIXME|XXX)\b", text, re.I)
    if todos:
        findings.append(f"{len(todos)} TODO/FIXME marker(s).")
    no_doc = [f for f in _functions(text)
              if not re.search(r"^def\s+" + re.escape(f) + r"\([^)]*\)[^:]*:\s*\n\s*(\"\"\"|''')", text, re.M)]
    if no_doc:
        findings.append(f"{len(no_doc)} function(s) without a docstring: {', '.join(no_doc[:6])}.")
    unsafe = [k for k in ("eval(", "exec(", "os.system(", "shell=True", "pickle.loads")
              if k in text]
    if unsafe:
        findings.append("Review before shipping: " + ", ".join(f"`{k}`" for k in unsafe) + ".")
    if not findings:
        findings.append("Nothing obvious: no bare excepts, mutable defaults, TODOs or long lines.")

    summary = "\n".join([
        f"### Review of `{os.path.basename(path)}`",
        "",
        f"- {len(lines)} lines · {len(_functions(text))} functions · {len(_classes(text))} classes",
        "",
        "**Findings**",
        *[f"- {item}" for item in findings],
        "",
        "*Static checks only — no model weights, so nothing here is a guess about your intent.*",
    ])
    return "Review file", "", summary, ""


def _op_explain(path: str, text: str) -> tuple[str, str, str, str]:
    funcs, classes, imports = _functions(text), _classes(text), _imports(text)
    body = "\n".join([
        f"### `{os.path.basename(path)}`",
        "",
        f"- {len(text.splitlines())} lines",
        f"- imports: {', '.join(imports) if imports else 'none'}",
        f"- classes: {', '.join(classes) if classes else 'none'}",
        f"- functions: {', '.join(funcs) if funcs else 'none'}",
        "",
        "*Structural summary from parsing the file. Ask for **review** for static findings, or pick a recipe "
        "in chat for a real explanation of the concepts involved.*",
    ])
    return "Explain file", "", body, ""


def _op_run(path: str) -> tuple[str, str, str, str]:
    if not path.endswith(".py"):
        raise ValueError("The agent runs Python files; open a .py file or use the terminal.")
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=30,
                          cwd=os.path.dirname(path) or None)
    ms = (time.perf_counter() - started) * 1000
    out = (proc.stdout or "").strip() or "(no stdout)"
    err = (proc.stderr or "").strip()
    body = "\n".join([
        f"**`python {os.path.basename(path)}`** · exit {proc.returncode} · {ms:.0f} ms",
        "",
        "```text",
        out[:4000],
        *(("", err[:2000]) if err else ()),
        "```",
    ])
    return f"Run {os.path.basename(path)}", "", body, ""


# --------------------------------------------------------------------------
# intent routing
# --------------------------------------------------------------------------
def _pick_op(prompt: str, path: str | None) -> str:
    low = prompt.lower()
    if re.search(r"\b(run|execute|try it)\b", low) and path:
        return "run"
    if re.search(r"\btests?\b|\bunittest\b|\bpytest\b", low):
        return "tests"
    if re.search(r"\bdocstrings?\b|\bdocument\b|\bcomment(s|ed)? code\b", low):
        return "docstrings"
    if re.search(r"\b(format|clean|lint|tidy|whitespace|crlf|indent)\b", low):
        return "cleanup"
    if re.search(r"\breview\b|\baudit\b|\bsmell", low):
        return "review"
    if re.search(r"\bexplain\b|\bwhat does this (file|code) do\b|\bsummar", low):
        return "explain"
    if re.search(r"\b(create|new file|write.*\bfile\b|scaffold|generate)\b", low) or not path:
        return "create"
    # default for an open file: a review is the safest non-destructive action
    return "review"


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def run(prompt: str, mode: str = "auto", path: str | None = None,
        cwd: str | None = None, content: str | None = None) -> dict:
    """Plan + execute one agent request. Returns steps, diffs and a staged token."""
    started = time.perf_counter()
    mode = (mode or "auto").lower()
    if mode not in ("auto", "turbo", "review"):
        mode = "auto"
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Give the agent an instruction first."}

    op = _pick_op(prompt, path)
    plan: list[str] = []
    steps: list[dict] = []
    staged_files: list[dict] = []

    try:
        target = _safe_target(path, cwd) if path else None
        before = content if content is not None else (_read(target) if target and os.path.exists(target) else "")

        if op == "create":
            title, after, notes, new_path = _op_create(prompt, os.path.abspath(cwd or os.getcwd()))
            new_path = _safe_target(new_path, cwd)
            plan = ["Pick a filename and recipe", "Generate the file", "Stage the diff"]
            steps.append({"kind": "edit", "title": title, "path": new_path,
                          "diff": _diff("", after, new_path), "notes": notes})
            staged_files.append({"path": new_path, "before": "", "after": after})
        elif op == "tests":
            if not target:
                raise ValueError("Open the file you want tests for.")
            title, after, notes, new_path = _op_tests(target, before)
            new_path = _safe_target(new_path, cwd)
            plan = ["Read the file", "Collect top-level functions", "Write the unittest file"]
            steps.append({"kind": "edit", "title": title, "path": new_path,
                          "diff": _diff(_read(new_path) if os.path.exists(new_path) else "", after, new_path),
                          "notes": notes})
            staged_files.append({"path": new_path,
                                 "before": _read(new_path) if os.path.exists(new_path) else "",
                                 "after": after})
        elif op in ("docstrings", "cleanup"):
            if not target or not os.path.exists(target):
                raise ValueError("Open a file first — there is nothing to edit.")
            title, after, notes, _ = (_op_docstrings(before) if op == "docstrings" else _op_cleanup(before))
            if not after:
                steps.append({"kind": "note", "title": title or "No change", "notes": notes,
                              "path": target, "diff": ""})
            else:
                plan = ["Read the file", title]
                steps.append({"kind": "edit", "title": title, "path": target,
                              "diff": _diff(before, after, target), "notes": notes})
                staged_files.append({"path": target, "before": before, "after": after})
        elif op == "review":
            if not target:
                raise ValueError("Open a file to review.")
            title, _after, notes, _ = _op_review(target, before)
            plan = ["Read the file", "Run static checks"]
            steps.append({"kind": "note", "title": title, "notes": notes, "path": target, "diff": ""})
        elif op == "explain":
            if not target:
                raise ValueError("Open a file to explain.")
            title, _after, notes, _ = _op_explain(target, before)
            plan = ["Read the file", "Parse structure"]
            steps.append({"kind": "note", "title": title, "notes": notes, "path": target, "diff": ""})
        elif op == "run":
            if not target:
                raise ValueError("Open a Python file to run.")
            read_step = {"kind": "read", "title": f"Read {os.path.basename(target)}",
                         "path": target, "notes": f"{len(before.splitlines())} lines", "diff": ""}
            steps.append(read_step)
            title, _after, notes, _ = _op_run(target)
            plan = ["Read the file", "Execute it", "Capture output"]
            steps.append({"kind": "run", "title": title, "notes": notes, "path": target, "diff": ""})
        else:  # pragma: no cover - defensive
            raise ValueError(f"Unsupported operation: {op}")

    except subprocess.TimeoutExpired:
        return {"ok": False, "op": op, "error": "The file ran longer than 30 s and was stopped."}
    except Exception as exc:  # noqa: BLE001 - surfaced to the client verbatim
        return {"ok": False, "op": op, "error": str(exc),
                "ms": round((time.perf_counter() - started) * 1000, 2)}

    applied_now: list[str] = []
    token = None
    if staged_files:
        if mode == "review":
            token = secrets.token_hex(8)
            _STAGED[token] = {"files": staged_files, "at": time.time(), "prompt": prompt}
            for step in steps:
                if step["kind"] == "edit":
                    step["status"] = "staged"
        else:
            for item in staged_files:
                _write(item["path"], item["after"])
                applied_now.append(item["path"])
            token = secrets.token_hex(8)
            _STAGED[token] = {"files": staged_files, "at": time.time(), "prompt": prompt, "applied": True}
            for step in steps:
                if step["kind"] == "edit":
                    step["status"] = "applied"
    for step in steps:
        step.setdefault("status", "done")

    _prune()
    return {
        "ok": True,
        "op": op,
        "mode": mode,
        "plan": [] if mode == "turbo" else plan,
        "steps": steps,
        "staged_id": token,
        "touched": applied_now,
        "awaiting_review": bool(staged_files) and mode == "review",
        "ms": round((time.perf_counter() - started) * 1000, 2),
        "engine": "demo agent (deterministic file operations)",
    }


def apply(token: str) -> dict:
    item = _STAGED.get(token)
    if not item:
        return {"ok": False, "error": "That review expired — ask the agent again."}
    written = []
    for entry in item["files"]:
        _write(entry["path"], entry["after"])
        written.append(entry["path"])
    item["applied"] = True
    return {"ok": True, "touched": written}


def discard(token: str) -> dict:
    _STAGED.pop(token, None)
    return {"ok": True, "discarded": True}


def revert(token: str) -> dict:
    item = _STAGED.get(token)
    if not item:
        return {"ok": False, "error": "Nothing to undo for that run."}
    for entry in item["files"]:
        _write(entry["path"], entry["before"])
    _STAGED.pop(token, None)
    return {"ok": True, "reverted": [e["path"] for e in item["files"]]}


def _prune() -> None:
    now = time.time()
    for token in [t for t, item in _STAGED.items() if now - item.get("at", 0) > _STAGE_TTL]:
        _STAGED.pop(token, None)
