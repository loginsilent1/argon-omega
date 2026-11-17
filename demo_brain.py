"""ARGON-OMEGA demo brain: simulated local inference.

Used automatically when no GGUF brain is installed / llama-server is not
available, so the UI is fully interactive out of the box (chat, streaming,
IDE assistance, benchmarking). Replies are generated locally from intent
templates — no network, no model weights.

Disable with:  ARGON_DEMO_BRAIN=0  python app.py
"""

from __future__ import annotations

import ast
import datetime as _dt
import json
import math
import os
import random
import re
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "demo_memory.json")

_LOCK = threading.Lock()
_SPEED_TOKENS_PER_SEC = 42.0  # simulated throughput, used for pacing + badges


# --------------------------------------------------------------------------
# availability / status
# --------------------------------------------------------------------------
def enabled() -> bool:
    return os.environ.get("ARGON_DEMO_BRAIN", "1").strip().lower() not in ("0", "false", "off", "no")


def status() -> dict:
    return {
        "enabled": enabled(),
        "active": enabled(),
        "name": "Argon Demo Brain (simulated)",
        "speed_tokens_per_sec": _SPEED_TOKENS_PER_SEC,
        "memory_facts": len(_load_memory()),
        "note": "No GGUF installed — replies are simulated locally, not model output.",
    }


def badge(ttft_ms: float = 0.0, tok_per_sec: float | None = None) -> str:
    tps = _SPEED_TOKENS_PER_SEC if tok_per_sec is None else tok_per_sec
    return (
        f"🧪 Demo Brain (simulated) · no GGUF installed | ⚡ TTFT: {ttft_ms:.0f}ms | "
        f"🚀 Speed: ~{tps:.0f} tok/s"
    )


# --------------------------------------------------------------------------
# tiny memory store (so "remember X" feels real)
# --------------------------------------------------------------------------
def _load_memory() -> list[dict]:
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_memory(items: list[dict]) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(items[-200:], fh, indent=1)
    except OSError:
        pass


def remember(fact: str) -> None:
    fact = fact.strip().rstrip(".")
    if not fact:
        return
    with _LOCK:
        items = _load_memory()
        items.append({"fact": fact, "at": _dt.datetime.now().isoformat(timespec="seconds")})
        _save_memory(items)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    return re.findall(r"\S+\s*", text)


def _sleep_pacing(tokens: int, thinking_level: str) -> float:
    """Total simulated generation time (seconds) for a given token count."""
    level_factor = {"low": 0.6, "mid": 1.0, "high": 1.6}.get(thinking_level, 1.0)
    return max(0.25, tokens / _SPEED_TOKENS_PER_SEC) * level_factor


def _safe_math(expr: str) -> str | None:
    expr = expr.strip().rstrip("?.!")
    if not re.fullmatch(r"[0-9\s\+\-\*\/\%\.\(\)\^eE,]+", expr or ""):
        return None
    expr = expr.replace("^", "**")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult,
               ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.FloorDiv)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            return None
    try:
        value = eval(compile(tree, "<math>", "eval"), {"__builtins__": {}}, {})  # noqa: S307 - whitelisted AST
    except Exception:
        return None
    if isinstance(value, float):
        if math.isclose(value, round(value)):
            value = int(round(value))
        else:
            value = round(value, 10)
    return f"**{expr.replace('**', '^')} = {value}**"


def _topic_of(prompt: str) -> str:
    p = prompt.lower()
    table = {
        "mmap": "memory mapping",
        "gguf": "the GGUF format",
        "quantiz": "quantization",
        "sql injection": "SQL injection",
        "sqli": "SQL injection",
        "xss": "cross-site scripting",
        "csrf": "CSRF",
        "hash": "hashing",
        "token": "tokenization",
        "transformer": "transformers",
        "http": "HTTP",
        "tcp": "TCP",
        "async": "async I/O",
        "thread": "threads",
        "gil": "the GIL",
        "docker": "containers",
        "git": "Git",
        "index": "database indexes",
        "cache": "caching",
        "python": "Python",
        "javascript": "JavaScript",
    }
    for key, label in table.items():
        if key in p:
            return label
    return ""


_CODE_SNIPPETS = {
    "directory": '''```python
import os
from pathlib import Path

def scan_large_files(root: str = ".", min_mb: float = 100) -> list[tuple[str, float]]:
    """Return [(path, size_mb)] for files above min_mb, largest first."""
    threshold = min_mb * 1024 * 1024
    found: list[tuple[str, float]] = []
    for dirpath, _dirs, files in os.walk(os.path.expanduser(root)):
        for name in files:
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:          # permission denied / vanished file
                continue
            if size >= threshold:
                found.append((str(path), round(size / 1024**2, 1)))
    return sorted(found, key=lambda item: item[1], reverse=True)

if __name__ == "__main__":
    for path, mb in scan_large_files("~", 250)[:10]:
        print(f"{mb:9.1f} MB  {path}")
```''',
    "fibonacci": '''```python
def fib(n: int) -> int:
    """Iterative Fibonacci — O(n) time, O(1) memory."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

print([fib(i) for i in range(10)])   # [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
```''',
    "sort": '''```python
from dataclasses import dataclass, field

@dataclass(order=True)
class Record:
    score: float
    name: str = field(compare=False)

rows = [Record(91.5, "b"), Record(78.2, "a"), Record(91.5, "c")]
print(sorted(rows, reverse=True))    # stable + explicit comparison key
```''',
    "csv": '''```python
import csv

with open("data.csv", newline="", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        print(row["name"], row["score"])
```''',
    "json": '''```python
import json
from pathlib import Path

data = json.loads(Path("config.json").read_text(encoding="utf-8"))
data["debug"] = True
Path("config.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
```''',
    "http": '''```python
import json
import urllib.request

req = urllib.request.Request(
    "https://api.example.com/items",
    headers={"User-Agent": "argon-omega", "Accept": "application/json"},
)
with urllib.request.urlopen(req, timeout=15) as resp:      # always set a timeout
    payload = json.load(resp)
print(len(payload), "items")
```''',
    "class": '''```python
class Counter:
    """Minimal, testable example class."""

    def __init__(self, start: int = 0) -> None:
        self._value = start

    def increment(self, by: int = 1) -> int:
        if by < 0:
            raise ValueError("by must be >= 0")
        self._value += by
        return self._value

    def __repr__(self) -> str:
        return f"Counter({self._value})"

c = Counter()
c.increment(4)
print(c)          # Counter(4)
```''',
}

_FACTS = {
    "capital of india": "New Delhi",
    "capital of france": "Paris",
    "capital of nepal": "Kathmandu",
    "capital of japan": "Tokyo",
    "capital of the uk": "London",
    "capital of england": "London",
    "capital of the usa": "Washington, D.C.",
    "largest planet": "Jupiter",
    "speed of light": "299,792,458 m/s (about 300,000 km/s) in a vacuum",
    "boiling point of water": "100 °C (212 °F) at sea-level pressure",
    "who wrote hamlet": "William Shakespeare",
    "square root of 2": "1.41421356237…",
}

_ADD_SNIPPET = '''```python
def add(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a + b


if __name__ == "__main__":
    assert add(2, 3) == 5
    assert add(-1.5, 1.5) == 0
    print(add(2, 3))          # 5
```'''

_HTML_SNIPPET = '''```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Demo page</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font: 16px/1.6 system-ui, sans-serif; color: #e6edf3;
      background: radial-gradient(circle at 30% 20%, #12324a, #0b1220 60%);
    }
    .card {
      padding: 28px 32px; border-radius: 18px; text-align: center;
      background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.12);
      backdrop-filter: blur(10px);
    }
    button {
      margin-top: 14px; padding: 10px 18px; border-radius: 10px; cursor: pointer;
      border: 1px solid #10b981; background: #10b981; color: #04120c; font-weight: 700;
    }
    button:hover { filter: brightness(1.1); }
  </style>
</head>
<body>
  <main class="card">
    <h1>Hello from ARGON-OMEGA</h1>
    <p id="out">Click the button to run the inline script.</p>
    <button onclick="document.getElementById('out').textContent = 'JS is working ✅'">
      Run script
    </button>
  </main>
</body>
</html>
```'''


def _code_reply(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ("directory", "folder", "scan", "large file", "disk usage")):
        snippet, title = _CODE_SNIPPETS["directory"], "a directory scanner for large files"
    elif "fibonacci" in p:
        snippet, title = _CODE_SNIPPETS["fibonacci"], "Fibonacci"
    elif any(k in p for k in ("sort", "order by")):
        snippet, title = _CODE_SNIPPETS["sort"], "sorting records"
    elif "csv" in p:
        snippet, title = _CODE_SNIPPETS["csv"], "CSV parsing"
    elif "json" in p:
        snippet, title = _CODE_SNIPPETS["json"], "JSON read/modify/write"
    elif any(k in p for k in ("http", "api", "request", "fetch", "download")):
        snippet, title = _CODE_SNIPPETS["http"], "an HTTP GET request"
    elif any(k in p for k in ("class", "object", "oop")):
        snippet, title = _CODE_SNIPPETS["class"], "a small class"
    else:
        snippet, title = _CODE_SNIPPETS["directory"], "a file utility"
    return (
        f"Here's a working starting point for {title}.\n\n"
        f"{snippet}\n\n"
        "**Why this shape**\n"
        "- Errors that actually happen (permissions, missing files) are handled, not assumed away.\n"
        "- No third-party imports, so it runs inside the ARGON sandbox as-is.\n"
        "- Pure functions + a `__main__` block keep it testable.\n\n"
        "Want it adapted — different language, CLI flags, or streaming output? Say the word."
    )


_KNOWLEDGE = {
    "memory mapping": (
        "`mmap()` maps a file into the process address space so the OS pages it in on demand. "
        "llama.cpp mmaps GGUF weights, so the model is never fully copied into RAM: cold pages "
        "come from disk, hot pages stay in the page cache, and several processes can share the "
        "same physical pages."
    ),
    "the GGUF format": (
        "GGUF is a single-file container: a header, a key/value metadata block, a tensor-info "
        "table, then the raw tensor data, all 32-byte aligned. Quantised blocks (Q4_K_M, IQ3_XS…) "
        "live next to their scales, so a loader reads metadata first and only touches the tensors "
        "it needs."
    ),
    "quantization": (
        "Weights are stored at lower precision (4-bit blocks with per-block scales) instead of "
        "16-bit floats. A 7B model drops from ~14 GB to ~4.7 GB, and quality loss is small because "
        "outliers are kept at higher precision in the K-quants variants."
    ),
    "tokenization": (
        "Text is split into sub-word tokens (BPE) before the model sees it. English averages "
        "~4 characters per token; code and non-Latin scripts use more tokens for the same text, "
        "which is why they feel slower and cost more context."
    ),
    "http": (
        "A request/response protocol over TCP: method + path + headers, an optional body, then a "
        "status line and body back. Stateless by design — cookies and tokens carry identity "
        "between requests."
    ),
    "tcp": (
        "Ordered, reliable byte streams with congestion control. The handshake costs one round "
        "trip, which is why connection reuse (keep-alive) matters more than raw bandwidth on "
        "high-latency links."
    ),
    "caching": (
        "Store the answer next to the cost you want to avoid. The only hard parts are a correct "
        "key and invalidation — prefer TTLs plus explicit invalidation on write, and always be "
        "able to serve a cold path."
    ),
    "database indexes": (
        "A B-tree keyed on the columns you filter/sort by, so lookups go from O(n) scan to O(log n). "
        "The cost is write amplification and disk: index the predicates you actually use, and check "
        "`EXPLAIN QUERY PLAN` before adding another one."
    ),
    "threads": (
        "OS-scheduled concurrency. In CPython the GIL serialises bytecode execution, so threads help "
        "with I/O-bound work; CPU-bound work wants processes or native extensions that release the GIL."
    ),
    "async i/o": (
        "A single-threaded event loop multiplexing thousands of sockets; `await` yields control "
        "instead of blocking. Fast for I/O, useless for CPU-bound code unless you offload it to a "
        "thread/process pool."
    ),
}


def _explain_reply(prompt: str) -> str:
    topic = _topic_of(prompt) or "this topic"
    detail = _KNOWLEDGE.get(topic)
    if detail:
        return (
            f"### {topic.title()}\n\n"
            f"{detail}\n\n"
            "**Where it bites**\n"
            "- The common failure mode is not the concept, it's the boundary: partial data, timeouts, "
            "or an assumption about size/lifetime that stops being true at scale.\n"
            "- Measure before optimising: a five-line timing harness beats an afternoon of guessing.\n\n"
            "*Demo Brain — install a GGUF (Models → Download) for a full model answer.*"
        )
    return (
        f"### {topic.title()} — quick, practical explanation\n\n"
        "**In one sentence.** The short version, then how it works underneath.\n\n"
        "**How it works**\n"
        "1. What the moving parts are and what each one owns.\n"
        "2. What happens step by step when you actually use it.\n"
        "3. Where the cost lands — CPU, memory, or latency.\n\n"
        "**Trade-offs**\n"
        "| Choice | You gain | You pay |\n"
        "| --- | --- | --- |\n"
        "| Simpler path | less code, fewer bugs | less control |\n"
        "| Optimised path | speed, headroom | complexity, tuning |\n\n"
        "**Try it**\n"
        "```bash\n"
        "# reproduce the behaviour locally, then measure it\n"
        "python -X importtime your_script.py\n"
        "```\n\n"
        "*This is the offline Demo Brain answering from built-in templates. Install a GGUF "
        "(Models → Download) and the same question gets a real model answer.*"
    )


def _security_reply(prompt: str) -> str:
    p = prompt.lower()
    if "sql" in p:
        return (
            "### SQL injection: the bug and the fix\n\n"
            "**Vulnerable** — string-built SQL, input treated as code:\n"
            "```python\n"
            'cur.execute(f"SELECT * FROM users WHERE email = \'{email}\'")\n'
            "```\n"
            "An `email` of `x\' OR \'1\'=\'1` returns every row.\n\n"
            "**Fixed** — parameter binding keeps data as data:\n"
            "```python\n"
            'cur.execute("SELECT * FROM users WHERE email = ?", (email,))\n'
            "```\n\n"
            "**Checklist**\n"
            "- Bind parameters everywhere; never concatenate input into SQL.\n"
            "- Grant the app user only the privileges it needs (no `DROP`, no `FILE`).\n"
            "- Validate on the boundary, log auth failures, alert on spike patterns.\n"
            "- Add the same rule to code review: *any* f-string near `execute(` is a finding.\n\n"
            "Run **Security Lab → SAST** to scan your own files for this pattern."
        )
    if "xss" in p:
        return (
            "### Cross-site scripting (XSS)\n\n"
            "Untrusted input reaching the DOM as HTML lets an attacker run script in your origin.\n\n"
            "**Vulnerable**\n```js\nelement.innerHTML = userComment;\n```\n"
            "**Fixed**\n```js\nelement.textContent = userComment;          // plain text\nelement.replaceChildren(buildNodes(userComment));  // structured DOM\n```\n\n"
            "**Checklist**\n"
            "- Prefer `textContent`; use `innerHTML` only on trusted, sanitised markup.\n"
            "- Set a strict `Content-Security-Policy` (no `unsafe-inline`).\n"
            "- Escape on output, per context (HTML, attribute, JS, URL).\n"
            "- Mark session cookies `HttpOnly; Secure; SameSite=Lax`."
        )
    return (
        "### Hardening checklist (offline, no tools required)\n\n"
        "1. **Patch** OS + browser + Python; most breaches are known-CVE lazy upgrades.\n"
        "2. **Least privilege** — daily account is not admin; services get their own user.\n"
        "3. **Secrets** — environment vars or a vault, never in git; rotate anything ever committed.\n"
        "4. **Backups** — 3 copies, 2 media, 1 off-site; *test the restore*, not the backup.\n"
        "5. **Network** — default-deny inbound; close listeners you do not recognise.\n"
        "6. **Input** — validate at the edge, parameterise queries, escape on output.\n\n"
        "Use **Security Lab → Audit My Laptop** to check listeners and privileges on this machine."
    )


def _math_reply(prompt: str) -> str | None:
    m = re.search(r"(?:what\s+is|calculate|compute|solve|=\s*)\s*([0-9][0-9\s\+\-\*\/\%\.\(\)\^]*)$", prompt.strip(), re.I)
    expr = m.group(1) if m else (prompt if re.fullmatch(r"[\s0-9\+\-\*\/\%\.\(\)\^]+", prompt.strip()) else None)
    if not expr:
        return None
    result = _safe_math(expr)
    if not result:
        return None
    return (
        f"{result}\n\n"
        "Computed locally with Python's evaluator (AST-whitelisted: no names, no calls, "
        "no imports — so it cannot execute anything else)."
    )


def _memory_reply(prompt: str) -> str | None:
    p = prompt.strip()
    m = re.match(r"(?:please\s+)?remember\s+(?:that\s+)?(.+)$", p, re.I)
    if m:
        fact = m.group(1).strip()
        remember(fact)
        return (
            f"Stored in local demo memory: **{fact}**\n\n"
            "- File: `demo_memory.json` (plain JSON, stays on this machine)\n"
            "- Recall anytime with *“what do you remember?”*\n"
            "- With a real GGUF installed this becomes SSD ROM + retrieval instead."
        )
    if re.search(r"(what|anything).*(remember|memory|know about me)", p, re.I):
        items = _load_memory()
        if not items:
            return (
                "Demo memory is empty. Tell me something with *“Remember that …”* and I'll "
                "store it in `demo_memory.json`."
            )
        lines = "\n".join(f"- {item['fact']}  \n  <span class=\"muted\">({item['at']})</span>" for item in items[-20:])
        return f"Here's everything in demo memory ({len(items)} item(s)):\n\n{lines}"
    return None


def _identity_reply() -> str:
    return (
        "I'm **ARGON-OMEGA** — an offline AI studio that runs entirely on your machine: "
        "chat, a code IDE, a Python/shell sandbox, image generation + editing, video studio, "
        "and a security lab.\n\n"
        "Right now I'm running the **Demo Brain**: no GGUF weights are installed, so replies "
        "come from built-in local templates instead of a model. Everything else — streaming, "
        "history, tools — is the real app.\n\n"
        "**To get real inference:** open *Models* in the top bar → **Download Brains** (or run "
        "`python download_models.py --recommended`). The smallest starter brain is ~0.8 GB and "
        "runs comfortably on a laptop."
    )


def _capabilities_reply() -> str:
    return (
        "### What I can do offline\n\n"
        "| Area | What runs locally |\n"
        "| --- | --- |\n"
        "| 💬 Chat | streaming replies, history, branching, export |\n"
        "| 🧪 Sandbox | Python execution + read-only shell |\n"
        "| 🛠️ IDE | file tree, editor, terminal, AI assist |\n"
        "| 🎨 Images | SSD gallery, editing, thumbnails |\n"
        "| 🛡️ Security | device audit, SAST scanner, CVE vault |\n"
        "| ⚡ Benchmark | TTFT / tok-per-sec measurement |\n\n"
        "Try *“write a Python script that scans a directory”*, *“explain mmap”*, "
        "*“remember that I use Python”*, or *“what is 17*31”*."
    )


def _fallback_reply(prompt: str, thinking_level: str) -> str:
    topic = _topic_of(prompt)
    core = prompt.strip().rstrip("?.!")[:160]
    detail = {
        "low": "Short version:",
        "mid": "Here's the practical breakdown:",
        "high": "Let's work through it properly:\n\n**1. Frame it.** Identify the actual constraint before choosing a tool.",
    }.get(thinking_level, "Here's the practical breakdown:")
    lines = [
        f"**Your question:** {core}",
        "",
        detail,
        "",
        f"- **Goal** — what a correct answer must satisfy for *{topic or 'this task'}*.",
        "- **Do the simple thing first** — the smallest working version, then measure.",
        "- **Check the failure modes** — bad input, missing permissions, timeouts, partial writes.",
        "- **Verify** — one assertion or one command that proves it works, not a vibe.",
    ]
    if thinking_level == "high":
        lines += [
            "",
            "**2. Compare options.** Make the trade-off explicit (speed vs. flexibility vs. cost) "
            "and pick the one that fails safest.",
            "",
            "**3. Ship small.** Land the change, watch it, then optimise where the data points.",
        ]
    lines += [
        "",
        "*Demo Brain: no GGUF model is installed, so this reply is template-generated locally. "
        "Install a brain (Models → Download) for real model answers.*",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main entry points
# --------------------------------------------------------------------------
def compose(prompt: str, language: str = "auto", thinking_level: str = "mid") -> str:
    """Build the deterministic demo reply for a prompt."""
    p = (prompt or "").strip()
    if not p:
        return "Send me a question and I'll answer from the local demo templates."

    low = p.lower()
    lowered = low.strip()

    if re.fullmatch(r"(hi|hey|hello|yo|good (morning|evening|afternoon))[\s!.]*", lowered):
        return (
            "Hey! **ARGON-OMEGA** here, running fully offline.\n\n"
            "Demo Brain is active right now (no GGUF installed), so I answer from local "
            "templates — everything else in the app is the real thing. Ask me for code, an "
            "explanation, a security check, or say *“remember that …”*."
        )
    if any(k in lowered for k in ("who are you", "what are you", "your name", "are you chatgpt", "which model")):
        return _identity_reply()
    if any(k in lowered for k in ("what can you do", "capabilities", "help me get started", "features")):
        return _capabilities_reply()

    mem = _memory_reply(p)
    if mem:
        return mem

    for needle, answer in _FACTS.items():
        if needle in lowered:
            return (
                f"**{answer}**\n\n"
                f"*({needle.title()} — answered from the offline Demo Brain's fact table. "
                "Install a GGUF model for full reasoning instead of lookups.)*"
            )

    math_reply = _math_reply(p)
    if math_reply:
        return math_reply

    if any(k in lowered for k in ("time is it", "current time", "what time", "today's date", "what date")):
        now = _dt.datetime.now()
        return (
            f"It's **{now:%H:%M:%S}** on **{now:%A, %d %B %Y}** (local system clock).\n\n"
            "I read this straight from the machine — no network call, no timezone guessing."
        )

    if any(k in lowered for k in ("sql injection", "sqli", "xss", "vulnerab", "security", "harden", "exploit")):
        return _security_reply(p)

    code_words = ("code", "script", "function", "write", "implement", "program", "fix", "bug", "refactor", "class")
    if re.search(r"\badd\s*\(\s*a", lowered) or ("add" in lowered and "function" in lowered):
        return (
            "Here's the function, with a self-check:\n\n"
            f"{_ADD_SNIPPET}\n\n"
            "It is typed, handles ints and floats, and the `assert`s make it self-verifying "
            "in the sandbox."
        )
    if any(k in lowered for k in code_words):
        if any(k in lowered for k in ("html", "web page", "website", "landing page", "css")):
            return (
                "Here's a self-contained page you can paste into **Sandbox** and run immediately:\n\n"
                f"{_HTML_SNIPPET}\n\n"
                "It's dependency-free (no CDN), so it renders offline exactly as shown."
            )
        if "javascript" in lowered or "node" in lowered:
            return (
                "```js\n"
                "// Dependency-free, works in the browser console or Node 18+\n"
                "async function mapLimit(items, limit, worker) {\n"
                "  const out = new Array(items.length);\n"
                "  let next = 0;\n"
                "  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {\n"
                "    while (next < items.length) {\n"
                "      const i = next++;\n"
                "      out[i] = await worker(items[i], i);\n"
                "    }\n"
                "  });\n"
                "  await Promise.all(runners);\n"
                "  return out;\n"
                "}\n\n"
                "await mapLimit([1, 2, 3, 4], 2, async n => n * 2);  // [2,4,6,8]\n"
                "```\n\n"
                "Bounded concurrency: at most `limit` workers in flight, results keep input order."
            )
        return _code_reply(p)

    if any(k in lowered for k in ("explain", "how does", "how do", "what is", "why is", "difference between", "teach")):
        return _explain_reply(p)

    return _fallback_reply(p, thinking_level)


def generate(prompt: str, language: str = "auto", thinking_level: str = "mid") -> dict:
    """Non-streaming demo completion."""
    started = time.perf_counter()
    reply = compose(prompt, language, thinking_level)
    tokens = max(1, len(_tokens(reply)))
    elapsed_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
    # Present plausible model-like timings for the UI badges.
    ttft_ms = round(random.uniform(28, 70), 1)
    total_ms = round(ttft_ms + (tokens / _SPEED_TOKENS_PER_SEC) * 1000.0, 1)
    return {
        "reply": reply,
        "latency_ms": total_ms,
        "first_token_ms": ttft_ms,
        "total_ms": total_ms,
        "tok_per_sec": round(tokens / (total_ms / 1000.0), 1),
        "ms_per_token": round(total_ms / tokens, 1),
        "source": "Argon Demo Brain (simulated)",
        "model_used": "demo-brain-1.0 (simulated)",
        "badge": badge(ttft_ms),
        "ms": total_ms,           # engine.answer_query compatibility
        "gear": "demo",
        "demo": True,
        "generated_ms": round(elapsed_ms, 2),
    }


def stream(prompt: str, language: str = "auto", thinking_level: str = "mid"):
    """Streaming demo completion, paced like a small local model."""
    reply = compose(prompt, language, thinking_level)
    parts = _tokens(reply)
    ttft_ms = round(random.uniform(28, 70), 1)
    yield {"type": "token", "token": "", "stage": "thinking"}
    started = time.perf_counter()
    # emulate prefill latency
    prefill = ttft_ms / 1000.0
    print_deadline = started + prefill

    while time.perf_counter() < print_deadline:
        time.sleep(min(0.02, print_deadline - time.perf_counter()))

    target_total = _sleep_pacing(len(parts), thinking_level)
    per_token = max(0.004, (target_total - prefill) / max(1, len(parts)))
    emitted = []
    try:
        for chunk in parts:
            emitted.append(chunk)
            yield {"type": "token", "token": chunk}
            time.sleep(per_token)
    except GeneratorExit:  # client aborted
        raise

    text = ''.join(emitted)
    total_ms = round((time.perf_counter() - started) * 1000.0, 1)
    toks = max(1, len(parts))
    yield {
        "type": "done",
        "reply": text,
        "ttft_ms": ttft_ms,
        "total_ms": total_ms,
        "tok_per_sec": round(toks / max(total_ms / 1000.0, 1e-6), 1),
        "badge": badge(ttft_ms, round(toks / max(total_ms / 1000.0, 1e-6), 1)),
        "source": "Argon Demo Brain (simulated)",
        "model_used": "demo-brain-1.0 (simulated)",
        "demo": True,
    }
