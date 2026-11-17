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
- **Look it up.** A curated knowledge base of **44 CS/OS/web topics** (deadlock,
  race conditions, GIL, async, indexes, ACID, HTTP, TLS, DNS, CORS, JWT, containers,
  Kubernetes, git rebase, RAG, embeddings, quantization, GGUF, profiling, …) written
  as real explanations with the failure mode for each, plus automatic `X vs Y`
  comparisons.
- **Write working code.** **31 runnable recipes** — reverse/palindrome/FizzBuzz,
  binary search, quicksort, prime sieve, Fibonacci, dedupe, sort-by-value, atomic
  file writes, retries with backoff, argparse, SQLite, unit tests, dataclasses,
  asyncio with bounded concurrency, HTTP with timeouts, validation, CSV/JSON, a JS
  debounce, an HTML page, a Node server. Every Python recipe is executed by
  `python selftest.py` and runs as-is.

Also handled: **`X or Y?` decisions** (SQLite/Postgres, SQL/NoSQL, JWT/sessions,
pytest/unittest, WebSockets/polling, monorepo/polyrepo), **traceback triage** for the
twelve exceptions people actually hit (IndexError, KeyError, TypeError, ImportError,
JSONDecodeError, timeouts, segfaults…), and honest scaffolding when no recipe matches —
a real skeleton for your function name rather than an unrelated snippet.

Anything outside those refuses cleanly: it says it has no vetted answer, offers the
topics it does cover, and points at installing a real brain — instead of padding the
reply with placeholder prose. Multi-turn follow-ups work too (*“add tests”*,
*“in JavaScript”*, *“shorter”*, *“more detail”*).

**Speed** is measured, not simulated: answers are composed in ~1–2 ms, cached after the
first hit, and streamed in multi-word chunks with no artificial pacing
(`ARGON_DEMO_PACE=0.02` reintroduces a per-chunk delay if you want a demo). The Benchmark
tab reports that measured latency plus a 12-check accuracy battery — arithmetic, units,
fact lookup, two code recipes, two knowledge entries, a decision question, two traceback
triage checks and one honesty check — currently **12/12** with no weights installed.

`python selftest.py` re-runs all of it (battery, every recipe in a temp dir, follow-up
handling, cold/warm latency) and exits non-zero on any failure.

- Honest labelling: the top bar shows a `🧪 Demo Brain` chip and the composer shows a banner.
- Installs seamlessly: drop any GGUF into `models/` (or use **Models** in the sidebar /
  `python download_models.py --recommended`) and the real engine takes over on the next message.
- Turn it off entirely: `ARGON_DEMO_BRAIN=0 python app.py`.

## UI & UX

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
