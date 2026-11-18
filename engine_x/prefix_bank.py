"""prefix_bank.py - Layer 2: keep expensive prompt prefixes as KV state on ROM.

Why
---
A 32k-token system prompt + repo map costs a full prefill every session: minutes on a
CPU. llama.cpp can save/restore a slot's KV cache, so a *frozen* prefix can be reloaded
in ~1s instead of recomputed. This module owns the mapping:

    language/task  ->  (prefix text, hash, kv file on disk)

and stays honest: a cached prefix is only reused when the prefix text hashes to the same
value, so editing a system prompt can never serve stale state.

No model required to use it; `LlamaSlotBackend` degrades to a no-op when llama-server
is missing, so the whole module is testable offline.
"""
from __future__ import annotations
import hashlib, json, os, shutil, time
from dataclasses import dataclass, asdict, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "ssd_kv_cache", "prefixes")


def prefix_hash(text: str) -> str:
    """Stable, order-sensitive hash of a prefix (whitespace normalised at the edges)."""
    norm = text.strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


@dataclass
class PrefixEntry:
    name: str                 # e.g. "python"
    text_hash: str
    kv_path: str
    tokens: int
    bytes: int
    created: float
    hits: int = 0

    def as_dict(self):
        return asdict(self)


class PrefixBank:
    """Directory-backed index of frozen KV prefixes."""

    INDEX = "index.json"

    def __init__(self, directory: str = DEFAULT_DIR):
        self.dir = directory
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, self.INDEX)
        self._index: dict[str, PrefixEntry] = {}
        self.load()

    # ---------------- persistence ----------------
    def load(self) -> None:
        self._index = {}
        try:
            raw = json.load(open(self.index_path, encoding="utf-8"))
        except (OSError, ValueError):
            return
        for name, d in raw.items():
            try:
                self._index[name] = PrefixEntry(**d)
            except TypeError:
                continue

    def save(self) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({n: e.as_dict() for n, e in self._index.items()}, f, indent=2)
        os.replace(tmp, self.index_path)

    # ---------------- lookup ----------------
    def get(self, name: str, text: str) -> PrefixEntry | None:
        """Return the entry only if it matches this exact prefix text and its file exists."""
        e = self._index.get(name)
        if not e:
            return None
        if e.text_hash != prefix_hash(text):
            return None
        if not os.path.exists(e.kv_path):
            return None
        return e

    def hit(self, name: str, text: str) -> PrefixEntry | None:
        e = self.get(name, text)
        if e:
            e.hits += 1
            self.save()
        return e

    def put(self, name: str, text: str, kv_path: str, tokens: int) -> PrefixEntry:
        size = os.path.getsize(kv_path) if os.path.exists(kv_path) else 0
        e = PrefixEntry(name=name, text_hash=prefix_hash(text), kv_path=kv_path,
                        tokens=int(tokens), bytes=size, created=time.time())
        self._index[name] = e
        self.save()
        return e

    def invalidate(self, name: str) -> bool:
        e = self._index.pop(name, None)
        if not e:
            return False
        try:
            os.remove(e.kv_path)
        except OSError:
            pass
        self.save()
        return True

    def prune(self, max_bytes: int) -> int:
        """Delete least-recently-used entries until the folder fits in max_bytes."""
        entries = sorted(self._index.values(), key=lambda e: e.created)
        freed = 0
        while entries and self.total_bytes() > max_bytes:
            e = entries.pop(0)
            freed += e.bytes
            self.invalidate(e.name)
        return freed

    def total_bytes(self) -> int:
        return sum(e.bytes for e in self._index.values())

    def stats(self) -> dict:
        return {"entries": len(self._index), "bytes": self.total_bytes(),
                "names": sorted(self._index), "dir": self.dir}


class SlotBackend:
    """Talks to llama-server's /slots endpoint. No-op when the server is absent."""

    def __init__(self, base_url: str = "http://127.0.0.1:8081", timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        try:
            import urllib.request
            with urllib.request.urlopen(self.base_url + "/health", timeout=1) as r:
                return r.status == 200
        except Exception:
            return False

    def save(self, filename: str) -> bool:
        ok, _ = self._post("/slots/0?action=save", {"filename": filename})
        return ok

    def restore(self, filename: str) -> bool:
        ok, _ = self._post("/slots/0?action=restore", {"filename": filename})
        return ok

    def _post(self, path: str, payload: dict) -> tuple[bool, str]:
        try:
            import urllib.request
            req = urllib.request.Request(
                self.base_url + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status == 200, ""
        except Exception as e:
            return False, str(e)


DEFAULT_PREFIXES = {
    "python": "You are a Python 3.12 coding assistant. Output only runnable code with type hints.",
    "js": "You are a JavaScript (ES2023) coding assistant. Output only runnable code, no jQuery.",
    "html": "You are an HTML/CSS assistant. Semantic markup, no external assets, no CDN links.",
    "css": "You are a CSS assistant. Modern layout (grid/flex), no frameworks.",
    "english": "You rewrite text clearly and concisely. British spelling. No filler.",
}


def freezes(bank: PrefixBank, backend: SlotBackend, name: str, text: str) -> PrefixEntry | None:
    """Freeze `text` as a KV prefix if the backend is reachable (otherwise no-op)."""
    if not backend.available():
        return None
    path = os.path.join(bank.dir, f"{name}.kv")
    if not backend.save(path):
        return None
    return bank.put(name, text, path, int(len(text.split()) * 1.3))


if __name__ == "__main__":
    bank = PrefixBank()
    backend = SlotBackend()
    print(json.dumps({"bank": bank.stats(), "backend_available": backend.available()}, indent=2))
