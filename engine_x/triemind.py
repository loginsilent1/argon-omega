"""triemind.py - Layer 1: draft several tokens, verify with one model pass.

Why
---
Decode is bandwidth-bound: every token costs a full pass over the weights. If a cheap
drafter proposes N tokens and the model verifies all N *in a single forward pass*, you
pay one pass and can emit up to N+1 tokens. The draft itself is a trie lookup (micro-
seconds), so the theoretical ceiling is `accepted + 1` tokens per pass.

What this is
------------
A radix (compressed) trie over token n-grams, built from local corpora:

    "def " -> "main" -> "(" -> ")" -> ":" -> newline -> "return" ...

`draft()` walks the trie from the current context and **stops at any branch where no
continuation has clear support** - a wrong guess is worse than no guess, because the
model pass is paid either way. `accept()` scores the draft against what the model
actually produced.

Honesty
-------
The speedup measured here is *corpus agreement*: it is the ceiling when the model's output
matches the corpus the trie was built from. A model that writes prose the corpus has never
seen gets no speedup, and the measurement below reports the hit rate so that is visible.
This module never claims a speedup it did not measure, and `drafter_cost_ms` is reported
next to it so a lookup that becomes expensive cannot hide.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\d+|\s+|[^\w\s]")
DEFAULT_CAP = 16
DEFAULT_K = 3


def tokenize(text: str) -> list[str]:
    """Lossless tokenizer: ''.join(tokenize(s)) == s, so drafts re-render exactly."""
    return TOKEN_RE.findall(text)


# --------------------------------------------------------------------------- trie
class _Node:
    __slots__ = ("children", "count")

    def __init__(self):
        self.children: dict[str, _Node] = {}
        self.count = 0          # how many times this exact path was seen


@dataclass
class Draft:
    tokens: list[str]
    confidence: float           # support of the *weakest* edge taken
    stopped_at: str             # "cap" | "branch" | "dead_end" | "low_support"
    lookup_ms: float = 0.0

    @property
    def text(self) -> str:
        return "".join(self.tokens)

    def __len__(self):
        return len(self.tokens)


class RadixDrafter:
    """Compressed-trie drafter over token sequences."""

    def __init__(self, k: int = DEFAULT_K, cap: int = DEFAULT_CAP,
                 min_count: int = 2, min_confidence: float = 0.6,
                 max_continuation: int = 24):
        self.k = k
        self.cap = cap
        # bounded lookahead per position: a suffix trie over whole documents is
        # quadratic in memory (it was OOM-killed at ~2 GB), and there is no point
        # storing continuations we would never draft past.
        self.max_continuation = max_continuation
        self.min_count = min_count
        self.min_confidence = min_confidence
        self.roots: dict[tuple[str, ...], _Node] = {}
        self.docs = 0
        self.train_tokens = 0

    # ---------------- build ----------------
    def add(self, tokens: list[str]) -> None:
        k = self.k
        self.train_tokens += len(tokens)
        self.docs += 1
        for i in range(k, len(tokens)):
            root = self.roots.get(tuple(tokens[i - k:i]))
            if root is None:
                root = self.roots[tuple(tokens[i - k:i])] = _Node()
            node = root
            for tok in tokens[i:i + self.max_continuation]:
                child = node.children.get(tok)
                if child is None:
                    child = node.children[tok] = _Node()
                child.count += 1
                node = child

    def add_text(self, text: str) -> None:
        self.add(tokenize(text))

    @classmethod
    def from_corpus(cls, patterns=("*.py", "*.md"), **kw) -> "RadixDrafter":
        d = cls(**kw)
        import glob
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
                try:
                    d.add_text(open(path, encoding="utf-8", errors="replace").read())
                except OSError:
                    continue
        return d

    # ---------------- draft ----------------
    def draft(self, context: list[str], cap: int | None = None) -> Draft:
        """Longest continuation with clear support after `context`."""
        cap = self.cap if cap is None else cap
        t0 = time.perf_counter()
        out: list[str] = []
        stop = "dead_end"
        weakest = 1.0
        node = self.roots.get(tuple(context[-self.k:]))
        if node is None:
            return Draft([], 0.0, "no_context", (time.perf_counter() - t0) * 1e3)
        while len(out) < cap:
            kids = node.children
            if not kids:
                stop = "cap" if len(out) >= cap else "dead_end"
                break
            if len(kids) > 1:
                # ranking: only continue if one child dominates
                ranked = sorted(kids.items(), key=lambda kv: -kv[1].count)
                total = sum(c.count for _, c in ranked)
                conf = ranked[0][1].count / total
                if conf < self.min_confidence or ranked[0][1].count < self.min_count:
                    stop = "branch"
                    break
            else:
                ranked = list(kids.items())
                child = ranked[0][1]
                if child.count < self.min_count:
                    stop = "low_support"
                    break
                conf = 1.0
            tok, nxt = ranked[0]
            weakest = min(weakest, conf)
            out.append(tok)
            node = nxt
        if len(out) >= cap:
            stop = "cap"
        return Draft(out, weakest if out else 0.0, stop, (time.perf_counter() - t0) * 1e3)

    # ---------------- verify bookkeeping ----------------
    @staticmethod
    def accept(draft: Draft | list[str], actual: list[str]) -> int:
        """How many drafted tokens the model actually agreed with, in order."""
        d = draft.tokens if isinstance(draft, Draft) else draft
        n = 0
        for a, b in zip(d, actual):
            if a != b:
                break
            n += 1
        return n

    # ---------------- stats ----------------
    def stats(self) -> dict:
        edges = 0
        stack = list(self.roots.values())
        while stack:
            n = stack.pop()
            edges += len(n.children)
            stack.extend(n.children.values())
        return {"docs": self.docs, "train_tokens": self.train_tokens,
                "contexts": len(self.roots), "trie_edges": edges, "k": self.k,
                "cap": self.cap, "max_continuation": self.max_continuation}


def evaluate(drafter: RadixDrafter, ref_tokens: list[str], stride: int = 5) -> dict:
    """Score a drafter on held-out tokens: how many tokens per model pass?

    `tokens_per_pass = 1 + mean_accepted`, because the model pass that verifies a
    draft also emits the token the model would have emitted anyway.
    """
    k = drafter.k
    positions = drafted = accepted = 0
    stops: dict[str, int] = {}
    t0 = time.perf_counter()
    for i in range(k, len(ref_tokens) - 1, stride):
        d = drafter.draft(ref_tokens[i - k:i])
        positions += 1
        drafted += len(d.tokens)
        stops[d.stopped_at] = stops.get(d.stopped_at, 0) + 1
        accepted += RadixDrafter.accept(d, ref_tokens[i:])
    elapsed = (time.perf_counter() - t0) * 1e3
    mean_accepted = accepted / positions if positions else 0.0
    return {
        "positions": positions,
        "mean_drafted": round(drafted / positions, 2) if positions else 0.0,
        "mean_accepted": round(mean_accepted, 2),
        "acceptance_pct": round(100 * accepted / drafted, 1) if drafted else 0.0,
        "tokens_per_pass": round(1 + mean_accepted, 2),
        "speedup": round(1 + mean_accepted, 2),
        "lookup_ms_per_draft": round(elapsed / positions, 4) if positions else 0.0,
        "stop_reasons": stops,
    }


# --------------------------------------------------------------------------- session
class SpeculativeSession:
    """Speculative decode loop over any callable model.

    `model_fn(context_tokens, n) -> list[str]` must return the next n tokens *as the
    model would have emitted them greedily*. One call = one forward pass over the
    weights (in llama.cpp you batch the draft for verification; the cost model is the
    same: one pass, up to n+1 tokens out).
    """

    def __init__(self, model_fn, drafter: RadixDrafter, max_new: int = 64):
        self.model_fn = model_fn
        self.drafter = drafter
        self.max_new = max_new

    def run(self, context: list[str]) -> dict:
        ctx = list(context)
        emitted: list[str] = []
        passes = accepted_total = drafted_total = 0
        draft_ms = 0.0
        while len(emitted) < self.max_new:
            d = self.drafter.draft(ctx)
            draft_ms += d.lookup_ms
            want = max(1, min(len(d.tokens), self.max_new - len(emitted)))
            # one pass verifies the draft and produces at least one new token
            truth = self.model_fn(ctx, max(1, want + 1))
            passes += 1
            drafted_total += len(d.tokens)
            got = RadixDrafter.accept(d, truth)
            accepted_total += got
            take = truth[:got + 1] if truth else []
            if not take:                        # model had nothing to add: stop
                break
            emitted.extend(take)
            ctx.extend(take)
        tok_per_pass = len(emitted) / passes if passes else 0.0
        return {
            "tokens": len(emitted),
            "passes": passes,
            "tokens_per_pass": round(tok_per_pass, 2),
            "acceptance_pct": round(100 * accepted_total / drafted_total, 1) if drafted_total else 0.0,
            "draft_ms_total": round(draft_ms, 3),
            "draft_ms_per_pass": round(draft_ms / passes, 3) if passes else 0.0,
            # a pass costs ~1 unit; the draft costs microseconds, so this is honest
            "speedup_vs_plain": round(tok_per_pass, 2),
        }
