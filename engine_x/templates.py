"""templates.py - Layer 0: plan-then-emit. The real 10x lever.

Why exact-match caching is the wrong idea
-----------------------------------------
A 10B CPU model writes ~800 tokens for "add a retry decorator". At 2 tok/s that is
6.7 minutes. If a cache stored the *finished answer* it would break the moment the user
changes a variable name, and it teaches the model nothing.

So store the *plan*, not the answer:

    task kind + slots  ->  ordered skeleton of edit slots
    "wrap this function in retry"  ->  [import contextlib, decorate, for-loop, sleeps]

The model then fills 3 short slots instead of inventing 800 tokens of structure. That is
where 800 -> ~60 tokens comes from, and why it still works on inputs never seen before.

Design rules that keep this honest
----------------------------------
1. A template is only eligible when *every required slot* is extractable from the user's
   request with a regex. If a slot is missing we say so; we never invent one.
2. Rendering is deterministic text, no model call, so token count is exact and
   comparable (tiktoken when installed, else the repo's own regex tokenizer, labelled).
3. Templates are verified: `verify()` runs a dozen unseen phrasings through the matcher
   so adding a template cannot silently break an existing one.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

try:                                    # package import
    from engine_x import template_refs
except ImportError:                     # run as a plain script
    import template_refs

try:                                    # exact counts when available
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

    TOKENIZER = "tiktoken:cl100k_base"
except Exception:                       # pragma: no cover - offline default
    from engine_x.triemind import tokenize as _tok

    def count_tokens(text: str) -> int:
        return len(_tok(text))

    TOKENIZER = "regex:lossless"


@dataclass
class Template:
    name: str
    kind: str
    patterns: list[str]                 # any of these selects the template
    required: list[str]                 # slots that must be extractable
    slots: dict[str, str]               # slot name -> regex with one group
    skeleton: str                       # plan, {slot} placeholders
    guidance: str = ""                  # short instruction to the model
    notes: str = ""
    # a genuinely verbose answer to the same request, written out by hand so the
    # "how many tokens did this save" claim is a tokenization, not an opinion
    reference_answer: str = ""

    freeform_reference: str = ""        # a full written-out answer of the same task
    _compiled: list = field(default_factory=list, repr=False)

    def compile(self) -> "Template":
        self._compiled = [re.compile(p, re.I | re.S) for p in self.patterns]
        return self

    def match(self, text: str) -> dict | None:
        if not self._compiled:
            self.compile()
        if not any(p.search(text) for p in self._compiled):
            return None
        out = {}
        for slot in self.required:
            m = re.search(self.slots[slot], text, re.I | re.S)
            if not m:
                return None                 # refuse rather than guess
            out[slot] = m.group(1).strip()
        # opportunistic slots never block eligibility
        for slot, rx in self.slots.items():
            if slot in out:
                continue
            m = re.search(rx, text, re.I | re.S)
            if m:
                out[slot] = m.group(1).strip()
        return out

    def holes(self, slots: dict) -> list[str]:
        """Placeholders the render left unfilled - every one needs the model to fill it.

        They are left visibly unfilled, never guessed: an invented slot value is a silent
        wrong answer, while a visible hole is one identifier to supply.
        """
        return sorted(set(re.findall(r"\{(\w+)\}", self.render(slots))))

    def render(self, slots: dict) -> str:
        class _D(dict):
            def __missing__(self, key):
                return "{" + key + "}"          # stays visible, never fabricated
        body = self.skeleton.format_map(_D(slots))
        if self.guidance:
            body = f"# plan: {self.name}\n# {self.guidance}\n\n{body}"
        return body

    def render_full(self, slots: dict) -> str:
        """Plan + filled skeleton: the entire model output if the model obeys it."""
        return self.render(slots)


class TemplateBank:
    """Ordered matcher. First match wins; every template must be verified."""

    def __init__(self, templates: list[Template] | None = None):
        self.templates = [t.compile() for t in (templates or TEMPLATES)]

    def match(self, request: str) -> tuple[Template, dict] | tuple[None, None]:
        for t in self.templates:
            slots = t.match(request)
            if slots is not None:
                return t, slots
        return None, None

    def plan(self, request: str) -> dict:
        t0 = time.perf_counter()
        t, slots = self.match(request)
        ms = (time.perf_counter() - t0) * 1e3
        if t is None:
            return {"hit": False, "name": None, "tokens_baseline": None,
                    "match_ms": round(ms, 4),
                    "reason": "no template matched - fall through to normal decode"}
        text = t.render(slots)
        holes = t.holes(slots)
        return {
            "hit": True,
            "name": t.name,
            "kind": t.kind,
            "slots": slots,
            "holes": holes,
            "plan": text,
            "tokens_planned": count_tokens(text),
            "match_ms": round(ms, 4),
            "tokenizer": TOKENIZER,
        }

    def verify(self, cases: list[tuple[str, str]]) -> dict:
        """cases: (request, expected_template_or_'MISS')."""
        failures = []
        for request, expected in cases:
            t, _ = self.match(request)
            got = t.name if t else "MISS"
            if got != expected:
                failures.append({"request": request, "expected": expected, "got": got})
        return {"cases": len(cases), "failures": failures, "ok": not failures}


# --------------------------------------------------------------------------- templates
# Every slot regex is deliberately narrow: a wrong extraction is worse than a miss.
_FUNC = r"((?:def|function|async function|public|private)\s+[A-Za-z_][\w]*\s*\([^)]*\))"
_NAME = r"(?:`([A-Za-z_][\w]*)`|\b(?:named|called|function\s+)\s+([A-Za-z_][\w]*))"

TEMPLATES: list[Template] = [
    Template(
        name="python-retry-decorator",
        kind="code_edit",
        patterns=[r"retry", r"retries", r"backoff", r"transient fail"],
        required=["target"],
        slots={"target": r"(?:for|to|on|wrap)\s+(?:this\s+|the\s+)?(?:function\s+)?`?([A-Za-z_][\w.]*)`?",
               "attempts": r"(\d+)\s*(?:times|attempts|tries|retries)"},
        skeleton="""```python
import time, functools

def with_retry(attempts: int = {attempts}):   # attempts: from the request, else template
    def deco(fn):
        @functools.wraps(fn)
        def inner(*a, **kw):
            last = None
            for i in range(attempts):
                try:
                    return fn(*a, **kw)
                except Exception as exc:
                    last = exc
                    time.sleep(0.2 * (2 ** i))
            raise last
        return inner
    return deco

@with_retry()
def {target}(*args, **kwargs):
    ...
```
""",
        guidance="fill only the decorator and the target signature; no prose",
        reference_answer=template_refs.RETRY,
    ),
    Template(
        name="add-test",
        kind="code_edit",
        patterns=[r"\b(?:add|write|generate)\b.*\btests?\b", r"\bunit test\b", r"\bpytest\b"],
        required=["target"],
        slots={"target": r"(?:for|of|covering)\s+`?([A-Za-z_][\w.]*)`?",
               "framework": r"\b(pytest|unittest|jest|vitest|go test)\b"},
        skeleton="""```python
# tests/test_{target}.py
import pytest
from {module} import {target}

def test_{target}_happy_path():
    assert {target}(...) is not None

def test_{target}_rejects_bad_input():
    with pytest.raises((ValueError, TypeError)):
        {target}(None)
```
""",
        guidance="two tests are enough; no prose, no extra helpers",
        reference_answer=template_refs.TEST,
    ),
    Template(
        name="explain-error",
        kind="answer",
        patterns=[r"traceback", r"stack ?trace", r"\berror\b.*\b(?:why|mean|means)\b",
                  r"exception"],
        required=["error"],
        slots={"error": r"(?:error|exception|traceback)\s*[:\-]?\s*([A-Za-z_][\w.]*(?:Error|Exception|Warning))",
               "file": r'File "([^"]+)"'},
        skeleton="""1. What it is: {error} raised at {file}
2. Usual cause: (one line, the specific violation)
3. Fix: (one code block, minimal diff)
4. Check: the command that proves it is fixed
""",
        guidance="four numbered lines only; no restating the traceback",
        reference_answer=template_refs.ERROR,
    ),
    Template(
        name="refactor-rename",
        kind="code_edit",
        patterns=[r"\brename\b", r"\bextract (?:method|function)\b", r"\bmove\b.*\b(?:into|to)\b.*\bfunction\b"],
        required=["old", "new"],
        slots={"old": r"rename\s+`?([A-Za-z_][\w]*)`?\s+to",
               "new": r"rename\s+`?[A-Za-z_][\w]*`?\s+to\s+`?([A-Za-z_][\w]*)`?"},
        skeleton="""```diff
- def {old}(
+ def {new}(
```
Call sites: search `{old}\\(` and update each; run the test suite after.
""",
        guidance="the diff plus one line on call sites",
        reference_answer=template_refs.RENAME,
    ),
    Template(
        name="json-to-python",
        kind="code_edit",
        patterns=[r"\b(?:convert|parse|load)\b.*\bjson\b.*\b(?:python|dataclass|object)\b",
                  r"\bdataclass\b.*\bjson\b"],
        required=["cls"],
        slots={"cls": r"(?:class|dataclass)\s+`?([A-Z][\w]*)`?"},
        skeleton="""```python
from dataclasses import dataclass, asdict
import json

@dataclass
class {cls}:
    ...

    @classmethod
    def from_json(cls, raw: str) -> "{cls}":
        return cls(**json.loads(raw))

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```
""",
        guidance="the class plus the two round-trip methods; stop there",
    ),
    Template(
        name="sql-query",
        kind="code_edit",
        patterns=[r"\bselect\b.*\bfrom\b", r"\bsql\b.*\bquery\b", r"\bjoin\b.*\bon\b"],
        required=["table"],
        slots={"table": r"\bfrom\s+`?([A-Za-z_][\w]*)"},
        skeleton="""```sql
SELECT t.*
FROM {table} AS t
WHERE t.created_at >= :since
ORDER BY t.created_at DESC
LIMIT :n;
```
""",
        guidance="one parameterised query; no prose",
    ),
    Template(
        name="git-recovery",
        kind="answer",
        patterns=[r"\bundo\b.*\bcommit\b", r"\breset\b.*\bcommit\b", r"\bstash\b", r"\bdropped\b"],
        required=[],
        slots={"commit": r"\b([0-9a-f]{7,40})\b"},
        skeleton="""1. Inspect: `git reflog` (find the commit above {commit})
2. Recover: `git reset --soft <sha>` to keep changes, `--hard` to discard
3. Verify: `git status` then `git log --oneline -3`
""",
        guidance="three commands, no prose",
    ),
]


def _demo() -> None:
    bank = TemplateBank()
    print("templates:", [t.name for t in bank.templates])
    for req in ("add a retry decorator for fetch_user with 3 attempts",
                "write tests for parse_config",
                "why does this traceback: ValueError: bad magic",
                "rename `foo` to `bar`"):
        print(req, "->", bank.plan(req))


if __name__ == "__main__":
    _demo()
