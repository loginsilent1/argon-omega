# ARGON-OMEGA

100% Offline AI Studio featuring local GGUF inference (`llama-server.exe`), ChatGPT/Gemini-style web UI, VS Code + Antigravity AI IDE, Read-Only System Terminal & Python Sandbox, AI Image Studio & Editor, and SSD Video AI Studio.

## How to Use It From Any PC

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/loginsilent1/argon-omega.git
   cd argon-omega
   ```

2. **Download the AI models (`models/*.gguf`):**
   ```powershell
   .\download_models.ps1
   ```
   *(Or run `python setup_engine.py` / click **📥 Download Brains** inside the UI.)*

   Windows PowerShell not available (Linux/macOS/WSL)? Use the cross-platform
   downloader — same filenames, resumable transfers, no extra packages:
   ```bash
   pip install -r requirements.txt
   python download_models.py --recommended      # ~5.5 GB starter set
   python download_models.py                    # all brains (~33 GB)
   python download_models.py --list             # show names/sizes
   python download_models.py --hf-mirror https://hf-mirror.com   # if huggingface.co is blocked
   ```

3. **Start Argon-Omega:**
   ```powershell
   python app.py
   ```
   Open `http://127.0.0.1:8080/` in your browser.

   > **Platform note:** bundled `bin/llama-server.exe` is a Windows binary. On
   > Linux/macOS the web UI, IDE, sandbox, image/video/security panels all run,
   > but local GGUF chat needs a matching `llama-server` build on port `8081`.

## Demo Brain (works with zero models)

No GGUF installed? Chat still works through the built-in **Demo Brain**
(`demo_brain.py`). It is not a language model and does not pretend to be one — it
routes each prompt to one of three things it can actually do:

- **Compute it.** Arithmetic and percentages (AST-whitelisted evaluator), unit
  conversion (km/mi, kg/lb, °C/°F), SHA-256, base64, UUID v4, word/character counts,
  primality and factorisation, extractive summaries of pasted text, date and time —
  all executed, never guessed.
- **Look it up.** A curated knowledge base of **47 CS/OS/web topics** (deadlock,
  race conditions, GIL, async, indexes, ACID, HTTP, TLS, DNS, CORS, JWT, containers,
  Kubernetes, git rebase, RAG, embeddings, quantization, GGUF, profiling, plus AI,
  LLMs and machine learning, …) written as real explanations with the failure mode for
  each, plus automatic `X vs Y` comparisons — including reversed phrasings like
  *“the difference between a thread and a process”*.
- **Write working code.** **44 runnable recipes** — reverse/palindrome/FizzBuzz,
  binary search, quicksort, prime sieve, Fibonacci, dedupe, sort-by-value, atomic
  file writes, retries with backoff, argparse, SQLite, unit tests, dataclasses,
  asyncio with bounded concurrency, HTTP with timeouts, validation, CSV/JSON, a JS
  debounce, an HTML page, a Node server — plus **LRU caches, timing decorators,
  context managers, thread pools, token-bucket rate limiting, logging setup,
  subprocess, env config, enums/dataclasses, TypeVar/Protocol, top-K with heapq,
  itertools chunking and pathlib scans**. Every Python recipe is executed by
  `python selftest.py` (40 of them) and runs as-is.
- **Coach the work.** **6 playbooks** for the questions that are not lookups: project
  ideas with a scope ladder, project structure, code-review order, a deliberate
  practice plan, a five-move debugging method and a testing strategy.

Also handled: **`X or Y?` decisions** (SQLite/Postgres, SQL/NoSQL, JWT/sessions,
pytest/unittest, WebSockets/polling, monorepo/polyrepo), **traceback triage for 20
failure modes** (IndexError, KeyError, TypeError, NameError, SyntaxError,
IndentationError, FileNotFoundError, ModuleNotFoundError, JSONDecodeError,
`database is locked`, `address already in use`, CORS, MemoryError, timeouts,
segfaults…), and honest scaffolding when no recipe matches — a real skeleton for your
function name rather than an unrelated snippet.

Anything outside those refuses cleanly: it says it has no vetted answer, offers the
topics it does cover, and points at installing a real brain — instead of padding the
reply with placeholder prose. Multi-turn follow-ups work too (*“add tests”*,
*“in JavaScript”*, *“shorter”*, *“more detail”*).

**Speed** is measured, not simulated: answers are composed in ~1–2 ms, cached after the
first hit, and streamed in multi-word chunks with no artificial pacing
(`ARGON_DEMO_PACE=0.02` reintroduces a per-chunk delay if you want a demo). The Benchmark
tab reports that measured latency plus an **18-check accuracy battery** — arithmetic,
units, fact lookup, code recipes, knowledge entries, a decision question, four traceback
triage checks, a comparison, a coaching answer and one honesty check — currently
**18/18** with no weights installed. The same battery, with a pass row per check, is in
**⚙️ Settings → Benchmark**.

`python selftest.py` re-runs all of it (battery, every recipe in a temp dir, follow-up
handling, cold/warm latency) and exits non-zero on any failure.

- Honest labelling: the top bar shows a `🧪 Demo Brain` chip and the composer shows a banner.
- Installs seamlessly: drop any GGUF into `models/` (or use **Models** in the sidebar /
  `python download_models.py --recommended`) and the real engine takes over on the next message.
- Turn it off entirely: `ARGON_DEMO_BRAIN=0 python app.py`.

## Code IDE + agent

The IDE is a browser VS Code: file explorer, tabbed editor with line numbers, run
button, live HTML preview, and a full terminal.

The right-hand **agent** pane is modelled on agentic editors like Antigravity, with
three explicit modes:

| Mode | Behaviour |
|---|---|
| 🤖 **Auto** | Plans, edits and runs, then shows the plan and every diff. |
| ⚡ **Turbo** | Applies immediately with minimal narration. |
| 🔍 **Request review** | Stages the change, shows the unified diff, and writes nothing until you press **Apply**. |

It performs **real file operations** (`code_agent.py`), not vibes: create a file from
the recipe library or a typed scaffold, write a unittest file covering the module's
functions, insert missing docstrings, normalise formatting (CRLF, trailing whitespace,
tabs, blank-line runs), run the file, explain its structure, or run static review
findings — bare `except:`, mutable default arguments, over-long lines, TODO markers,
`eval`/`os.system` usage. Every edit comes with a diff and can be undone from the
transcript (**↩ Undo this run**) or reverted server-side.

Because no model weights are installed, the agent is explicit about what it is: a
planner over parsers and templates. It will not invent an implementation — the test
file it writes has TODO bodies for the assertions, and the review lists only what the
static checks actually found.

## UI & UX

- **Tools live in a collapsible drawer inside the sidebar**, between the chat search and
  the recent chats: ⌨️ Code IDE · 🎨 Gallery · 🎬 Video studio, with the open tool
  highlighted. The drawer remembers whether you left it open. Chat, Security, the image
  editor and the benchmark are not tool entries any more — chat is the default view, the
  editor opens from the Gallery, and Security + Benchmark live in **⚙️ Settings**.
- **Sidebar** keeps the conversation side: brand row → **＋ New chat** → chat search →
  **Tools drawer** → **Recent chats** → archived → **⚙️ Settings / 🧠 Models** pinned at the
  bottom above the storage readout.
- **⚙️ Settings** has four panes: **Benchmark** (cards, all 18 checks, re-run, plus a link to
  the full benchmark view), **Engine**, **Security** (device audit, offline CVE vault, SAST
  scanner with auto-patch — the panel widens for it) and **Workspace**.
- **Touch sizing, always on**: the sidebar is sized for fingers everywhere, not just on
  phones — New chat 46 px tall, search 44 px, history rows 48 px with 34 px share/archive/
  delete buttons that stay visible, Archived toggle 42 px, Settings/Models 46 px side by
  side, engine strip 96 px, header buttons 40 px. A `(pointer: coarse)` pass additionally
  grows icon buttons to 42 px, chips to 40 px, the send button to 48 px and sets form fonts
  to 16 px so iOS does not zoom on focus. Tap highlight is removed and
  `touch-action: manipulation` kills the 300 ms double-tap delay.
- Tapping any row in the sidebar opens that conversation and keeps it highlighted, even
  when the chat is still empty.
- **Every screen has its own URL** and is deep-linkable, with back/forward working:
  `/chat`, `/ide`, `/gallery`, `/editor`, `/video`, `/benchmark`, `/sandbox`,
  `/settings?tab=benchmark|engine|security|workspace` and `/models`. The title follows the
  screen (`ARGON-OMEGA · Code IDE`), switching a screen pushes a history entry, and the
  server answers those paths with the same single-file app (unknown paths still 404).
- **A conversation has its own URL too**: opening a chat — from the list, after searching,
  or from Archived — lands on `/chat#chat-<id>`, so a reload (or a shared link) re-opens
  that exact chat. Searching the chat list only filters while you type and never rewrites
  the URL; picking a result is what moves you to `/chat`.
- **Top bar is minimal**: brand, demo chip and the offline toggle only — Models and Settings
  are not up there any more.
- **The ✦ logo starts a new chat**: the brand pill in the top bar (next to the demo chip) and
  the sidebar's ＋ New chat button both clear the conversation, show the welcome screen and
  land on `/chat` — even when you are already reading a chat.
- **The rail never scrolls itself**: the chat list owns the scrolling, every inner scroller
  is contained (no scroll chaining out to the rail or the page), all `focus()` calls use
  `preventScroll` so focusing the composer can never slide the brand/New chat row out of
  view, and re-rendering the list (New chat, search, archive toggle) resets the rail to the
  top defensively for short windows.
- **Search chats sits between Tools and Recent chats**, where you look for it: it shows as a
  compact row, expands into a text field on tap (Esc clears and folds it back), filters while
  you type, and never rewrites the URL.
- **Phone and desktop are one code path**: below 820px the rail is a drawer opened by ☰ with
  a scrim and a locked body; at 820px and up it is a permanent panel. A layout watchdog
  re-applies the right state whenever the width changes — rotate, fold/unfold, split-screen
  or a resized desktop window — so a phone can never inherit the desktop `width:0` rail and a
  desktop can never inherit the drawer transform. Dynamic viewport units (`100dvh`) plus
  `env(safe-area-inset-*)` keep it usable on notched phones and inside mobile browsers'
  collapsing toolbars, `.main`/`.tab-view` can shrink instead of overflowing, and 16px inputs
  on touch devices stop iOS from zooming the page. Extra breakpoints cover short landscape
  phones (≤500px tall), very narrow covers (≤380px) and 821–1100px tablets/laptops.
- **Settings is one vertical stack**: the four sections are a compact horizontal chip strip
  across the top (Benchmark · Engine · Security · Workspace), and everything under it runs
  full-width down the page — benchmark cards one per row, buttons stacked, then the check
  list; Security's three lab panels stack too. The Models window follows the same idea, with
  each model's status pill and button dropping below its name.
- **The bottom row never moves**: the chat list is the only thing that scrolls inside the
  rail, so the Settings/Models buttons and the offline note stay pinned at the bottom
  whatever the chat count, filter or tools-drawer state (very short windows fall back to
  scrolling the rail so nothing gets clipped).
- **Sidebar bottom is two icon buttons** (⚙️ Settings, 🧠 Models) with tooltips/aria-labels,
  and the SSD ROM / engine readout block is gone from the sidebar (engine + storage facts
  live in Settings → Engine / Workspace).
- Type scale is one notch smaller (`html { font-size: 15px }`).
- **Compact scale**: 248 px sidebar, 46 px top bar, 42 px New chat, 44 px history rows,
  40 px tool rows, 760 px chat column/composer and a 780 px Settings panel (940 px for the
  security lab) — denser without dropping below comfortable touch targets.
- **⚙️ Settings** (sidebar, top bar, or `Ctrl+,`): **Benchmark** (accuracy cards, latency,
  all 18 checks with pass/fail rows and a re-run button), **Engine** (active brain, topics,
  recipes, memory facts, measured latency) and **Workspace** (ROM database path and size,
  rows, free disk, storage guard, harvester state).
- **Sandbox** is no longer a top-level tool: it opens from the IDE's terminal header,
  since the IDE terminal already covers day-to-day execution.


- **Composer**: auto-growing input, image / terminal / quantum toggles, thinking-level
  selector, live character count, `Enter` send · `Shift+Enter` newline.
- **Chat**: streamed replies with a stop button, per-message Copy / Regenerate / Branch /
  Delete, code cards with Copy + Re-run, scroll-to-bottom button, toast notifications.
- **Sidebar**: chat search, live SSD-ROM/engine telemetry, model manager entry point.
- **Keyboard**: `Ctrl/Cmd+K` focus composer · `Ctrl/Cmd+B` toggle sidebar ·
  `Ctrl+Shift+O` new chat · `Esc` stop generation.
- **Models panel**: per-model install state and one-click downloads (works with the
  same `engine.ELITE_BRAINS` catalog the CLI uses).
- **Texture & depth**: layered gradients, film grain, focus glows, and reduced-motion support.

### Platform / host notes

- `POST` requests are protected by a same-origin guard. Loopback hosts, `*.e2b.app`
  preview domains and anything listed in `ARGON_ALLOWED_HOSTS` are accepted; the
  `Origin` header must match the request host. Set `ARGON_ALLOW_ANY_HOST=1` only on a
  trusted private network.

---

## `engine_x/` — the fused-engine work, as measured code

Optional, self-contained measurement layer (does not change how the app runs unless you
apply it). Everything is verified before it is timed:

- **47 tests:** `python3 engine_x/test_engine_x.py`
- **baseline numbers:** `python3 engine_x/bench_all.py --quick` → `engine_x/bench_baseline.json`
- **what each layer measured:** `engine_x/README.md`

Highlights: plan-then-emit templates **4.9×** fewer output tokens · radix drafter
**1.86** tokens per model pass · BitCore int8 AVX2 **28×** and VNNI **68×** vs float
(ternary: 0.97×, a density win only) · prefix-KV bank lookup **0.003 ms** · FlashMap
1.1–1.55× disk read · AirLLM layer streaming keeps peak RAM at **11–17 %** of the model
but runs a 70 GB model at 0.010 tok/s on this disk, so quantise-and-stay-resident wins
whenever it fits.

To wire it into `engine.py` (dry-run first, idempotent, aborts cleanly):

```bash
python3 engine_x/apply_to_engine.py          # prints the diff, writes nothing
python3 engine_x/apply_to_engine.py --write  # applies, keeps engine.py.bak
```
