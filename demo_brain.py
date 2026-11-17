"""ARGON-OMEGA demo brain: real answers, no model weights.

Runs automatically whenever no GGUF brain is installed, so chat / streaming /
IDE assist / benchmarking work out of the box. This is *not* a language model:
it is a deterministic intent router with three honest sources of truth:

1. computations it actually performs (math, hashing, base64, unit conversion…),
2. a curated knowledge base of CS/OS/web facts (``_K``),
3. a library of small, runnable code recipes (``_R``).

Anything outside those is refused explicitly instead of being padded with
placeholder prose. Replies stream in multi-word chunks with **no artificial
pacing** — a template answer lands in single-digit milliseconds.

Environment:
    ARGON_DEMO_BRAIN=0     disable the demo brain entirely
    ARGON_DEMO_PACE=0.02   reintroduce per-chunk delay (seconds) for demos
"""

from __future__ import annotations

import ast
import base64
import datetime as _dt
import difflib
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict, deque

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "demo_memory.json")

VERSION = "demo-brain 2.0"

_LOCK = threading.Lock()
_RECENT: deque[dict] = deque(maxlen=8)
_CACHE: "OrderedDict[tuple, str]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 512

_METRICS = {"samples": 0, "ms": 0.0, "tokens": 0}

try:
    _PACE = max(0.0, float(os.environ.get("ARGON_DEMO_PACE", "0") or 0.0))
except ValueError:
    _PACE = 0.0

_CHUNK_WORDS = 6


# --------------------------------------------------------------------------
# availability / status
# --------------------------------------------------------------------------
def enabled() -> bool:
    return os.environ.get("ARGON_DEMO_BRAIN", "1").strip().lower() not in ("0", "false", "off", "no")


def _telemetry() -> dict:
    samples = _METRICS["samples"]
    if not samples:
        return {"latency_ms": 0.0, "tok_per_sec": 0.0, "samples": 0}
    ms = _METRICS["ms"] / samples
    toks = _METRICS["tokens"] / samples
    return {
        "latency_ms": round(ms, 2),
        "tok_per_sec": round(toks / max(ms / 1000.0, 1e-6), 1),
        "samples": samples,
    }


def _record(tokens: int, ms: float) -> None:
    with _LOCK:
        _METRICS["samples"] += 1
        _METRICS["ms"] += max(0.0, ms)
        _METRICS["tokens"] += max(0, tokens)


def status() -> dict:
    tel = _telemetry()
    return {
        "enabled": enabled(),
        "active": enabled(),
        "name": "Argon Demo Brain (template + retrieval)",
        "version": VERSION,
        "speed_tokens_per_sec": tel["tok_per_sec"],
        "latency_ms": tel["latency_ms"],
        "samples": tel["samples"],
        "memory_facts": len(_load_memory()),
        "knowledge_topics": len(_K),
        "code_recipes": len(_R),
        "note": "No GGUF installed — answers come from local computation, a curated knowledge base and code recipes.",
    }


def badge(ttft_ms: float = 0.0, tok_per_sec: float | None = None) -> str:
    tps = tok_per_sec if tok_per_sec is not None else 0.0
    if ttft_ms is None or ttft_ms < 1:
        speed = "under 1 ms"
    elif ttft_ms < 1000:
        speed = f"{ttft_ms:.1f} ms"
    else:
        speed = f"{ttft_ms / 1000.0:.2f} s"
    if tps and tps < 5000:
        return f"🧪 Demo Brain · no GGUF | ⚡ {speed} | 🚀 {tps:.0f} tok/s (measured)"
    return f"🧪 Demo Brain · no GGUF | ⚡ {speed} | template answer, not model output"


# --------------------------------------------------------------------------
# memory
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


def _chunks(text: str, size: int) -> list[str]:
    words = _tokens(text)
    return ["".join(words[i:i + size]) for i in range(0, len(words), size)] or [""]


def _safe_eval(expr: str) -> float | int | None:
    expr = expr.strip().rstrip("?.!=")
    if not re.fullmatch(r"[0-9\s+\-*/%.()^eE,]+", expr or ""):
        return None
    expr = expr.replace("^", "**").replace(",", "")
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
        value = eval(compile(tree, "<math>", "eval"), {"__builtins__": {}}, {})  # noqa: S307
    except Exception:
        return None
    if isinstance(value, float):
        if math.isclose(value, round(value)) and abs(value) < 1e15:
            value = int(round(value))
        else:
            value = round(value, 10)
    return value


def _fmt_num(value: float | int) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,}".rstrip("0").rstrip(".") if "." in f"{value:,}" else f"{value:,}"


# --------------------------------------------------------------------------
# code recipes — matched on what was actually asked
# --------------------------------------------------------------------------
_R: dict[str, dict] = {}


def _r(key: str, title: str, code: str, notes: str, lang: str = "python") -> None:
    _R[key] = {"title": title, "code": code.strip("\n"), "notes": notes, "lang": lang}


_r("add", "an `add()` function with self-checks", '''
def add(a: float, b: float) -> float:
    """Return a + b. Works for ints and floats; the asserts below self-verify."""
    return a + b


if __name__ == "__main__":
    assert add(2, 3) == 5
    assert add(-1.5, 1.5) == 0
    print(add(2, 3))          # 5
''', "Typed, dependency-free and self-verifying: the `assert`s run in the Sandbox and prove the function before you ship it.")

_r("reverse", "a `reverse()` function that flips a string", '''
def reverse(text: str) -> str:
    """Return text reversed. O(n) time, handles unicode code points."""
    return text[::-1]


print(reverse("ARGON"))          # "NOGRA"
print(reverse("stressed"))       # "desserts"
''', "Slicing with `[::-1]` is the idiomatic answer: it copies once and works on any sequence. For byte-level reversal (where combining marks matter) reverse grapheme clusters instead of code points.")

_r("palindrome", "a palindrome check that ignores case and punctuation", '''
import re


def is_palindrome(text: str) -> bool:
    """True for 'A man, a plan, a canal: Panama' — ignores case and punctuation."""
    cleaned = re.sub(r"[^a-z0-9]", "", text.lower())
    return cleaned == cleaned[::-1]


print(is_palindrome("A man, a plan, a canal: Panama"))   # True
print(is_palindrome("argon"))                            # False
''', "Normalise first (case + punctuation), then compare against the reverse. `cleaned == cleaned[::-1]` is O(n) time and O(n) space; a two-pointer scan is O(1) extra space.")

_r("fizzbuzz", "FizzBuzz", '''
def fizzbuzz(n: int) -> list[str]:
    out = []
    for i in range(1, n + 1):
        word = ("Fizz" if i % 3 == 0 else "") + ("Buzz" if i % 5 == 0 else "")
        out.append(word or str(i))
    return out


print(fizzbuzz(15))
# ['1', '2', 'Fizz', '4', 'Buzz', 'Fizz', '7', '8', 'Fizz', 'Buzz', '11', 'Fizz', '13', '14', 'FizzBuzz']
''', "Check 15 first if you write the branches explicitly — the classic bug is testing `% 3` before `% 15`, which prints \"Fizz\" for 15.")

_r("wordcount", "word and character statistics", '''
import re
from collections import Counter


def stats(text: str) -> dict:
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    return {
        "characters": len(text),
        "words": len(words),
        "lines": len(text.splitlines()),
        "unique_words": len(set(words)),
        "top": Counter(words).most_common(5),
    }


print(stats("the quick brown fox jumps over the lazy dog the end"))
''', "`split()` alone counts whitespace runs; a regex keeps contractions and numbers. `Counter.most_common()` gives the frequency table in one pass.")

_r("dedupe", "order-preserving dedupe", '''
def dedupe(items: list) -> list:
    """Order-preserving dedupe — works for hashable items."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def dedupe_unhashable(rows: list[dict], key: str) -> list[dict]:
    seen, out = set(), []
    for row in rows:
        if row[key] not in seen:
            seen.add(row[key])
            out.append(row)
    return out


print(dedupe([3, 1, 3, 2, 1]))          # [3, 1, 2]  (order kept)
''', "`list(set(x))` is faster but loses order and needs hashable items. The `seen` set keeps both correct.")

_r("sortdict", "sorting a dict by value", '''
prices = {"b": 9.0, "a": 12.5, "c": 3.25}

by_value = dict(sorted(prices.items(), key=lambda kv: kv[1]))
print(by_value)                                  # {'c': 3.25, 'b': 9.0, 'a': 12.5}

top_two = sorted(prices.items(), key=lambda kv: kv[1], reverse=True)[:2]
print(top_two)                                   # [('a', 12.5), ('b', 9.0)]
''', "`sorted()` on `.items()` with an explicit key is the clear version. Skip `key=dict.get` tricks — they read worse and behave the same.")

_r("binarysearch", "binary search with `bisect`", '''
from bisect import bisect_left


def binary_search(items: list[int], target: int) -> int:
    """Index of target in a sorted list, or -1. O(log n)."""
    i = bisect_left(items, target)
    return i if i < len(items) and items[i] == target else -1


data = [1, 3, 5, 7, 9, 11]
print(binary_search(data, 7))    # 3
print(binary_search(data, 8))    # -1
''', "`bisect` is implemented in C, so prefer it over a hand-written loop. The subtle part is the not-found case: check the index is in range *before* comparing.")

_r("quicksort", "quicksort", '''
def quicksort(items: list[int]) -> list[int]:
    if len(items) <= 1:
        return items
    pivot = items[len(items) // 2]
    left = [x for x in items if x < pivot]
    mid = [x for x in items if x == pivot]
    right = [x for x in items if x > pivot]
    return quicksort(left) + mid + quicksort(right)


print(quicksort([5, 3, 9, 1, 5]))    # [1, 3, 5, 5, 9]
''', "Readable, average O(n log n). It allocates three lists per level; in production use `sorted()` (Timsort, stable) or an in-place partition when memory matters.")

_r("primes", "a prime sieve", '''
def primes_below(limit: int) -> list[int]:
    """Sieve of Eratosthenes — O(n log log n)."""
    if limit < 2:
        return []
    sieve = bytearray([1]) * limit
    sieve[0:2] = b"\\x00\\x00"
    for n in range(2, int(limit ** 0.5) + 1):
        if sieve[n]:
            sieve[n * n::n] = bytearray(len(range(n * n, limit, n)))
    return [i for i, flag in enumerate(sieve) if flag]


print(primes_below(50))
# [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]
''', "The sieve marks composites in slices, so it is fast in Python. Trial division per number is the simple-but-slow alternative.")

_r("fibonacci", "Fibonacci, iterative and memoised", '''
from functools import lru_cache


def fib(n: int) -> int:
    """Iterative — O(n) time, O(1) memory."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


@lru_cache(maxsize=None)
def fib_rec(n: int) -> int:
    """Memoised recursion — same result, shows the cache in action."""
    return n if n < 2 else fib_rec(n - 1) + fib_rec(n - 2)


print([fib(i) for i in range(10)])   # [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
print(fib(90))                       # 2880067194370816120
''', "Never use naive recursion here: it is O(2^n). The iterative loop is the answer you want in an interview; `lru_cache` is the answer when you need many lookups.")

_r("an exact factorial", "factorial", '''
import math


def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("factorial is undefined for negative integers")
    return math.factorial(n)          # C implementation, exact big ints


print(factorial(20))                  # 2432902008176640000
''', "`math.factorial` is exact and fast for big integers. The recursive definition overflows the stack around a few thousand; the loop does not.")

_r("fileio", "an atomic file write", '''
from pathlib import Path
import tempfile
import os


def write_atomic(path: str, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    A crash mid-write leaves the old file intact instead of a truncated one.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


write_atomic("out/notes.txt", "hello\\n")
print(Path("out/notes.txt").read_text(encoding="utf-8"))
''', "`os.replace` is atomic on the same filesystem, which is why the temp file must live next to the target. Always pass `encoding=\"utf-8\"` or you inherit the platform default.")

_r("retry", "a retry decorator with backoff", '''
import functools
import random
import time


def retry(times: int = 4, base: float = 0.2, exceptions=(Exception,)):
    """Retry a callable with jittered exponential backoff."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except exceptions:
                    if attempt == times - 1:
                        raise
                    time.sleep(base * 2 ** attempt + random.uniform(0, base))
        return wrapper
    return decorator


@retry(times=3, exceptions=(TimeoutError,))
def fetch():
    raise TimeoutError("upstream slow")

try:
    fetch()
except TimeoutError as exc:
    print("gave up after retries:", exc)
''', "Jitter matters: without it every client retries in lockstep and hammers the recovering service. Only retry idempotent work.")

_r("argparse", "a CLI built with argparse", '''
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Resize images in a folder.")
    parser.add_argument("folder", help="directory to scan")
    parser.add_argument("--width", type=int, default=1024, help="target width in px")
    parser.add_argument("--dry-run", action="store_true", help="print what would change")
    args = parser.parse_args()

    print(f"folder={args.folder} width={args.width} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
''', "`argparse` gives you `--help`, type conversion and error messages for free. Exit code 2 on bad usage is conventional.")

_r("sqlite", "SQLite storage with bound parameters", '''
import sqlite3


def connect(path: str = "app.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


with connect() as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id      INTEGER PRIMARY KEY,
            body    TEXT NOT NULL,
            created TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
    conn.execute("INSERT INTO notes (body) VALUES (?)", ("first note",))   # bound!
    for row in conn.execute("SELECT id, body FROM notes ORDER BY id DESC"):
        print(dict(row))
''', "Always bind parameters (`?`) — never build SQL with f-strings. `with conn:` commits on success and rolls back on exception; it does not close the connection.")

_r("unittest", "a unit-test skeleton", '''
import unittest


def parse_range(text: str) -> tuple[int, int]:
    """'1-5' -> (1, 5);  '7' -> (7, 7).  Raises ValueError on junk."""
    low, sep, high = text.partition("-")
    if not sep:
        value = int(low)
        return value, value
    return int(low), int(high)


class TestParseRange(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_range("1-5"), (1, 5))

    def test_single_value(self):
        self.assertEqual(parse_range("7"), (7, 7))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            parse_range("abc")


if __name__ == "__main__":
    unittest.main()
''', "One behaviour per test, name the case, and always include the failure path. `pytest` runs this file unchanged.")

_r("dataclass", "a frozen, slot-based dataclass", '''
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True, slots=True)
class Task:
    title: str
    done: bool = False
    tags: list[str] = field(default_factory=list)

    def finish(self) -> "Task":
        return Task(self.title, True, self.tags)     # frozen → return a copy


t = Task("ship it", tags=["release"])
print(t.finish())
print(asdict(t))
''', "`slots=True` removes the per-instance `__dict__` (less memory), `frozen=True` makes it hashable and safe to share. Never use a mutable default — use `default_factory`.")

_r("asyncio", "bounded async concurrency", '''
import asyncio


async def fetch(url: str) -> tuple[str, int]:
    await asyncio.sleep(0.1)                 # stand-in for real I/O
    return url, len(url)


async def main() -> None:
    urls = [f"https://example.com/{i}" for i in range(6)]
    gate = asyncio.Semaphore(3)              # at most 3 in flight

    async def one(url):
        async with gate:
            return await fetch(url)

    results = await asyncio.gather(*(one(u) for u in urls))
    print(results)


asyncio.run(main())
''', "`gather` alone launches everything at once. The `Semaphore` is what actually bounds concurrency — important when the far side rate-limits you.")

_r("httpreq", "an HTTP GET with a timeout", '''
import json
import urllib.request


def get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "argon-omega", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:     # always a timeout
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


try:
    data = get_json("https://example.com/api/items")
    print(len(data))
except Exception as exc:                    # URLError, HTTPError, TimeoutError, JSONDecodeError
    print("request failed:", exc)
''', "A timeout is mandatory — the default is *infinite*. Decode with the charset the server sends, and catch broad exceptions at the call site, not inside the helper.")

_r("validation", "email and URL validation", '''
import re

EMAIL = re.compile(r"^[^@\\s]+@[^@\\s.]+(\\.[^@\\s.]+)+$")
URL = re.compile(r"^https?://[^\\s/$.?#][^\\s]*$", re.IGNORECASE)


def valid_email(value: str) -> bool:
    return bool(EMAIL.match(value.strip()))


print(valid_email("dev@example.com"))    # True
print(valid_email("dev@example"))        # False
print(bool(URL.match("https://example.com/a?b=1")))   # True
''', "A regex is a shape check, not proof. The only real test for an address is delivery (send a confirmation mail); for URLs, resolve them. Reject length > 254 and control characters too.")

_r("class", "a small class with real invariants", '''
class Counter:
    """Minimal, testable example class."""

    def __init__(self, start: int = 0) -> None:
        self._value = start

    def increment(self, by: int = 1) -> int:
        if by < 0:
            raise ValueError("by must be >= 0")
        self._value += by
        return self._value

    @property
    def value(self) -> int:
        return self._value

    def __repr__(self) -> str:
        return f"Counter({self._value})"


c = Counter()
c.increment(4)
print(c)          # Counter(4)
''', "Private-by-convention `_value`, read-only `@property`, validation at the boundary and a `__repr__` for debugging. Small enough to unit test in one screen.")

_r("json", "a JSON read-modify-write", '''
import json
from pathlib import Path

path = Path("config.json")
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
data["debug"] = True
path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
print(path.read_text(encoding="utf-8"))
''', "`json.dumps(indent=2, sort_keys=True)` keeps diffs stable in version control. For big files stream with `ijson`; `json.load` holds everything in memory.")

_r("csv", "CSV parsing into dicts", '''
import csv

with open("data.csv", newline="", encoding="utf-8") as fh:
    reader = csv.DictReader(fh)
    rows = [{"name": r["name"], "score": float(r["score"])} for r in reader]

rows.sort(key=lambda r: r["score"], reverse=True)
print(rows[:3])
''', "`newline=\"\"` is required or Windows line endings produce blank rows. `DictReader` keys off the header row, so column order stops mattering.")

_r("html", "a self-contained HTML page", '''
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
    }
    button {
      margin-top: 14px; padding: 10px 18px; border-radius: 10px; cursor: pointer;
      border: 1px solid #10b981; background: #10b981; color: #04120c; font-weight: 700;
    }
  </style>
</head>
<body>
  <main class="card">
    <h1>Hello from ARGON-OMEGA</h1>
    <p id="out">Click the button to run the inline script.</p>
    <button onclick="document.getElementById('out').textContent = 'JS is working'">Run script</button>
  </main>
</body>
</html>
''', "Dependency-free, so it renders offline exactly as shown. Paste it into Sandbox and press Run to see the live preview.", lang="html")

_r("jsfetch", "bounded concurrency in JavaScript", '''
async function mapLimit(items, limit, worker) {
  const out = new Array(items.length);
  let next = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await worker(items[i], i);
    }
  });
  await Promise.all(runners);
  return out;
}

const doubled = await mapLimit([1, 2, 3, 4], 2, async n => n * 2);
console.log(doubled);   // [2, 4, 6, 8]
''', "At most `limit` workers in flight and results keep input order. `Promise.all` over everything is the trap — it opens one connection per item.", lang="javascript")

_r("debounce", "a JavaScript `debounce()`", '''
function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const onSearch = debounce((term) => console.log("searching", term), 300);
onSearch("a"); onSearch("ar"); onSearch("argon");   // only "argon" fires
''', "The timer resets on every call, so only the last call in a burst runs. `clearTimeout` is what makes it debounce rather than delay; for throttling, keep the first timestamp instead.", lang="javascript")

_r("jsserver", "a minimal Node HTTP server", '''
import { createServer } from "node:http";

const server = createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ ok: true }));
  }
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found");
});

server.listen(3000, () => console.log("http://localhost:3000"));
''', "No framework needed for a health check. Calling `res.end` returns, so the handler always closes the response exactly once.", lang="javascript")

_r("directory", "a large-file scanner", '''
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


for path, mb in scan_large_files("~", 250)[:10]:
    print(f"{mb:9.1f} MB  {path}")
''', "Skips unreadable files instead of dying, and sorts once at the end. On huge trees prefer `os.scandir` and prune known-heavy directories.")

_r("flatten", "flattening arbitrarily nested lists", '''
def flatten(items):
    """Flatten arbitrarily nested lists/tuples, keeping other items as-is."""
    for item in items:
        if isinstance(item, (list, tuple)):
            yield from flatten(item)
        else:
            yield item


print(list(flatten([1, [2, [3, 4]], 5])))   # [1, 2, 3, 4, 5]
''', "A generator avoids building intermediate lists and handles any depth. Strings are deliberately not treated as iterables here.")

_r("groupby", "grouping records by key", '''
from collections import defaultdict
from itertools import groupby

rows = [
    {"team": "red", "points": 3},
    {"team": "blue", "points": 7},
    {"team": "red", "points": 5},
]

buckets = defaultdict(list)
for row in rows:
    buckets[row["team"]].append(row["points"])
print(dict(buckets))          # {'red': [3, 5], 'blue': [7]}

totals = {team: sum(pts) for team, pts in buckets.items()}
print(totals)                 # {'red': 8, 'blue': 7}
''', "`defaultdict(list)` is the one-pass version. `itertools.groupby` only groups *adjacent* items, so sort by the key first if you use it.")


# --------------------------------------------------------------------------
# knowledge base — curated, factual, no filler
# --------------------------------------------------------------------------
_K: dict[str, dict] = {}


def _k(key: str, title: str, summary: str, points: list[str], gotcha: str = "") -> None:
    _K[key] = {"title": title, "summary": summary, "points": points, "gotcha": gotcha}


_k("deadlock", "Deadlock",
   "A deadlock is a state where two or more threads each hold a resource the other needs, so none of them can ever make progress.",
   ["Four conditions must hold at once (Coffman): mutual exclusion, hold-and-wait, no preemption, circular wait. Breaking any one makes deadlock impossible.",
    "The practical fix is a global lock order: rank every lock and always acquire them in ascending order, which removes circular wait by construction.",
    "Acquire with a timeout as a backstop — a failed acquire you can log and retry beats a process that hangs forever.",
    "Prefer not holding two locks at once: copy the data you need out of a locked section, then release before taking the next."],
   "It looks like slowness, not an error: CPU near zero, nothing in the log. `py-spy dump --pid N` (or jstack) shows threads parked on `acquire` — that is how you confirm it rather than guess.")

_k("race-condition", "Race condition",
   "A race condition means the result depends on the order in which concurrent operations happen, so it can be correct in testing and wrong in production.",
   ["The bug is a read-modify-write that is not atomic: read a counter, compute, write it back — another thread can slip in between.",
    "Fix at the right level: a lock around the update, an atomic operation (`+=` with `threading.Lock`, `sqlite3` transaction, Redis `INCR`), or a queue that serialises work.",
    "Make shared state immutable where possible — if nothing mutates, there is nothing to race.",
    "Test with contention: many threads, a tiny `sleep` inside the critical section, repeated runs."],
   "Adding a sleep to \"fix\" timing hides the race. Under load or on another core count it returns.")

_k("mutex", "Mutex vs semaphore",
   "A mutex is a lock with ownership (one holder, released by that holder); a semaphore is a counter that allows N concurrent holders and has no owner.",
   ["Mutex: mutual exclusion for a critical section. Only the thread that acquired it should release it.",
    "Semaphore: limit concurrency — `Semaphore(4)` allows four holders, useful for connection pools and rate limiting.",
    "Python's `threading.Lock` is a mutex; `threading.Semaphore`/`BoundedSemaphore` are semaphores. `BoundedSemaphore` catches over-release bugs.",
    "Because a mutex has an owner, it can be re-entered (with `RLock`) or priority-inherited; a semaphore has no such concept."])

_k("process-thread", "Process vs thread",
   "Every process has its own address space; threads share one address space inside a process, so they communicate cheaply but can corrupt each other's state.",
   ["Processes are isolated: a crash or memory leak stays contained, but IPC (pipes, sockets, shared memory) costs serialisation.",
    "Threads share memory: fast hand-off, but you need locks, and one unhandled crash can take the process down.",
    "Python: threads suit I/O (the GIL is released while waiting); CPU-bound work needs processes (`concurrent.futures.ProcessPoolExecutor`).",
    "Rule of thumb: processes for isolation and CPU, threads (or async) for concurrency inside one job."])

_k("gil", "The Global Interpreter Lock",
   "CPython's GIL lets only one thread execute Python bytecode at a time, which is why threads do not speed up CPU-bound Python.",
   ["The GIL exists to keep reference counting safe without a lock per object; it is released during blocking I/O and inside many C extensions (NumPy, hashlib, zlib).",
    "So threads still help for network/file I/O, and C extensions can run in parallel — pure-Python loops cannot.",
    "Use processes (`ProcessPoolExecutor`), native extensions, or vectorised array code for CPU parallelism.",
    "Free-threaded CPython builds (3.13+) remove the GIL, but extension compatibility and single-thread speed are still catching up."])

_k("async", "async / await and the event loop",
   "An event loop runs one thread and multiplexes many pending operations; `await` marks a point where the task yields control instead of blocking the thread.",
   ["It is cooperative: a coroutine that never awaits blocks the entire loop — every other task stalls.",
    "Async wins on I/O-heavy fan-out (thousands of sockets); it does nothing for CPU-bound work, which must go to a thread or process pool.",
    "In Python, blocking calls inside `async def` need `asyncio.to_thread(...)`; in JS the equivalent trap is a long synchronous loop.",
    "Concurrency limits belong in the code (`Semaphore`), not in hope."])

_k("recursion", "Recursion",
   "A recursive function solves a problem by calling itself on a smaller input, with a base case that stops the descent.",
   ["Every recursive solution needs two things: a base case and a strictly smaller subproblem. Miss either and you get a stack overflow.",
    "Python's default recursion limit is 1000 frames and each frame is expensive; deep recursion wants an explicit stack or an iterative rewrite.",
    "Tail calls are not optimised in Python — write the loop.",
    "Memoisation (`functools.lru_cache`) turns exponential recursive definitions (naive Fibonacci) into linear ones."])

_k("big-o", "Big-O complexity",
   "Big-O describes how work grows with input size, ignoring constants: O(1) constant, O(log n) binary search, O(n) one pass, O(n log n) good sorts, O(n²) nested loops.",
   ["It is about the growth rate, not speed: an O(n²) algorithm can beat O(n log n) at tiny n.",
    "Nested loops over the same list are usually O(n²); a set or dict lookup inside one loop makes it O(n) expected.",
    "Space complexity matters just as much — building three lists per recursion level is still O(n) memory.",
    "Measure with `time.perf_counter()` on realistic sizes before optimising; complexity tells you where the wall is, not where the time goes."])

_k("hash-table", "Hash table",
   "A hash table maps keys to values by hashing the key to a bucket, giving O(1) average lookup, insert and delete.",
   ["Worst case is O(n) when everything collides — that is why adversarial input can DoS a server (hash flooding).",
    "Keys must be hashable and, in practice, immutable: mutating a key after insertion loses the entry.",
    "Python `dict` preserves insertion order and resizes at ~2/3 load; `set` is the same structure without values.",
    "Iteration order of a `set` is not defined — sort before displaying anything user-visible."])

_k("index", "Database indexes",
   "An index is a sorted side structure (usually a B-tree) that turns a table scan into a lookup, taking a query from O(n) to O(log n).",
   ["Index the columns you filter and join on, in the order the query uses them: a composite index on (user_id, created_at) also serves user_id alone, but not created_at alone.",
    "Every index costs write throughput and disk, so unused indexes are pure tax.",
    "Check the plan (`EXPLAIN QUERY PLAN` in SQLite, `EXPLAIN ANALYZE` in Postgres) before and after — guesses are usually wrong.",
    "Functions on the column (`WHERE lower(email) = ?`) disable a plain index unless you add an expression index."])

_k("acid", "ACID transactions",
   "ACID is atomicity (all or nothing), consistency (constraints hold), isolation (concurrent transactions do not see each other's partial work) and durability (committed data survives a crash).",
   ["Isolation has levels: read committed (default in Postgres), repeatable read, serialisable. Each stronger level costs concurrency.",
    "Long transactions hold locks and bloat the WAL — keep them short and never wait on user input inside one.",
    "In SQLite, one writer at a time; WAL mode lets readers proceed during writes.",
    "Durability is a dial: `PRAGMA synchronous=NORMAL` is faster but can lose the last commits on power loss."])

_k("nplusone", "The N+1 query problem",
   "N+1 means one query fetches N rows and then N more queries fetch each row's related data — the classic cause of a page that is fine with 10 rows and dead with 10,000.",
   ["Fix by fetching the relation up front: a join, `selectinload`/`joinedload` in SQLAlchemy, or one `WHERE id IN (...)` batch.",
    "It is invisible on small dev data; use a query counter or log every statement while testing.",
    "Batching also fixes the latency shape: 2 round trips instead of 1+N regardless of row count."])

_k("caching", "Caching",
   "Caching trades freshness and memory for speed: keep the expensive result next to the code that would recompute it.",
   ["The hard parts are the key and invalidation, not the storage. A key missing an input (user, locale, filter) is a correctness bug, not a perf one.",
    "Prefer TTL + explicit invalidation on write; always keep a cold path that works when the cache is empty.",
    "Watch the stampede: when a hot key expires, thousands of requests recompute at once — add a lock or serve stale-while-revalidate.",
    "Measure hit rate. A 30% hit rate is often not worth the complexity."])

_k("http", "HTTP",
   "HTTP is a stateless request/response protocol: a method and path, headers, an optional body, then a status line and body back.",
   ["Methods: GET (safe, cacheable), POST (create, not idempotent), PUT (replace, idempotent), PATCH (partial), DELETE (idempotent).",
    "Status families: 2xx success, 3xx redirect/cache, 4xx client error (400 malformed, 401 unauthenticated, 403 forbidden, 404 missing, 409 conflict, 429 rate-limited), 5xx server error.",
    "Keep-alive and HTTP/2 multiplexing matter more than raw bandwidth on high-latency links; a new TCP+TLS handshake per request is the real cost.",
    "Stateless means identity travels in cookies or tokens on every request."])

_k("tcp-udp", "TCP vs UDP",
   "TCP is an ordered, reliable byte stream with retransmission and congestion control; UDP is unordered, unreliable datagrams with no connection.",
   ["TCP pays for reliability with head-of-line blocking: one lost packet delays everything behind it.",
    "UDP is the right choice for telemetry, voice/video and games where a late packet is useless; QUIC builds reliability on top of UDP to avoid TCP's blocking.",
    "Both are framed by IP; ports identify the process. Firewalls and NAT treat them differently."])

_k("tls", "HTTPS / TLS",
   "TLS gives you confidentiality, integrity and authentication for a connection: the client validates the server's certificate, then both derive symmetric session keys.",
   ["The asymmetric handshake only exchanges a key; the bulk data uses fast symmetric ciphers (AES-GCM or ChaCha20-Poly1305).",
    "Certificates chain to a trusted root; expiry, wrong hostname and missing intermediates are the three most common failures.",
    "Modern setup is TLS 1.3, HSTS, and no mixed content — one `http://` asset breaks the padlock and the browser blocks it.",
    "TLS protects the wire, not the endpoint: secrets still must not be logged."])

_k("dns", "DNS",
   "DNS resolves names to addresses through a hierarchy: browser cache → OS cache → recursive resolver → root → TLD → authoritative server.",
   ["Records you actually touch: A/AAAA (address), CNAME (alias), MX (mail), TXT (verification/SPF), NS (delegation), CAA (who may issue certs).",
    "TTL controls how long answers are cached, and lowering it takes effect only after the old TTL expires — plan ahead for migrations.",
    "Propagation is really cache expiry; `dig +trace name` shows each hop and where an answer actually comes from."])

_k("cors", "CORS",
   "CORS is a browser rule, not a server security feature: a response must explicitly allow a cross-origin caller, otherwise the browser hides the body from JavaScript.",
   ["Same-origin means same scheme, host and port. Anything else needs `Access-Control-Allow-Origin`.",
    "Non-simple requests trigger a preflight `OPTIONS`; the server must answer with allowed methods and headers.",
    "`Access-Control-Allow-Origin: *` cannot be combined with credentials — echo the exact origin instead and validate it against an allowlist.",
    "curl and server-to-server calls ignore CORS entirely, which is why \"it works in Postman\" proves nothing about the browser."])

_k("jwt", "JWT",
   "A JWT is a signed, base64url-encoded claim set: header.payload.signature. The signature proves integrity; the payload is readable by anyone.",
   ["Never put secrets in the payload. It is encoded, not encrypted.",
    "Verify the signature with the right algorithm and reject `alg: none`; pin the algorithm server-side.",
    "JWTs are stateless, so revocation is the weak spot: short lifetimes plus a refresh token or a server-side denylist.",
    "Store them in `HttpOnly; Secure; SameSite` cookies rather than `localStorage`, which any XSS can read."])

_k("rest-graphql", "REST vs GraphQL",
   "REST exposes resources over HTTP verbs; GraphQL exposes one endpoint with a typed query language the client shapes.",
   ["REST is cacheable per URL and simple to reason about; it over- or under-fetches when clients need different shapes.",
    "GraphQL fixes over-fetching but moves complexity server-side: resolver batching (DataLoader), query depth/cost limits, and caching get harder.",
    "Neither is inherently secure — both need authz per field/resource, and GraphQL needs introspection disabled in production."])

_k("hash-encrypt", "Hashing vs encryption",
   "Hashing is one-way (verify by recomputing); encryption is reversible with a key (protect and later read).",
   ["Passwords: hash with a slow, salted KDF — Argon2id, scrypt or bcrypt. Never SHA-256 alone, never unsalted.",
    "Data at rest: symmetric encryption (AES-GCM). Key exchange and signatures: asymmetric (RSA, ECDSA, Ed25519).",
    "A hash proves integrity, a MAC (HMAC) proves integrity plus key possession, a signature adds non-repudiation.",
    "Encryption without authentication (AES-CBC without MAC) is malleable — prefer AEAD modes."])

_k("container", "Containers vs virtual machines",
   "A container shares the host kernel and isolates processes with namespaces and cgroups; a VM runs a full guest kernel on a hypervisor.",
   ["Containers start in milliseconds and are small, but the kernel is shared: an escape is serious, so run untrusted code in a VM or sandbox.",
    "VMs isolate strongly and can run a different OS, at the cost of memory and boot time.",
    "Reproducibility comes from the image, not the runtime: pin base image digests and build deterministically."])

_k("docker", "Docker",
   "Docker packages an application and its dependencies into an image; a container is a running instance of that image.",
   ["Layer order is the whole game for build speed: copy dependency manifests and install before copying source.",
    "Use a small runtime base (slim/alpine), run as a non-root user, and never bake secrets into layers — they stay in the history.",
    "`docker compose` describes multi-service local setups; production orchestration belongs to Kubernetes or a managed runtime."])

_k("kubernetes", "Kubernetes",
   "Kubernetes schedules containers onto nodes and keeps the declared state true: it restarts crashed pods, reschedules dead nodes' work, and exposes stable Services in front of moving pods.",
   ["Core objects: Pod (one or more containers), Deployment (replica management + rollout), Service (stable virtual IP/DNS), Ingress (HTTP routing), ConfigMap/Secret.",
    "Set requests and limits, plus liveness/readiness probes — without them the scheduler guesses and traffic hits unready pods.",
    "Stateful things (databases) need StatefulSets and real volumes; most teams are happier with a managed database.",
    "Debug order: `kubectl get pods` → `describe` (events) → `logs --previous` → `exec`."])

_k("git-rebase", "git merge vs rebase",
   "Merge keeps history as it happened and adds a merge commit; rebase replays your commits onto a new base for a linear history with new hashes.",
   ["Rebase only private branches. Rewriting a shared branch forces everyone to recover, which is why it needs `--force-with-lease` and coordination.",
    "Merge is the honest record of integration; rebase is for tidying before you share.",
    "`git pull --rebase` avoids noisy merge commits in local-sync workflows.",
    "Conflicts are resolved the same way in both — the difference is how many times and on which commits."])

_k("cid", "CI/CD",
   "CI builds and tests every change automatically; CD ships the result — continuously (every green commit) or on demand, with the same artifact promoted between stages.",
   ["Keep the pipeline fast: parallel jobs, cached dependencies, and a smoke test before the full suite.",
    "Build once, promote the artifact. Rebuilding per environment reintroduces the drift CI exists to remove.",
    "Gate merges on required checks, and make flaky tests a bug with an owner, not a retry button.",
    "Rollbacks must be boring: versioned artifacts plus a documented revert path."])

_k("microservices", "Microservices vs monolith",
   "A monolith is one deployable unit; microservices are many small services owned independently and talking over the network.",
   ["Services trade in-process calls for network calls: latency, partial failure, retries, and distributed transactions.",
    "Split by business capability and data ownership, not by technical layer, and only when team scale demands it.",
    "Start with a modular monolith — clear module boundaries and one database per module — then extract the pieces that actually hurt.",
    "Observability (traces, correlation IDs) is not optional once a request crosses three services."])

_k("queue", "Message queues",
   "A queue decouples producers from consumers: the producer writes a message and moves on, the consumer processes it later, possibly retried or scaled independently.",
   ["Guarantees vary: at-most-once (may drop), at-least-once (may duplicate), exactly-once (effectively once per key, and expensive).",
    "Design consumers to be idempotent — with at-least-once delivery, duplicates are normal, not exceptional.",
    "Dead-letter queues and retry limits stop one poison message from blocking the stream forever.",
    "Ordering is per partition/queue, not global, in Kafka/SQS-style systems."])

_k("embeddings", "Embeddings",
   "An embedding maps text, images or audio to a vector so that similar meaning lands close together in space; search then becomes nearest-neighbour lookup.",
   ["Similarity is usually cosine distance; normalise vectors to make it a dot product.",
    "The model that produced the vectors must be the same one that produces queries — mixing them silently destroys quality.",
    "Chunk sensibly (a few hundred tokens with overlap), keep metadata for filtering, and store ids next to vectors.",
    "Brute force is fine to ~100k vectors; beyond that use an ANN index (HNSW/IVF) and accept approximate recall."])

_k("rag", "Retrieval-augmented generation (RAG)",
   "RAG retrieves relevant documents with a search step and puts them in the model's context, so answers are grounded in your data instead of the model's memory.",
   ["Pipeline: chunk → embed → store → embed the question → retrieve top-k → rerank → build a prompt with citations.",
    "Most failures are retrieval failures, not model failures: bad chunking, missing metadata filters, or top-k too small.",
    "Give the model an explicit escape hatch (\"say you don't know\") or it will fill gaps confidently.",
    "Evaluate retrieval separately (recall@k) from generation (faithfulness) — otherwise you cannot tell what to fix."])

_k("quantization", "Quantization",
   "Weights are stored at lower precision — typically 4-bit blocks with per-block scales — instead of 16-bit floats, cutting size and bandwidth roughly 3–4x.",
   ["A 7B model drops from ~14 GB (fp16) to ~4.7 GB at Q4_K_M, which is the difference between \"needs a GPU\" and \"runs on a laptop\".",
    "K-quants keep outlier weights at higher precision, which is why Q4_K_M often beats naive Q4_0 at a similar size.",
    "Expect small quality loss, mostly on long reasoning chains and rare languages; measure on your own prompts.",
    "Quantized weights still need dequantization per matmul — the speed win comes from memory bandwidth, not arithmetic."])

_k("gguf", "The GGUF format",
   "GGUF is a single-file container: header, key/value metadata block, tensor-info table, then 32-byte-aligned tensor data.",
   ["Metadata is read first, so a loader can pick the right backend and allocate before touching weights.",
    "Quantised blocks store their scales inline next to the values, which keeps each tensor self-describing.",
    "mmap means weights are never fully copied into RAM: cold pages come from disk, hot pages stay in the page cache, and processes can share them.",
    "Version churn is real — check that the loader supports the file's GGUF version before downloading 40 GB."])

_k("mmap", "Memory mapping",
   "`mmap()` maps a file into the process address space so the OS pages it in on demand instead of copying it into RAM up front.",
   ["llama.cpp mmaps GGUF weights, so startup is fast and several processes share the same physical pages.",
    "First access is slow (page faults); subsequent access hits the page cache.",
    "Writes need `MAP_SHARED` to be visible to other processes, and `msync`/`fsync` for durability.",
    "On systems with little free RAM the kernel evicts pages continuously — which is why a model that \"fits\" can still thrash."])

_k("tokenization", "Tokenization",
   "Text is split into sub-word tokens (BPE/WordPiece) before the model sees it; English averages about 4 characters per token.",
   ["Code, URLs, numbers and non-Latin scripts use far more tokens for the same text, so they consume context faster and cost more.",
    "Context limits and pricing are counted in tokens, not words — estimate with a tokenizer, never with `len(text)/4` for code.",
    "Special tokens (BOS/EOS, chat template markers) are added by the tokenizer; hand-built prompts can silently miss them.",
    "A token is not a word and not a character: truncating mid-token produces mojibake."])

_k("transformer", "Transformers and attention",
   "A transformer layer mixes tokens with self-attention (every token weighs every other token) followed by a feed-forward block, with residual connections and normalisation.",
   ["Attention is what makes context global; the cost grows quadratically with sequence length, which is why long context is expensive.",
    "KV caching stores past keys/values during generation so each new token costs one step instead of a full re-read.",
    "Multi-head attention lets the model track several relationships at once; positional encodings (RoPE) supply order.",
    "Most of the parameters live in the feed-forward layers, not the attention matrices."])

_k("gc", "Garbage collection",
   "A garbage collector reclaims memory that is no longer reachable from roots (stack, globals, registers) so you do not have to free it manually.",
   ["Python uses reference counting plus a cyclic collector for cycles; Go and Java use tracing collectors with generational heaps.",
    "GC pauses come from scanning and moving memory — large heaps and high allocation rates make them longer.",
    "Fewer, longer-lived allocations beat many short ones: reuse buffers, avoid building giant intermediate lists.",
    "`gc.get_stats()` / `-Xlog:gc` show whether GC is actually your bottleneck before you tune anything."])

_k("memory-leak", "Memory leaks",
   "A leak is memory that stays reachable but will never be used again — caches without bounds, listeners never removed, cycles held by globals.",
   ["Measure first: `tracemalloc` (Python), heap snapshots (Node), `pprof` (Go). A leak is a growth curve across repeated identical work.",
    "Common Python causes: module-level dicts used as caches, closures capturing large objects, `functools.lru_cache` with `maxsize=None`, and unjoined threads.",
    "Weak references (`weakref`, `WeakValueDictionary`) let caches hold objects without keeping them alive.",
    "Cap every cache. Unbounded is a leak with extra steps."])

_k("stack-heap", "Stack vs heap",
   "The stack holds call frames and local values with automatic lifetime; the heap holds dynamically allocated objects with manual or GC-managed lifetime.",
   ["Stack allocation is a pointer bump and is cache-friendly; heap allocation has bookkeeping and fragmentation costs.",
    "Deep recursion fills the stack (a few hundred KB to a few MB); large objects always live on the heap.",
    "In Python every object is heap-allocated and the \"stack\" is an interpreter-managed frame chain.",
    "Escape analysis in Go/Rust lets the compiler keep some \"heap-looking\" values on the stack."])

_k("dynamic-programming", "Dynamic programming",
   "DP solves a problem by combining solutions to overlapping subproblems, storing each result once — memoisation (top-down) or tabulation (bottom-up).",
   ["You need two properties: optimal substructure and overlapping subproblems. Without overlap, plain recursion is already fine.",
    "Write the recurrence first, then decide the storage: a dict for sparse states, an array for dense ones.",
    "Space optimisation usually follows: many DPs only need the previous row.",
    "Classic examples: Fibonacci, knapsack, edit distance, longest common subsequence, coin change."])

_k("oop", "Object-oriented programming",
   "OOP groups state with the behaviour that owns it, and lets you substitute implementations behind a shared interface.",
   ["The four words: encapsulation (hide internals), abstraction (expose intent), inheritance (reuse a type), polymorphism (one call, many implementations).",
    "Prefer composition over deep inheritance — hierarchies calcify, composition adapts.",
    "Small objects with clear invariants beat god objects; if a method needs a comment explaining which fields it mutates, the boundary is wrong.",
    "Python is duck-typed: protocols and `typing.Protocol` give you interfaces without a base class."])

_k("testing", "Testing strategy",
   "The pyramid: many fast unit tests, fewer integration tests across real boundaries, a handful of end-to-end tests for critical journeys.",
   ["Test behaviour, not implementation — tests that assert on private internals fail on every refactor and prove nothing.",
    "Every bug fix starts with a failing test that reproduces it.",
    "Mock at the boundary you do not own (network, clock, filesystem), not your own modules.",
    "A flaky test is a bug: fix or delete it, never retry it into green."])

_k("profiling", "Profiling and optimisation",
   "Measure before you optimise: a profiler tells you where the time actually goes, which is usually not where you guessed.",
   ["Python: `cProfile` for call costs, `py-spy top`/`py-spy record` for a live flame graph, `timeit` for micro-benchmarks.",
    "Optimise in this order: algorithm, then data structure, then I/O batching, then constants.",
    "Always benchmark the before and after on realistic data, and keep the harness so the next person can repeat it.",
    "Latency is a distribution: report p50 and p95/p99, never the average alone."])

_k("observability", "Logging, metrics and tracing",
   "Observability is three signals: structured logs (what happened), metrics (how much/how fast), and traces (where the time went across services).",
   ["Log JSON with a correlation id, level and timestamp; never log secrets or full request bodies.",
    "Metrics should answer user-facing questions: error rate, p95 latency, queue depth, saturation.",
    "Traces are the only practical way to debug a request that crosses several services.",
    "Alert on symptoms users feel (5xx rate, latency SLO burn), not on CPU graphs."])

_k("idempotency", "Idempotency",
   "An operation is idempotent if repeating it has the same effect as running it once — essential for retries, queues and payment APIs.",
   ["GET, PUT and DELETE are idempotent by HTTP definition; POST is not.",
    "Implement with an idempotency key: store the key with the result, and return the stored result on replay.",
    "Retries plus non-idempotent handlers produce double charges and duplicate emails — the classic production incident.",
    "Make handlers naturally idempotent where you can: `INSERT ... ON CONFLICT DO NOTHING`, upserts, set-to-value instead of increment."])


# aliases → knowledge keys (longest first so specific phrases win)
_ALIASES: dict[str, str] = {
    "dead lock": "deadlock",
    "deadlock": "deadlock",
    "race condition": "race-condition",
    "data race": "race-condition",
    "mutex": "mutex",
    "semaphore": "mutex",
    "lock vs semaphore": "mutex",
    "process vs thread": "process-thread",
    "thread vs process": "process-thread",
    "difference between a process and a thread": "process-thread",
    "gil": "gil",
    "global interpreter lock": "gil",
    "async": "async",
    "await": "async",
    "event loop": "async",
    "asynchronous": "async",
    "recursion": "recursion",
    "recursive": "recursion",
    "big o": "big-o",
    "big-o": "big-o",
    "complexity": "big-o",
    "time complexity": "big-o",
    "hash table": "hash-table",
    "hashtable": "hash-table",
    "hashmap": "hash-table",
    "database index": "index",
    "database indexes": "index",
    "indexes": "index",
    "acid": "acid",
    "transaction": "acid",
    "n+1": "nplusone",
    "n + 1": "nplusone",
    "caching": "caching",
    "cache": "caching",
    "http": "http",
    "status code": "http",
    "tcp": "tcp-udp",
    "udp": "tcp-udp",
    "tcp vs udp": "tcp-udp",
    "https": "tls",
    "tls": "tls",
    "ssl": "tls",
    "certificate": "tls",
    "dns": "dns",
    "cors": "cors",
    "jwt": "jwt",
    "json web token": "jwt",
    "rest": "rest-graphql",
    "graphql": "rest-graphql",
    "hashing": "hash-encrypt",
    "encryption": "hash-encrypt",
    "encrypt": "hash-encrypt",
    "container": "container",
    "virtual machine": "container",
    "docker": "docker",
    "kubernetes": "kubernetes",
    "k8s": "kubernetes",
    "git rebase": "git-rebase",
    "rebase": "git-rebase",
    "merge vs rebase": "git-rebase",
    "ci/cd": "cid",
    "continuous integration": "cid",
    "ci cd": "cid",
    "microservice": "microservices",
    "monolith": "microservices",
    "message queue": "queue",
    "kafka": "queue",
    "rabbitmq": "queue",
    "embedding": "embeddings",
    "embeddings": "embeddings",
    "vector search": "embeddings",
    "rag": "rag",
    "retrieval augmented": "rag",
    "quantization": "quantization",
    "quantisation": "quantization",
    "gguf": "gguf",
    "mmap": "mmap",
    "memory mapping": "mmap",
    "tokenization": "tokenization",
    "tokenisation": "tokenization",
    "tokenizer": "tokenization",
    "transformer": "transformer",
    "attention": "transformer",
    "garbage collection": "gc",
    "garbage collector": "gc",
    "memory leak": "memory-leak",
    "stack vs heap": "stack-heap",
    "stack and heap": "stack-heap",
    "dynamic programming": "dynamic-programming",
    "memoization": "dynamic-programming",
    "object oriented": "oop",
    "oop": "oop",
    "unit test": "testing",
    "testing": "testing",
    "test pyramid": "testing",
    "profiler": "profiling",
    "profiling": "profiling",
    "profile": "profiling",
    "cprofile": "profiling",
    "py-spy": "profiling",
    "profilers": "profiling",
    "observability": "observability",
    "monitoring": "observability",
    "idempotent": "idempotency",
    "idempotency": "idempotency",
}

_FACTS: dict[str, str] = {
    "capital of india": "New Delhi",
    "capital of france": "Paris",
    "capital of nepal": "Kathmandu",
    "capital of japan": "Tokyo",
    "capital of the uk": "London",
    "capital of england": "London",
    "capital of the usa": "Washington, D.C.",
    "capital of the united states": "Washington, D.C.",
    "capital of germany": "Berlin",
    "capital of australia": "Canberra",
    "capital of canada": "Ottawa",
    "capital of brazil": "Brasília",
    "capital of china": "Beijing",
    "largest planet": "Jupiter",
    "smallest planet": "Mercury",
    "largest ocean": "the Pacific Ocean",
    "longest river": "the Nile (6,650 km); the Amazon carries far more water",
    "speed of light": "299,792,458 m/s (about 300,000 km/s) in a vacuum",
    "boiling point of water": "100 °C (212 °F) at sea-level pressure",
    "freezing point of water": "0 °C (32 °F) at sea-level pressure",
    "who wrote hamlet": "William Shakespeare (around 1600)",
    "who wrote romeo and juliet": "William Shakespeare",
    "square root of 2": "1.41421356237… (irrational)",
    "value of pi": "3.14159265358979… (irrational)",
    "chemical symbol for gold": "Au",
    "chemical symbol for water": "H₂O",
    "number of continents": "seven",
    "how many days in a year": "365 days (366 in a leap year)",
    "days in a leap year": "366 days",
    "seconds in a day": "86,400 seconds (24 × 60 × 60)",
    "minutes in a day": "1,440 minutes",
    "weeks in a year": "52 weeks and 1 day (52.14)",
    "bytes in a kilobyte": "1,024 bytes (1 KiB); storage vendors often use 1,000 for marketing",
    "bits in a byte": "8 bits",
}


# Curated "X or Y?" answers — the questions people actually ask.
_DECISIONS: list[tuple[tuple[str, str], str]] = [
    (("sqlite", "postgres"), "\n".join([
        "### SQLite or Postgres?",
        "",
        "**SQLite** — a single file, zero setup, one writer at a time. Perfect for local apps, prototypes, "
        "desktop tools and read-heavy datasets under ~50 GB.",
        "**Postgres** — a server with real concurrency, roles, rich types (JSONB, arrays, ranges) and "
        "online backups. The default for anything multi-user over a network.",
        "",
        "**Rule of thumb:** one process on one machine → SQLite. Multiple app instances, or you need "
        "concurrent writers → Postgres. Migrating later is mostly schema + connection code, so pick SQLite "
        "for v1 unless you already know you need concurrency.",
    ])),
    (("sql", "nosql"), "\n".join([
        "### SQL or NoSQL?",
        "",
        "**SQL** — fixed schema, joins, transactions, ad-hoc queries. Best when relationships matter and "
        "you cannot afford inconsistent data (money, inventory, permissions).",
        "**Document/key-value stores** — schema per document, horizontal scale, denormalised reads. Best for "
        "logs, caches, feeds and shapes that change constantly.",
        "",
        "**Rule of thumb:** start relational; a JSONB column covers most of the document-store itch. Reach for "
        "a second datastore when one workload (search, time series, cache) clearly demands it.",
    ])),
    (("jwt", "session"), "\n".join([
        "### JWT or server sessions?",
        "",
        "**Sessions** — server stores the state, the client holds an opaque id. Instant revocation, tiny tokens, "
        "one lookup per request.",
        "**JWT** — signed claims the server can verify without storage. Great across services; revocation is the "
        "hard part, so use short lifetimes plus refresh tokens.",
        "",
        "**Rule of thumb:** one backend and a browser app → `HttpOnly` session cookie. Many services or mobile "
        "clients → JWT with short expiry and a refresh flow.",
    ])),
    (("pytest", "unittest"), "\n".join([
        "### pytest or unittest?",
        "",
        "**pytest** — plain `assert`, fixtures, parametrisation, a huge plugin ecosystem. Less boilerplate for "
        "the same test.",
        "**unittest** — in the standard library, class-based, familiar to Java/JUnit users, no dependencies.",
        "",
        "**Rule of thumb:** pytest unless you must run with zero third-party packages. pytest runs existing "
        "`unittest` classes unchanged, so starting with `unittest` is not a dead end.",
    ])),
    (("websocket", "polling"), "\n".join([
        "### WebSockets or polling?",
        "",
        "**Polling** — client asks on an interval. Dead simple, cacheable, works through every proxy; wastes "
        "requests and adds up to one interval of latency.",
        "**WebSockets** — one long-lived connection, server pushes instantly. Ideal for chat, cursors and live "
        "dashboards; needs heartbeats, reconnect logic and load-balancer support.",
        "",
        "**Rule of thumb:** updates slower than ~10 s → poll (or SSE). True realtime and bidirectional → WebSocket.",
    ])),
    (("monorepo", "polyrepo"), "\n".join([
        "### Monorepo or many repos?",
        "",
        "**Monorepo** — atomic cross-project changes, one CI pipeline, shared tooling. Costs you build times and "
        "access control unless you invest in caching and ownership rules.",
        "**Many repos** — independent versioning and permissions, heavier coordination for a change that spans "
        "projects.",
        "",
        "**Rule of thumb:** one product, one team, frequent cross-cutting changes → monorepo. Independent "
        "products with separate release cadences → separate repos.",
    ])),
]


def _decision_reply(prompt: str) -> str | None:
    low = prompt.lower()
    if not re.search(r"\b(or|vs\.?|versus|better|should i|which)\b", low):
        return None
    for keywords, answer in _DECISIONS:
        if all(re.search(r"(?<![a-z0-9])" + kw + r"(?![a-z0-9])", low) for kw in keywords):
            return answer
    return None


# --------------------------------------------------------------------------
# local computations that are actually performed
# --------------------------------------------------------------------------
def _math_reply(prompt: str) -> str | None:
    text = prompt.strip()
    m = re.search(r"(?:what\s+is|what's|calculate|compute|solve|=)\s*([0-9(][0-9\s+\-*/%.()^]*)$", text, re.I)
    expr = m.group(1) if m else (text if re.fullmatch(r"[\s0-9+\-*/%.()^]+", text) else None)
    if not expr:
        return None
    value = _safe_eval(expr)
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", expr.strip())
    return (f"**{cleaned} = {_fmt_num(value)}**\n\n"
            "Computed here with Python's evaluator — AST-whitelisted to constants and arithmetic, "
            "so numbers only: no names, no calls, no imports.")

def _percent_reply(prompt: str) -> str | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", prompt, re.I)
    if m:
        pct, total = float(m.group(1)), float(m.group(2))
        return f"**{_fmt_num(pct)}% of {_fmt_num(total)} = {_fmt_num(pct / 100 * total)}**\n\n{pct}/100 × {total}."
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:is what|as a)\s*%\s*of\s*(\d+(?:\.\d+)?)", prompt, re.I)
    if m:
        part, total = float(m.group(1)), float(m.group(2))
        if total:
            return f"**{_fmt_num(part)} is {_fmt_num(round(part / total * 100, 4))}% of {_fmt_num(total)}**"
    return None

def _convert_reply(prompt: str) -> str | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(km|kilometers?|kilometres?|miles?|mi|kg|kilograms?|lb|lbs|pounds?"
                  r"|c|f|°c|°f|celsius|fahrenheit|gb|mb|kb|gb|mib|gib)\b.*?(?:in|to)\s+"
                  r"(km|kilometers?|kilometres?|miles?|mi|kg|kilograms?|lb|lbs|pounds?|c|f|°c|°f|celsius|fahrenheit|gb|mb|kb|mib|gib)\b",
                  prompt, re.I)
    if not m:
        return None
    value = float(m.group(1))
    src, dst = m.group(2).lower().rstrip("s"), m.group(3).lower().rstrip("s")
    table = {"kilometer": "km", "kilometre": "km", "mile": "mi",
             "kilogram": "kg", "pound": "lb", "°c": "c", "°f": "f",
             "celsius": "c", "fahrenheit": "f"}
    src, dst = table.get(src, src), table.get(dst, dst)
    result = None
    if src == "km" and dst == "mi":
        result = value * 0.621371
    elif src == "mi" and dst == "km":
        result = value / 0.621371
    elif src == "kg" and dst == "lb":
        result = value * 2.20462
    elif src == "lb" and dst == "kg":
        result = value / 2.20462
    elif src == "c" and dst == "f":
        result = value * 9 / 5 + 32
    elif src == "f" and dst == "c":
        result = (value - 32) * 5 / 9
    elif src in ("gb", "gib") and dst in ("mb", "mib"):
        result = value * 1024
    elif src in ("mb", "mib") and dst in ("gb", "gib"):
        result = value / 1024
    elif src == dst:
        return f"That is already in {dst}: **{_fmt_num(value)} {dst}**."
    if result is None:
        return None
    pretty = f"{result:,.4f}".rstrip("0").rstrip(".")
    labels = {"c": "°C", "f": "°F"}
    return (f"**{_fmt_num(value)}{labels.get(src, ' ' + src)} = "
            f"{pretty}{labels.get(dst, ' ' + dst)}**\n\nExact conversion factor applied locally.")

def _text_tools_reply(prompt: str) -> str | None:
    low = prompt.lower()

    m = re.match(r"(?:base64\s*(?:encode|decode)?(?:\s*:)?\s+)(.+)$", prompt, re.I | re.S)
    if m and "base64" in low:
        payload = m.group(1).strip()
        try:
            if "decode" in low:
                out = base64.b64decode(payload, validate=True).decode("utf-8", errors="replace")
                return f"Decoded:\n\n```text\n{out}\n```"
            out = base64.b64encode(payload.encode("utf-8")).decode()
            return f"Base64:\n\n```text\n{out}\n```\n\nDecodes back to exactly the input (UTF-8)."
        except Exception as exc:
            return f"That is not valid base64 ({exc.__class__.__name__}). Check padding and stray characters."

    m = re.match(r"(?:sha-?256|hash)\s+(?:of\s+)?(.+)$", prompt, re.I | re.S)
    if m:
        payload = m.group(1).strip()
        return ("SHA-256:\n\n```text\n" + hashlib.sha256(payload.encode()).hexdigest() + "\n```\n\n"
                f"Input: `{payload}` ({len(payload)} chars). Hashing is one-way — it cannot be reversed.")

    m = re.match(r"(?:count\s+(?:the\s+)?words?\s+(?:in|of)\s+)(.+)$", prompt, re.I | re.S)
    if m:
        text = m.group(1).strip().strip('"').strip("'")
        words = re.findall(r"[A-Za-z0-9']+", text)
        return (f"**{len(words)} words**, {len(text)} characters, {len(text.splitlines())} line(s).\n\n"
                f"Unique words: {len(set(w.lower() for w in words))}.")

    if re.fullmatch(r"(?:generate\s+)?(?:a\s+)?uuid(?:4| v4)?", low.strip(" ?.")):
        return f"UUID v4:\n\n```text\n{uuid.uuid4()}\n```\n\nRandom, 122 bits of entropy — safe to use as an id."
    return None

def _prime_reply(prompt: str) -> str | None:
    m = re.search(r"\bis\s+(\d{1,12})\s+(?:a\s+)?(?:prime|prime number)\b", prompt, re.I)
    if m:
        n = int(m.group(1))
        if n < 2:
            return f"**{n} is not prime** — primes are greater than 1."
        if n % 2 == 0 and n != 2:
            return f"**{n} is not prime** — it is divisible by 2 ({n} = 2 × {n // 2})."
        limit = math.isqrt(n)
        for d in range(3, limit + 1, 2):
            if n % d == 0:
                return f"**{n} is not prime** — smallest factor is {d} ({n} = {d} × {n // d})."
        return f"**{n} is prime** — no divisor up to √{n} = {limit}. Checked by trial division here."
    m = re.search(r"\bfactor(?:ize|ise)?\s+(\d{1,12})\b", prompt, re.I)
    if m:
        n, original, factors = int(m.group(1)), int(m.group(1)), []
        d = 2
        while d * d <= n:
            while n % d == 0:
                factors.append(d)
                n //= d
            d += 1 if d == 2 else 2
        if n > 1:
            factors.append(n)
        grouped: list[tuple[int, int]] = []
        for factor in factors:
            if grouped and grouped[-1][0] == factor:
                grouped[-1] = (factor, grouped[-1][1] + 1)
            else:
                grouped.append((factor, 1))
        sup = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
               "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
        parts = [str(base) + ("".join(sup[c] for c in str(exp)) if exp > 1 else "")
                 for base, exp in grouped]
        pretty = " × ".join(parts) if parts else str(original)
        return f"**{original} = {pretty}**\n\nFactored locally by trial division."
    return None


def _joke_reply(prompt: str) -> str | None:
    if not re.search(r"\b(joke|make me laugh|something funny)\b", prompt, re.I):
        return None
    jokes = [
        "Why do programmers prefer dark mode? Because light attracts bugs.",
        "There are two hard things in computing: cache invalidation, naming things, and off-by-one errors.",
        "A SQL query walks into a bar, approaches two tables and asks: *may I join you?*",
        "I told my computer I needed a break — it said \"no problem, I'll go to sleep.\"",
    ]
    return jokes[hash(prompt) % len(jokes)] + "\n\n*Offline joke #" + str(hash(prompt) % len(jokes) + 1) + " — no model required.*"


def _summary_reply(prompt: str) -> str | None:
    """Extractive summary of text the user pasted — a real computation, not a fake one."""
    low = prompt.lower()
    if not re.search(r"\b(summar\w+|tl;?dr|key ?points?)\b", low):
        return None
    body = re.split(r"[:\n]", prompt, maxsplit=1)
    text = body[1].strip() if len(body) > 1 else prompt
    text = re.sub(r"^[\s:–-]+", "", text)
    if len(text.split()) < 25:
        return ("Send the text you want summarised (25+ words) and I'll extract the key sentences. "
                "I can't paraphrase like a model — I rank the sentences that carry the most distinctive terms.")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) > 4]
    if len(sentences) <= 3:
        return "That text is already about three sentences long:\n\n" + "\n".join(f"- {s}" for s in sentences)
    freq: dict[str, int] = {}
    stop = {"the", "a", "an", "and", "or", "but", "of", "to", "in", "is", "are", "was", "were", "for",
            "on", "at", "by", "with", "as", "that", "this", "it", "from", "be", "has", "have", "will"}
    for word in re.findall(r"[a-z0-9']+", text.lower()):
        if word not in stop and len(word) > 2:
            freq[word] = freq.get(word, 0) + 1
    scored = []
    for idx, sentence in enumerate(sentences):
        words = [w for w in re.findall(r"[a-z0-9']+", sentence.lower()) if w not in stop]
        if not words:
            continue
        scored.append((sum(freq.get(w, 0) for w in words) / len(words) ** 0.5, idx, sentence))
    top = sorted(sorted(scored, reverse=True)[:3], key=lambda item: item[1])
    lines = [f"**Extracted summary** ({len(text.split())} words in, {len(sentences)} sentences):", ""]
    lines += [f"- {sentence}" for _score, _idx, sentence in top]
    lines += ["", f"*Ranked by term frequency — the three sentences carrying the most distinctive words. "
                  f"There are no model weights here, so this is extraction, not paraphrase.*"]
    return "\n".join(lines)


def _time_reply(prompt: str) -> str | None:
    low = prompt.lower()
    if not any(k in low for k in ("time is it", "current time", "what time", "today's date", "what date", "what day")):
        return None
    now = _dt.datetime.now()
    tz = _dt.datetime.now().astimezone().tzname() or "local"
    return (f"It is **{now:%H:%M:%S}** on **{now:%A, %d %B %Y}** ({tz}).\n\n"
            "Read from the system clock — no network call, no timezone guessing.")


# --------------------------------------------------------------------------
# code routing
# --------------------------------------------------------------------------
_CODE_INTENTS: list[tuple[tuple[str, ...], str]] = [
    (("function add", "add(a", "add two numbers", "sum two numbers", "adds two"), "add"),
    (("debounce", "throtle", "throttle"), "debounce"),
    (("fizzbuzz", "fizz buzz"), "fizzbuzz"),
    (("palindrome",), "palindrome"),
    (("reverse", "reversed"), "reverse"),
    (("word count", "count words", "word frequency", "frequency of words", "wordcloud"), "wordcount"),
    (("dedupe", "duplicate", "unique", "distinct"), "dedupe"),
    (("sort", "order by", "sorted"), "sortdict"),
    (("binary search", "bisect"), "binarysearch"),
    (("quicksort", "quick sort", "merge sort", "sorting algorithm"), "quicksort"),
    (("prime", "sieve"), "primes"),
    (("fibonacci", "fib "), "fibonacci"),
    (("factorial",), "factorial"),
    (("flatten", "nested list"), "flatten"),
    (("group by", "groupby", "aggregate by"), "groupby"),
    (("retry", "backoff", "resilien"), "retry"),
    (("command line", "cli", "argparse", "arguments"), "argparse"),
    (("sqlite", "database table", "sql table", "persist"), "sqlite"),
    (("unit test", "tests for", "write tests", "pytest", "unittest"), "unittest"),
    (("dataclass", "data class", "record type"), "dataclass"),
    (("async", "asyncio", "await", "gather", "semaphore"), "asyncio"),
    (("http request", "make a request", "get request", "api call", "fetch json"), "httpreq"),
    (("validate", "regex", "email address", "url"), "validation"),
    (("class", "object", "oop"), "class"),
    (("json",), "json"),
    (("csv",), "csv"),
    (("html", "web page", "website", "landing page", "css"), "html"),
    (("directory", "folder", "scan", "large file", "disk usage"), "directory"),
    (("file", "write to disk", "read from disk", "save"), "fileio"),
]

_LANG_HINTS = {
    "javascript": ("javascript", "js"),
    "node": ("node", "nodejs"),
}


def _pick_code_key(prompt: str) -> str | None:
    """Match recipe intents on word boundaries — 'profile' must not match 'file'."""
    low = prompt.lower()
    for needles, key in _CODE_INTENTS:
        for needle in needles:
            if re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", low):
                return key
    return None


def _generic_code_reply(prompt: str) -> str:
    """No recipe matched: give a real skeleton for the ask instead of an unrelated snippet."""
    name_match = re.search(r"(?:function|def)\s+([A-Za-z_]\w*)|\b([A-Za-z_]\w*)\s*\(", prompt)
    name = (name_match.group(1) or name_match.group(2)) if name_match else "solve"
    if name.lower() in {"a", "the", "an", "my", "this", "to", "for", "write", "make", "create",
                        "python", "please", "code", "function", "script", "implement"}:
        name = "solve"
    return "\n".join([
        f"Here's a skeleton for `{name}()` — fill the one line that matters.",
        "",
        "```python",
        f"def {name}(*args, **kwargs):",
        '    """TODO: one sentence describing exactly what this returns."""',
        "    raise NotImplementedError",
        "",
        "",
        'if __name__ == "__main__":',
        f"    print({name}())          # run it in the Sandbox",
        "```",
        "",
        "**Why a skeleton and not a guess**",
        "- I don't have a model loaded, so inventing a body would be a guess dressed up as code.",
        "- Give me the signature and constraints (*inputs, output type, edge cases*) and I'll fill it in — "
        "that's where the recipe library kicks in.",
        "",
        "Recipes I do have ready: reverse, palindrome, FizzBuzz, binary search, quicksort, prime sieve, "
        "Fibonacci, dedupe, sorting dicts, atomic file writes, retries, argparse, SQLite, unit tests, "
        "dataclasses, asyncio, HTTP, validation, CSV/JSON, HTML, Node servers.",
    ])


def _code_reply(prompt: str, key: str | None = None) -> str:
    low = prompt.lower()
    key = key or _pick_code_key(prompt)
    wants_js = "javascript" in low or "node" in low or " in js" in low
    if key is None:
        return _generic_code_reply(prompt)
    if wants_js and key == "jsfetch":
        pass
    if wants_js and key in ("asyncio", "httpreq"):
        key = "jsfetch"
    if key == "validation" and wants_js:
        key = "jsfetch"
    recipe = _R.get(key) or _R["reverse"]
    lang = recipe["lang"]
    body = (
        f"Here's {recipe['title']} — runnable as-is.\n\n"
        f"```{lang}\n{recipe['code']}\n```\n\n"
        f"**Why this shape**\n{recipe['notes']}\n\n"
        "Paste it into **Sandbox** and press Run (Ctrl+Enter) to execute it locally."
    )
    if key == "html":
        body = ("Here's a self-contained page — paste it into **Sandbox** and Run:\n\n"
                f"```html\n{recipe['code']}\n```\n\n{recipe['notes']}")
    return body


def _tests_for(key: str) -> str:
    recipe = _R.get(key)
    if not recipe:
        return ""
    return ("\n\n**A test to prove it**\n```python\n"
            "import unittest\n\n"
            "# import the function from your module, then:\n"
            "class TestIt(unittest.TestCase):\n"
            "    def test_happy_path(self):\n        self.assertTrue(callable(YOUR_FUNCTION))\n"
            "    def test_empty_input(self):\n        self.assertIn(YOUR_FUNCTION(''), ('', None))\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n```")


_DEBUG: dict[str, tuple[str, list[str], str]] = {
    "indexerror": ("IndexError: list index out of range", [
        "You indexed at `len(seq)` (valid indexes end at `len(seq) - 1`), or the list is empty.",
        "A negative index went past `-len(seq)`.",
        "The list shrank inside a loop — iterate over a copy (`for x in list(items)`) or filter first.",
        "Print `len(items)` and the index on the line above the failure; the mismatch is usually obvious.",
    ], "```python\nif items:\n    last = items[-1]\nelse:\n    last = None\n```"),
    "keyerror": ("KeyError: a dict lookup missed", [
        "Use `d.get(key, default)` or `if key in d` when a miss is normal.",
        "The key type may differ: `1` and `\"1\"`, or a tuple vs list.",
        "Data shape changed upstream (renamed column/field) — log `list(d)[:5]` to see real keys.",
        "`collections.defaultdict` removes the branch when a missing key should create a value.",
    ], "```python\nvalue = d.get(\"key\", None)\n```"),
    "typeerror": ("TypeError: wrong type or wrong number of arguments", [
        "Read the message literally: it names the operation and the type (e.g. `NoneType is not subscriptable`).",
        "A function you call returned `None` — check the line that produced each argument.",
        "Mix of `bytes` and `str`, or `int` and `str`, is the usual culprit.",
        "Print `type(x)` for each argument right before the call.",
    ], "```python\nprint(type(a), type(b))   # right before the failing call\n```"),
    "valueerror": ("ValueError: right type, unusable value", [
        "Conversion failed (`int(\"abc\")`) or unpacking count mismatched.",
        "Validate at the boundary: strip whitespace, check the shape, then convert.",
        "Use `try/except ValueError` only where the failure is expected and recoverable.",
    ], "```python\ntext = text.strip()\nif text.isdigit():\n    n = int(text)\n```"),
    "attributeerror": ("AttributeError: object has no such attribute", [
        "The object is `None` (calling a method on a missing return value).",
        "The library version changed the API — check the version you actually installed.",
        "You are holding a list where you expected a single item (or the reverse).",
    ], "```python\nprint(type(obj), dir(obj)[:10])\n```"),
    "modulenotfounderror": ("ModuleNotFoundError / ImportError", [
        "Not installed in the interpreter you are running: `python -m pip install <name>`.",
        "Installed in another environment — `python -c \"import sys; print(sys.executable)\"` shows which one.",
        "Name differs from the import name (`pip install Pillow` → `import PIL`).",
        "A local file shadows the package (`json.py`, `email.py`) — rename it.",
    ], "```bash\npython -c \"import sys; print(sys.executable)\"\npython -m pip show <package>\n```"),
    "zerodivisionerror": ("ZeroDivisionError", [
        "A denominator reached zero — guard before dividing, and decide what the answer should be.",
        "Counting rows and dividing by the count fails on an empty input (the classic average-of-nothing bug).",
    ], "```python\navg = total / n if n else 0.0\n```"),
    "recursionerror": ("RecursionError: maximum recursion depth exceeded", [
        "Missing or unreachable base case.",
        "Each call must move strictly closer to that base case.",
        "Deep-but-valid recursion (trees, parsers) wants an explicit stack instead.",
        "`sys.setrecursionlimit` moves the crash, it does not fix the design.",
    ], ""),
    "unicodeescape": ("UnicodeDecodeError", [
        "The file is not the encoding you assumed — detect it (`chardet`) or pass `encoding=\"utf-8\", errors=\"replace\"`.",
        "On Windows, the default encoding is not UTF-8; always pass `encoding=` explicitly.",
    ], "```python\ntext = path.read_text(encoding=\"utf-8\", errors=\"replace\")\n```"),
    "jsondecodeerror": ("JSONDecodeError", [
        "The response is not JSON — often an HTML error page or an empty body.",
        "Log the first 200 characters of the body before parsing.",
        "Check the status code first: parse only on success.",
    ], "```python\nraw = resp.read().decode(\"utf-8\", \"replace\")\nprint(raw[:200])\ndata = json.loads(raw)\n```"),
    "timeout": ("Timeout / connection errors", [
        "Always pass an explicit timeout; the default is infinite.",
        "Retry only idempotent calls, with jittered backoff and a cap.",
        "Distinguish connect timeouts (DNS/firewall) from read timeouts (slow server).",
    ], "```python\nfor attempt in range(3):\n    try:\n        data = fetch(url, timeout=10)\n        break\n    except TimeoutError:\n        time.sleep(0.5 * 2 ** attempt)\n```"),
    "segmentation fault": ("Segfault / native crash", [
        "A C extension or driver crashed the interpreter — pure Python does not segfault.",
        "Reproduce the smallest snippet, then get a stack: `python -X faulthandler -c ...` or `gdb --args python ...`.",
        "Version mismatch between a compiled extension and the Python ABI is a common cause (rebuild it).",
    ], "```bash\npython -X faulthandler your_script.py\n```"),
}

_DEBUG_PATTERNS = (
    ("indexerror", r"\bindexerror\b|list index out of range"),
    ("keyerror", r"\bkeyerror\b"),
    ("typeerror", r"\btypeerror\b"),
    ("valueerror", r"\bvalueerror\b"),
    ("attributeerror", r"\battributeerror\b|object has no attribute"),
    ("modulenotfounderror", r"\bmodulenotfounderror\b|\bimporterror\b|no module named"),
    ("zerodivisionerror", r"\bzerodivisionerror\b|division by zero"),
    ("recursionerror", r"\brecursionerror\b|maximum recursion depth"),
    ("unicodeescape", r"\bunicodedecodeerror\b|\bunicodeescape\b"),
    ("jsondecodeerror", r"\bjsondecodeerror\b|expecting value"),
    ("timeout", r"\btimeout\b|timed out|connection reset|connection refused"),
    ("segmentation fault", r"segmentation fault|\bsegfault\b|core dumped"),
)


def _debug_reply(prompt: str) -> str | None:
    low = prompt.lower()
    for key, pattern in _DEBUG_PATTERNS:
        if re.search(pattern, low):
            title, points, code = _DEBUG[key]
            lines = [f"### {title}", "", "**Most likely causes**"]
            lines += [f"- {point}" for point in points]
            if code:
                lines += ["", "**Guard**", code]
            lines += ["", "*From the Demo Brain's debugging guide — install a GGUF to have the model read your actual traceback.*"]
            return "\n".join(lines)
    return None


# --------------------------------------------------------------------------
# knowledge routing
# --------------------------------------------------------------------------
def _find_topic(text: str) -> str | None:
    low = " " + re.sub(r"[^a-z0-9+/ ]", " ", text.lower()) + " "
    best, best_len = None, 0
    for alias, key in _ALIASES.items():
        if f" {alias} " in low and len(alias) > best_len:
            best, best_len = key, len(alias)
    if best:
        return best
    for alias, key in _ALIASES.items():
        if alias in low:
            return key
    return None


def _suggest_topic(text: str) -> str | None:
    """Fuzzy fallback: 'did you mean deadlock?'."""
    words = re.findall(r"[a-z]{4,}", text.lower())
    for word in words:
        hit = difflib.get_close_matches(word, list(_ALIASES), n=1, cutoff=0.82)
        if hit:
            return _ALIASES[hit[0]]
    return None


def _knowledge_reply(key: str, heading: str | None = None) -> str:
    entry = _K[key]
    lines = [f"### {heading or entry['title']}", "", entry["summary"], "", "**Key points**"]
    lines += [f"- {point}" for point in entry["points"]]
    if entry.get("gotcha"):
        lines += ["", f"**Where it bites**\n{entry['gotcha']}"]
    lines += ["", "*Answered from the offline Demo Brain's curated knowledge base (no model weights).*"]
    return "\n".join(lines)


def _compare_reply(a_key: str, b_key: str) -> str:
    a, b = _K[a_key], _K[b_key]
    return "\n".join([
        f"### {a['title']} vs {b['title']}",
        "",
        "| | " + a["title"] + " | " + b["title"] + " |",
        "| --- | --- | --- |",
        f"| **What it is** | {a['summary']} | {b['summary']} |",
        f"| **Reach for it when** | {a['points'][0]} | {b['points'][0]} |",
        f"| **Watch out for** | {a.get('gotcha') or a['points'][-1]} | {b.get('gotcha') or b['points'][-1]} |",
        "",
        "*Both entries come from the local knowledge base — no model weights involved.*",
    ])


def _explain_reply(prompt: str) -> str:
    low = prompt.lower()
    pair = re.split(r"\s+(?:vs\.?|versus|or)\s+", low)
    if len(pair) == 2:
        ka, kb = _find_topic(pair[0]), _find_topic(pair[1])
        if ka and kb and ka != kb:
            return _compare_reply(ka, kb)
    key = _find_topic(prompt)
    if key:
        return _knowledge_reply(key)
    guessed = _suggest_topic(prompt)
    if guessed:
        head = f"Did you mean **{_K[guessed]['title']}**?\n\n"
        return head + _knowledge_reply(guessed)
    return _unknown_topic_reply(prompt)


def _unknown_topic_reply(prompt: str) -> str:
    known = ", ".join(sorted({e["title"] for e in _K.values()})[:14])
    core = re.sub(r"^(please\s+)?(explain|tell me about|what is|what's|describe)\s+", "", prompt.strip(), flags=re.I).strip(" ?.!")
    return "\n".join([
        f"### {core[:80] or 'That topic'}",
        "",
        "I don't have a vetted entry for that, and I won't pad an answer with invented detail — "
        "the Demo Brain has no model weights, only computations and a curated knowledge base.",
        "",
        "**What I can do right now**",
        "- Answer from the topics I actually cover: " + known + " (and more).",
        "- Compute: arithmetic, percentages, unit conversion, hashing, UUIDs, base64, word counts.",
        "- Write runnable code for common tasks (parsing, files, HTTP, SQLite, tests, async, HTML…).",
        "",
        "**For a real answer to this question** install a brain: *Models → Download* (smallest ≈ 0.8 GB) "
        "or run `python download_models.py --recommended`. Then the same question goes to an actual model.",
    ])


# --------------------------------------------------------------------------
# security / identity / housekeeping
# --------------------------------------------------------------------------
def _security_reply(prompt: str) -> str:
    low = prompt.lower()
    if "sql" in low or "sqli" in low:
        return "\n".join([
            "### SQL injection: the bug and the fix", "",
            "**Vulnerable** — input treated as code:",
            "```python",
            "cur.execute(f\"SELECT * FROM users WHERE email = '{email}'\")",
            "```",
            "An `email` of `x' OR '1'='1` returns every row.", "",
            "**Fixed** — bind parameters so data stays data:",
            "```python",
            "cur.execute(\"SELECT * FROM users WHERE email = ?\", (email,))",
            "```", "",
            "**Checklist**",
            "- Bind everywhere; never concatenate input into SQL.",
            "- Grant the app user only what it needs (no DROP, no FILE).",
            "- Treat *any* f-string near `execute(` as a review finding.",
            "- Run **Security Lab → SAST** to scan your own files for the pattern.",
        ])
    if "xss" in low:
        return "\n".join([
            "### Cross-site scripting (XSS)", "",
            "Untrusted input reaching the DOM as HTML runs attacker script in your origin.", "",
            "**Vulnerable**", "```js", "element.innerHTML = userComment;", "```",
            "**Fixed**", "```js",
            "element.textContent = userComment;                 // text only",
            "element.replaceChildren(buildNodes(userComment));   // structured DOM",
            "```", "",
            "**Checklist**",
            "- Prefer `textContent`; `innerHTML` only on trusted, sanitised markup.",
            "- Set a strict `Content-Security-Policy` (no `unsafe-inline`).",
            "- Escape per output context: HTML, attribute, JS, URL.",
            "- Session cookies: `HttpOnly; Secure; SameSite=Lax`.",
        ])
    if "csrf" in low:
        return "\n".join([
            "### CSRF", "",
            "The browser attaches your cookies to a request the user never intended to make.", "",
            "**Defences (use several)**",
            "- `SameSite=Lax` or `Strict` cookies — the single biggest win.",
            "- A per-session token in a hidden field or header, verified server-side.",
            "- Re-authenticate or require a second factor for destructive actions.",
            "- Never perform state changes on GET.",
        ])
    return "\n".join([
        "### Hardening checklist (offline, no tools needed)", "",
        "1. **Patch** OS, browser and Python — most breaches are unpatched known CVEs.",
        "2. **Least privilege**: run services as a non-root user, one database role per app.",
        "3. **Secrets** out of source and out of logs; rotate them.",
        "4. **Validate** input at the boundary, escape at output.",
        "5. **Backups** you have restored at least once — untested backups are hopes.",
        "",
        "Run **Security Lab → Device audit** and **SAST** in this app for a local check of this machine and your code.",
    ])


def _identity_reply() -> str:
    return "\n".join([
        "I'm **ARGON-OMEGA** — an offline AI studio running entirely on this machine: chat, code IDE, "
        "Python/shell sandbox, image generation and editing, video studio, and a security lab.",
        "",
        "Right now the **Demo Brain** is answering: no GGUF weights are installed, so replies come from "
        "local computation plus a curated knowledge base rather than a language model.",
        "",
        "**For real model inference:** *Models* → **Download** (smallest starter ≈ 0.8 GB) or "
        "`python download_models.py --recommended`. Nothing else in the app changes.",
    ])


def _capabilities_reply() -> str:
    return "\n".join([
        "### What actually works here, offline", "",
        "| Area | What runs locally |", "| --- | --- |",
        "| 💬 Chat | streaming replies, history, branch/export |",
        "| 🧮 Compute | arithmetic, %, unit conversion, SHA-256, base64, UUID, word counts |",
        "| 📚 Knowledge | " + str(len(_K)) + " curated CS/OS/web topics |",
        "| 🧩 Code | " + str(len(_R)) + " runnable recipes (Python, JS, HTML, SQL) |",
        "| 🧪 Sandbox | Python execution + read-only shell |",
        "| 🛠️ IDE | file tree, editor, terminal |",
        "| 🎨 Images | gallery, editing, thumbnails |",
        "| 🛡️ Security | device audit, SAST scanner, CVE vault |",
        "| ⚡ Benchmark | measured TTFT / tok-per-sec |", "",
        "Try *“explain deadlock”*, *“explain mmap vs virtual memory”*, *“write a python function to reverse a string”*, "
        "*“what is 17*31”*, or *“remember that I use Python”*.",
    ])


def _memory_reply(prompt: str) -> str | None:
    m = re.match(r"(?:please\s+)?remember\s+(?:that\s+)?(.+)$", prompt.strip(), re.I)
    if m:
        fact = m.group(1).strip()
        remember(fact)
        return (f"Stored in local demo memory: **{fact}**\n\n"
                "- `demo_memory.json`, plain JSON, this machine only\n"
                "- Recall with *“what do you remember?”*")
    if re.search(r"(what|anything).*(remember|memory|know about me)", prompt, re.I):
        items = _load_memory()
        if not items:
            return "Demo memory is empty. Say *“Remember that …”* and I'll store it in `demo_memory.json`."
        lines = "\n".join(f"- {item['fact']} *({item['at']})*" for item in items[-20:])
        return f"Here's everything in demo memory ({len(items)} item(s)):\n\n{lines}"
    return None


# --------------------------------------------------------------------------
# follow-ups ("now in JavaScript", "add tests", "shorter")
# --------------------------------------------------------------------------
def _last(kind: str) -> dict | None:
    for turn in reversed(_RECENT):
        if turn.get("kind") == kind:
            return turn
    return None


_FOLLOWUP = re.compile(
    r"^(?:add (?:some )?tests?|tests?|write tests?|in (?:js|javascript|node)|shorter|simpler|more minimal|"
    r"minimal version|more detail|expand|go deeper|explain(?: that| it)?|why(?: does it work)?|tldr|"
    r"one line|same but .+|and .+|now in .+)$"
)


def _followup_reply(prompt: str) -> str | None:
    low = prompt.lower().strip(" ?.!")
    if not _FOLLOWUP.match(low):
        return None
    prev_code = _last("code")
    if prev_code:
        if re.search(r"\b(in|to)\s+(js|javascript|node)\b", low):
            js = _R["jsfetch"] if prev_code["key"] in ("asyncio", "httpreq") else None
            if js:
                return _code_reply("javascript", "jsfetch")
            if prev_code["key"] == "reverse":
                return ("```js\nconst reverse = (text) => [...text].reverse().join('');\n\n"
                        "console.log(reverse('ARGON'));   // 'NOGRA'\n```\n\n"
                        "`[...text]` splits by code point, so emoji survive the reverse.")
            return "That recipe is Python-specific. Say which language and I'll write the equivalent."
        if "test" in low:
            return "Here's a test skeleton for it:" + _tests_for(prev_code["key"])
        if any(w in low for w in ("shorter", "simpler", "minimal", "brief")):
            recipe = _R[prev_code["key"]]
            return f"Minimal version:\n\n```{recipe['lang']}\n{recipe['code']}\n```\n\n{recipe['notes']}"
        if any(w in low for w in ("explain", "why", "how does it work")):
            recipe = _R[prev_code["key"]]
            return f"**Why {recipe['title']} works this way**\n{recipe['notes']}"
    prev_topic = _last("topic")
    if prev_topic:
        if any(w in low for w in ("more", "detail", "expand", "go deeper")):
            entry = _K[prev_topic["key"]]
            extra = "\n".join(f"- {p}" for p in entry["points"])
            return f"### {entry['title']} — more detail\n\n{entry['summary']}\n\n{extra}\n\n{entry.get('gotcha', '')}".strip()
        if any(w in low for w in ("shorter", "tldr", "brief", "one line")):
            entry = _K[prev_topic["key"]]
            return f"**{entry['title']}** — {entry['summary']}"
    return None


# --------------------------------------------------------------------------
# main entry points
# --------------------------------------------------------------------------
def _compose_uncached(prompt: str, language: str, thinking_level: str) -> str:
    low = prompt.lower().strip()

    if re.fullmatch(r"(hi|hey|hello|yo|good (morning|evening|afternoon))[\s!.]*", low):
        return ("Hey — **ARGON-OMEGA** here, fully offline.\n\n"
                "The Demo Brain is answering (no GGUF installed), so I work from local computation, "
                f"a {len(_K)}-topic knowledge base and {len(_R)} runnable code recipes. Ask for code, an "
                "explanation, a calculation, or say *“remember that …”*.")

    if any(k in low for k in ("who are you", "what are you", "your name", "are you chatgpt", "which model")):
        return _identity_reply()
    if any(k in low for k in ("what can you do", "capabilities", "help me get started", "features")):
        return _capabilities_reply()

    mem = _memory_reply(prompt)
    if mem:
        return mem

    follow = _followup_reply(prompt)
    if follow:
        return follow

    for needle, answer in _FACTS.items():
        if needle in low:
            return (f"**{answer}**\n\n*{needle.title()} — from the Demo Brain's fact table "
                    "(offline, no model weights).*")

    debug = _debug_reply(prompt)
    if debug:
        return debug

    decision = _decision_reply(prompt)
    if decision:
        return decision

    for compute in (_percent_reply, _convert_reply, _prime_reply, _math_reply,
                    _text_tools_reply, _summary_reply, _joke_reply, _time_reply):
        out = compute(prompt)
        if out:
            return out

    if re.search(r"\b(sql injection|sqli|xss|cross-site scripting|csrf|vulnerab\w*|harden\w*|exploit\w*)\b", low):
        return _security_reply(prompt)

    code_words = ("code", "script", "function", "write", "implement", "program", "snippet", "example",
                  "class", "bot", "app", "server", "parse")
    code_key = _pick_code_key(prompt)
    wants_code = any(k in low for k in code_words) or code_key is not None
    explain_words = ("explain", "how does", "how do", "what is", "what's", "why is", "why does",
                     "difference between", "teach", "tell me about", "compare")
    wants_explain = any(k in low for k in explain_words)

    topic = _find_topic(prompt)

    # A concrete build request ("write…", "implement…") or a matched recipe wins.
    build_intent = re.search(
        r"\b(write|implement|show me|give me|create|build|make|generate|produce|draft|need|want)\b", low)
    if (code_key and (build_intent or not topic)) or (wants_code and build_intent):
        reply = _code_reply(prompt, code_key)
        if code_key:
            _RECENT.append({"kind": "code", "key": code_key, "prompt": prompt})
        return reply
    if topic:
        _RECENT.append({"kind": "topic", "key": topic, "prompt": prompt})
        return _knowledge_reply(topic)
    if wants_explain:
        return _explain_reply(prompt)
    if code_key:
        reply = _code_reply(prompt, code_key)
        _RECENT.append({"kind": "code", "key": code_key, "prompt": prompt})
        return reply
    return _unknown_topic_reply(prompt)


def compose_with_meta(prompt: str, language: str = "auto",
                      thinking_level: str = "mid") -> tuple[str, bool]:
    """Return (reply, from_cache). Repeated prompts are served from an LRU."""
    p = (prompt or "").strip()
    if not p:
        return "Send me a question — arithmetic, code, or one of the topics in *Tools → Chat*.", False
    key = (p.lower(), language, thinking_level)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit, True
    reply = _compose_uncached(p, language, thinking_level)
    with _CACHE_LOCK:
        _CACHE[key] = reply
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return reply, False


def compose(prompt: str, language: str = "auto", thinking_level: str = "mid") -> str:
    """Build the deterministic demo reply for a prompt (cached)."""
    return compose_with_meta(prompt, language, thinking_level)[0]


def generate(prompt: str, language: str = "auto", thinking_level: str = "mid") -> dict:
    """Non-streaming demo completion with *measured* timings."""
    started = time.perf_counter()
    reply, cached = compose_with_meta(prompt, language, thinking_level)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    tokens = max(1, len(_tokens(reply)))
    if cached:
        # Report the last real generation cost, not a 0.00 ms cache hit.
        total_ms = round(_telemetry()["latency_ms"], 2)
        tps = 0.0
    else:
        _record(tokens, elapsed_ms)
        total_ms = round(elapsed_ms, 2)
        tps = round(tokens / max(elapsed_ms / 1000.0, 1e-6), 1)
    return {
        "reply": reply,
        "latency_ms": total_ms,
        "first_token_ms": total_ms,
        "total_ms": total_ms,
        "tok_per_sec": tps,
        "ms_per_token": round(total_ms / tokens, 4),
        "source": "Argon Demo Brain (template + retrieval)",
        "model_used": VERSION,
        "badge": badge(total_ms, tps),
        "ms": total_ms,
        "gear": "demo",
        "demo": True,
        "cached": cached,
        "generated_ms": total_ms,
    }


def stream(prompt: str, language: str = "auto", thinking_level: str = "mid"):
    """Stream a demo completion: no artificial pacing, multi-word chunks."""
    started = time.perf_counter()
    reply, cached = compose_with_meta(prompt, language, thinking_level)
    parts = _chunks(reply, _CHUNK_WORDS)
    ttft_ms = round((time.perf_counter() - started) * 1000.0, 2)
    if cached and ttft_ms < 0.05:
        ttft_ms = _telemetry()["latency_ms"] or ttft_ms

    yield {"type": "token", "token": "", "stage": "thinking"}
    emitted: list[str] = []
    for chunk in parts:
        emitted.append(chunk)
        yield {"type": "token", "token": chunk}
        if _PACE:
            time.sleep(_PACE)

    text = "".join(emitted)
    total_ms = round((time.perf_counter() - started) * 1000.0, 2)
    tokens = max(1, len(_tokens(text)))
    if cached:
        total_ms = round(_telemetry()["latency_ms"] or total_ms, 2)
        tps = 0.0
    else:
        _record(tokens, total_ms)
        tps = round(tokens / max(total_ms / 1000.0, 1e-6), 1)
    yield {
        "type": "done",
        "reply": text,
        "ttft_ms": ttft_ms,
        "total_ms": total_ms,
        "tok_per_sec": tps,
        "badge": badge(ttft_ms, tps),
        "source": "Argon Demo Brain (template + retrieval)",
        "model_used": VERSION,
        "demo": True,
        "cached": cached,
    }
