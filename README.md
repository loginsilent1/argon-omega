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

No GGUF installed? The app still runs a full chat experience through the built-in
**Demo Brain** (`demo_brain.py`): replies are generated locally from intent
templates, streamed token-by-token, and labelled `🧪 Demo Brain` so it is never
mistaken for model output. It is used automatically when `models/` has no brain
and a local `llama-server` is unavailable.

- Honest labelling: the top bar shows a `🧪 Demo Brain` chip and the composer shows a banner.
- Useful behaviour without weights: code snippets, explanations, security guidance,
  safe arithmetic, date/time, and a small persistent memory (`demo_memory.json`).
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
