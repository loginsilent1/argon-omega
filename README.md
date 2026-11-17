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

3. **Start Argon-Omega:**
   ```powershell
   python app.py
   ```
   Open `http://127.0.0.1:8080/` in your browser.
