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

- **Tools live in a bottom navigation bar** — 💬 Chat · ⌨️ IDE · 🎨 Gallery · 🖌️ Editor ·
  🛡️ Security · ⚡ Bench · 🎬 Video · ⚙️ Settings — always visible under the content, thumb
  reachable, 52–56 px tap targets, safe-area padding on phones, horizontal scroll on
  narrow screens. The active tool is highlighted and marked `aria-current`.
- **Sidebar** keeps the conversation side: brand row → **＋ New chat** → chat search →
  **Recent chats** → archived → **⚙️ Settings / 🧠 Models** pinned at the bottom above the
  storage readout.
- **Touch sizing**: a `(pointer: coarse)` pass grows icon buttons to 42 px, chips and
  buttons to 40 px, history rows and sidebar actions to 44 px, the send button to 48 px,
  and sets form fonts to 16 px so iOS does not zoom on focus. Tap highlight is removed and
  `touch-action: manipulation` kills the 300 ms double-tap delay.
- **Top bar** stays for device state only: brand, demo chip, Settings, Models, Offline —
  no duplicate tool rows to clip or scroll.
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
