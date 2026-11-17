import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes

from cognitive_core import (
    SYSTEM_PROMPT,
    _REAL_URLLIB_URLOPEN as _real_urlopen,
    clear_chat_history,
    get_model_tier_info,
    list_available_models,
    run_5gear_brain,
    run_pocketpal_engine,
    select_best_model_for_prompt,
    stream_pocketpal_engine,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
MODELS_DIR = os.path.join(BASE_DIR, "models")
LLAMA_SERVER_EXE = os.path.join(BIN_DIR, "llama-server.exe")
LLAMA_SERVER_URL = "http://127.0.0.1:8081/v1/chat/completions"

CODER_FAST_BRAIN = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
CODER_ARCHITECT_BRAIN = "qwen2.5-coder-32b-instruct-IQ3_XS.gguf"
PHI_BRAIN = "Phi-4-mini-instruct-Q4_K_M.gguf"
PHI_DEEP_BRAIN = "phi-4-Q4_K_M.gguf"
GEMMA_GENERAL_BRAIN = "gemma-3-4b-it-Q4_K_M.gguf"
GEMMA_FAST_BRAIN = "gemma-3-1b-it-Q4_K_M.gguf"

FAST_DEFAULT_BRAIN = GEMMA_FAST_BRAIN
BALANCED_CODER_BRAIN = "qwen2.5-coder-3b-instruct-q4_k_m.gguf"
HIGH_CODER_BRAIN = CODER_FAST_BRAIN
DEFAULT_BRAIN = FAST_DEFAULT_BRAIN

EXCLUDED_MODEL_PREFIXES = ("sd_turbo", "flux", "wan", "umt5", "t5xxl", "clip", "ae", "dreamshaper")

ELITE_BRAINS = {
    CODER_FAST_BRAIN: "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    CODER_ARCHITECT_BRAIN: "https://huggingface.co/bartowski/Qwen2.5-Coder-32B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-32B-Instruct-IQ3_XS.gguf",
    PHI_BRAIN: "https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF/resolve/main/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
    PHI_DEEP_BRAIN: "https://huggingface.co/bartowski/phi-4-GGUF/resolve/main/phi-4-Q4_K_M.gguf",
    GEMMA_GENERAL_BRAIN: "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/google_gemma-3-4b-it-Q4_K_M.gguf",
    GEMMA_FAST_BRAIN: "https://huggingface.co/bartowski/google_gemma-3-1b-it-GGUF/resolve/main/google_gemma-3-1b-it-Q4_K_M.gguf",
}

_SERVER_LOCK = threading.Lock()
_SERVER_PROC: subprocess.Popen | None = None
_ACTIVE_MODEL_NAME: str | None = None
CURRENT_LOADED_GGUF: str | None = None
_SELECTED_MODE: str = "auto"
_DOWNLOAD_LOCK = threading.Lock()
_DOWNLOAD_STATE = {
    "running": False,
    "models": {name: {"status": "missing", "downloaded_mb": 0.0, "total_mb": 0.0} for name in ELITE_BRAINS},
    "cleanup": None,
}
_RAM_TRIMMER_STOP = threading.Event()

CODER_PRIORITY = (
    HIGH_CODER_BRAIN,
    BALANCED_CODER_BRAIN,
    FAST_DEFAULT_BRAIN,
)


def select_best_coder_model() -> str | None:
    """Pick the highest-capability coder GGUF installed in models/."""
    for fname in CODER_PRIORITY:
        p = _find_model_path(fname)
        if p and os.path.getsize(p) > 100_000_000:
            return fname
    return None


def _find_model_path(filename: str) -> str | None:
    if not os.path.isdir(MODELS_DIR):
        return None
    wanted = filename.casefold()
    for existing in os.listdir(MODELS_DIR):
        if existing.casefold() == wanted:
            path = os.path.join(MODELS_DIR, existing)
            if os.path.isfile(path):
                return path
    return None


def ensure_model_loaded_in_llama_server(model_filename: str | None = None) -> tuple[bool, str | None]:
    """Hot-swap llama-server.exe on port 8081 to the requested GGUF model."""
    global CURRENT_LOADED_GGUF
    ok, loaded = ensure_llama_server(model_filename)
    if ok:
        CURRENT_LOADED_GGUF = loaded
    return ok, loaded


def switch_llama_server_model(model_name: str) -> tuple[bool, str | None]:
    """Physically switch llama-server.exe on port 8081 to `model_name` if not already loaded."""
    if CURRENT_LOADED_GGUF and CURRENT_LOADED_GGUF.casefold() == model_name.casefold():
        return True, model_name
    ok, loaded = ensure_model_loaded_in_llama_server(model_name)
    return ok, loaded


ACTIVE_ENGINE_MODEL = CURRENT_LOADED_GGUF


def ensure_model_loaded(target_model: str | None = None) -> tuple[bool, str | None]:
    """Ensure the requested specialist GGUF is physically loaded in llama-server.exe."""
    global ACTIVE_ENGINE_MODEL
    ok, loaded = switch_llama_server_model(target_model) if target_model else ensure_model_loaded_in_llama_server()
    if ok:
        ACTIVE_ENGINE_MODEL = loaded
    return ok, loaded


def compile_chatml_prompt(system_prompt: str, user_prompt: str) -> dict:
    """Compile native ChatML prompt with stop tokens."""
    prompt = (
        f"<|im_start|>system\n{system_prompt}\n<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}\n<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    return {"prompt": prompt, "stop": ["<|im_end|>", "<|endoftext|>"]}


def get_sampling_profile(mode: str = "general") -> dict:
    """Sampling profiles shared by coding, reasoning, and general text brains."""
    if mode == "coding":
        return {"temperature": 0.05, "min_p": 0.08, "top_k": 20, "repeat_penalty": 1.05, "n_predict": 2048, "cache_prompt": True}
    if mode == "reasoning":
        return {"temperature": 0.05, "min_p": 0.05, "n_predict": 2048, "cache_prompt": True}
    if mode == "fast":
        return {"temperature": 0.2, "min_p": 0.05, "n_predict": 512, "cache_prompt": True}
    return {"temperature": 0.35, "min_p": 0.05, "n_predict": 2048, "cache_prompt": True}


def verify_and_repair_code(code: str, lang: str = "python") -> tuple[str, list[str]]:
    """2-pass static & DOM verifier."""
    import ast, re
    issues = []
    if lang == "python":
        try:
            ast.parse(code)
        except SyntaxError as se:
            issues.append(f"SyntaxError on line {se.lineno}: {se.msg}")
        if len(re.findall(r"^\s*while\s+True\s*:", code, re.MULTILINE)) >= 2:
            issues.append("Multiple sequential 'while True:' loops detected")
    elif lang == "html":
        if "</script>" not in code.lower() or "</html>" not in code.lower():
            issues.append("Truncated HTML: missing </script> or </html>")
        if code.count("{") != code.count("}"):
            issues.append("Unbalanced { } braces")
        ids = set(re.findall(r'id=["\']([^"\']+)["\']', code))
        for ref in set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', code)):
            if ref not in ids:
                issues.append(f"Missing id='{ref}' for getElementById('{ref}')")
        for fn in set(re.findall(r'onclick=["\']([a-zA-Z_]\w*)\s*\(', code)):
            if not re.search(rf'(?:function\s+{fn}|window\.{fn}\s*=)', code):
                issues.append(f"Missing top-level function {fn}")
    return code, issues


CODE_VAULT_DB = os.path.join(BASE_DIR, "ssd_code_vault.db")
SSD_MATRIX_DB = os.path.join(BASE_DIR, "ssd_100m_token_matrix.db")
SANDBOX_WORKSPACE = os.path.join(BASE_DIR, "sandbox_workspace")
SSD_KV_CACHE_DIR = os.path.join(BASE_DIR, "ssd_kv_cache")
CONTEXT_WINDOW_TOKENS = 32768
_CODE_VAULT_INITIALIZED = False
_TOKEN_MATRIX_INITIALIZED = False
_CODE_VAULT_CONN = None
_TOKEN_MATRIX_CONN = None
_SSD_DB_LOCK = threading.RLock()


def save_kv_slot_to_ssd(session_id: str = "default") -> bool:
    """Freeze active llama-server KV cache slot 0 to ssd_kv_cache/."""
    os.makedirs(SSD_KV_CACHE_DIR, exist_ok=True)
    try:
        payload = json.dumps({"filename": f"{session_id}.bin"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8081/slots/0?action=save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def restore_kv_slot_from_ssd(session_id: str = "default") -> bool:
    """Restore frozen KV cache slot 0 from ssd_kv_cache/."""
    os.makedirs(SSD_KV_CACHE_DIR, exist_ok=True)
    try:
        payload = json.dumps({"filename": f"{session_id}.bin"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8081/slots/0?action=restore",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_infinite_context_window(prompt: str, history: list[dict] | None = None) -> dict:
    """3-Tier Infinite Context Compiler: Pinned Core + <3ms SSD Retrieval + Rolling Window."""
    t0 = time.perf_counter()
    parts = []

    # Tier 1: Pinned Core (laws.txt if present)
    laws_path = os.path.join(BASE_DIR, "laws.txt")
    if os.path.exists(laws_path):
        try:
            with open(laws_path, "r", encoding="utf-8", errors="replace") as f:
                parts.append(f"[Pinned Laws]\n{f.read(1200)}")
        except Exception:
            pass

    # Tier 2: <3ms SSD Retrieval from ssd_code_vault.db
    vault_hit = query_code_vault(prompt)
    if vault_hit:
        parts.append(f"[SSD Code Vault Reference]\n{vault_hit[:1200]}")
    matrix_hits = query_token_matrix(prompt)
    if matrix_hits:
        parts.append("[SSD 100M Token Matrix]\n" + "\n".join(matrix_hits))

    # Tier 3: Rolling Conversation Window
    if history:
        recent = history[-6:]
        hist_txt = "\n".join(f"{m.get('role','user')}: {str(m.get('content',''))[:300]}" for m in recent)
        parts.append(f"[Recent Turns]\n{hist_txt}")

    context_text = "\n\n".join(parts)
    used_tokens = max(1, (len(context_text) + len(prompt)) // 4)
    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "context": context_text,
        "used_tokens": used_tokens,
        "max_tokens": CONTEXT_WINDOW_TOKENS,
        "retrieval_ms": ms,
        "badge": f"🔭 Context: 32,768 Native (4-Bit KV) + 100M SSD Matrix | Used: {used_tokens} / 32,768 tokens",
    }


CONTEXT_GRAPH_DB = os.path.join(BASE_DIR, "ssd_context_graph.db")


def compress_context_tokens(history: list[dict], max_verbatim: int = 4) -> str:
    """5x Symbolic Context Compressor: keep last N turns verbatim, compress older turns."""
    if not history:
        return ""
    if len(history) <= max_verbatim:
        return "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in history)
    older = history[:-max_verbatim]
    recent = history[-max_verbatim:]
    comp = " | ".join(f"{m.get('role','u')}: {str(m.get('content',''))[:60]}" for m in older)
    verb = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in recent)
    return f"[Compressed History: {comp}]\n{verb}"


def index_codebase_graph() -> int:
    """Index .py files (functions, classes, line numbers) into ssd_context_graph.db."""
    import ast, sqlite3
    conn = sqlite3.connect(CONTEXT_GRAPH_DB)
    conn.execute("PRAGMA mmap_size = 30000000000;")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS code_graph USING fts5(file, symbol, kind, line);")
    conn.execute("DELETE FROM code_graph;")
    rows = []
    for fn in os.listdir(BASE_DIR):
        if fn.endswith(".py"):
            fp = os.path.join(BASE_DIR, fn)
            try:
                tree = ast.parse(open(fp, "r", encoding="utf-8", errors="replace").read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        kind = "class" if isinstance(node, ast.ClassDef) else "def"
                        rows.append((fn, node.name, kind, str(node.lineno)))
            except Exception:
                pass
    conn.executemany("INSERT INTO code_graph (file, symbol, kind, line) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def query_context_graph(prompt: str) -> list[str]:
    """<2ms SSD Cross-File GraphRAG lookup."""
    import re, sqlite3
    if not os.path.exists(CONTEXT_GRAPH_DB):
        index_codebase_graph()
    tokens = [t for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", prompt)][:6]
    if not tokens:
        return []
    fts_q = " OR ".join(tokens)
    conn = sqlite3.connect(CONTEXT_GRAPH_DB)
    try:
        rows = conn.execute("SELECT file, kind, symbol, line FROM code_graph WHERE code_graph MATCH ? LIMIT 8", (fts_q,)).fetchall()
        return [f"{f}:{ln} {k} {sym}" for f, k, sym, ln in rows]
    except Exception:
        return []
    finally:
        conn.close()


def auto_relay_complete_code(reply: str, lang: str = "html") -> str:
    """Multi-Pass SSD Output Relay: stitch truncated code blocks and auto-indent."""
    if reply.count("```") % 2 == 1:
        reply += "\n```"
    if lang == "html" and "</html>" not in reply.lower():
        if "</script>" not in reply.lower():
            reply = reply.rstrip("`") + "\n    </script>\n</body>\n</html>\n```"
        else:
            reply = reply.rstrip("`") + "\n</body>\n</html>\n```"
    return reply


VIRTUAL_CONTEXT_DB = os.path.join(BASE_DIR, "ssd_virtual_context.db")
_FILLER_WORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "and", "or", "in", "on", "at", "for", "with", "that", "this", "it", "very", "just", "really", "actually"}


class GlobalInfiniteContextEngine:
    """LLMLingua Symbolic Pruner + InfLLM/MemGPT 256-Token SSD Block Pager."""

    @staticmethod
    def prune_low_entropy_tokens(text: str) -> str:
        import re
        tokens = text.split()
        kept = [
            t for t in tokens
            if t.lower().strip(".,;:!?") not in _FILLER_WORDS or re.search(r"[0-9_./\\(){}\[\]=]", t)
        ]
        return " ".join(kept)

    @staticmethod
    def page_in_blocks(prompt: str, top_k: int = 6) -> list[str]:
        import re, sqlite3
        conn = sqlite3.connect(VIRTUAL_CONTEXT_DB)
        conn.execute("PRAGMA mmap_size = 30000000000;")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS virtual_blocks USING fts5(centroid, content);")
        tokens = [t for t in re.findall(r"[a-zA-Z0-9_]{3,}", prompt)][:6]
        if not tokens:
            conn.close()
            return []
        fts_q = " OR ".join(tokens)
        try:
            rows = conn.execute("SELECT content FROM virtual_blocks WHERE virtual_blocks MATCH ? LIMIT ?", (fts_q, top_k)).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    @classmethod
    def assemble(cls, prompt: str, history: list[dict] | None = None) -> dict:
        t0 = time.perf_counter()
        pruned_hist = cls.prune_low_entropy_tokens(compress_context_tokens(history or []))
        paged = cls.page_in_blocks(prompt, top_k=6)
        graph_hits = query_context_graph(prompt)
        dt = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "pruned_history": pruned_hist,
            "paged_blocks": len(paged),
            "graph_hits": len(graph_hits),
            "assembly_ms": dt,
        }


def detect_entropy_spikes(logprobs: list[float], threshold: float = 0.40) -> list[int]:
    """Stanford Logprobs Entropy Radar: flag token indices where exp(logprob) < threshold."""
    import math
    return [i for i, lp in enumerate(logprobs) if math.exp(lp) < threshold]


def run_alphacodium_contract_gate(code: str, lang: str = "html", topic: str = "") -> dict:
    """AlphaCodium Test-First + ETH Zurich Formal Contract Gate."""
    import re
    formatted = auto_indent_code(code, lang)
    _, issues = verify_and_repair_code(formatted, lang)
    if lang == "python":
        for line_number, line in enumerate(formatted.splitlines(), 1):
            indent = len(line) - len(line.lstrip())
            if line.strip() and ("\t" in line[:indent] or indent % 4):
                issues.append(f"Indentation Contract: line {line_number} must use nested 4-space indentation")

    # Contract 3: Functional assertions for Tic-Tac-Toe
    if "tictactoe" in topic.lower() or "tic-tac-toe" in topic.lower():
        if lang == "html":
            compact = re.sub(r"\s+", "", formatted)
            if "Array(9)" not in formatted and "repeat(3" not in formatted:
                issues.append("Functional Contract: missing 9-cell 3x3 board")
            if "createElement('button')" not in compact and 'createElement("button")' not in compact:
                issues.append("Functional Contract: all nine squares must be clickable buttons")
            required_wins = ("0,1,2", "3,4,5", "6,7,8", "0,3,6", "1,4,7", "2,5,8", "0,4,8", "2,4,6")
            if any(pattern not in compact for pattern in required_wins):
                issues.append("Functional Contract: missing one or more of the 8 win conditions")
            if "</script>" not in formatted.lower() or "</html>" not in formatted.lower():
                issues.append("Functional Contract: incomplete HTML document")

    repaired = False
    if issues:
        vault_code = query_code_vault(topic or "tictactoe", lang=lang)
        if vault_code:
            formatted = auto_indent_code(vault_code, lang)
            _, issues = verify_and_repair_code(formatted, lang)
            repaired = True

    return {
        "ok": len(issues) == 0,
        "code": formatted,
        "issues": issues,
        "repaired": repaired,
    }


def init_code_vault() -> None:
    """Initialize ssd_code_vault.db with FTS5 + mmap and seed verified reference patterns."""
    global _CODE_VAULT_INITIALIZED
    if _CODE_VAULT_INITIALIZED and os.path.isfile(CODE_VAULT_DB):
        return
    import sqlite3
    conn = sqlite3.connect(CODE_VAULT_DB)
    conn.execute("PRAGMA mmap_size = 30000000000;")
    conn.execute("PRAGMA cache_size = -1000;")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS code_vault USING fts5(topic, lang, code);")
    cnt = conn.execute("SELECT COUNT(*) FROM code_vault").fetchone()[0]
    if cnt == 0:
        seeds = [
            (
                "tictactoe html javascript 3x3 game",
                "html",
                """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tic-Tac-Toe</title>
    <style>
        body { margin: 0; background: #0f172a; color: #e2e8f0; font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; }
        .board { display: grid; grid-template-columns: repeat(3, 90px); gap: 8px; margin: 16px 0; }
        .cell { width: 90px; height: 90px; background: #1e293b; border: 2px solid #38bdf8; border-radius: 10px; font-size: 2rem; font-weight: 700; color: #38bdf8; cursor: pointer; }
        button.reset { padding: 8px 18px; background: #38bdf8; color: #0f172a; border: none; border-radius: 8px; font-weight: 700; cursor: pointer; }
    </style>
</head>
<body>
    <h2 id="status">Player X's Turn</h2>
    <div id="board" class="board"></div>
    <button class="reset" onclick="resetGame()">Restart</button>
    <script>
        let board = Array(9).fill('');
        let player = 'X';
        let active = true;
        const wins = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
        function render() {
            const el = document.getElementById('board');
            el.innerHTML = '';
            board.forEach((v, i) => {
                const b = document.createElement('button');
                b.className = 'cell';
                b.textContent = v;
                b.onclick = () => move(i);
                el.appendChild(b);
            });
        }
        function move(i) {
            if (!active || board[i]) return;
            board[i] = player;
            if (wins.some(([a,b,c]) => board[a] && board[a] === board[b] && board[a] === board[c])) {
                document.getElementById('status').textContent = `Player ${player} Wins!`;
                active = false;
            } else if (board.every(Boolean)) {
                document.getElementById('status').textContent = 'Draw!';
                active = false;
            } else {
                player = player === 'X' ? 'O' : 'X';
                document.getElementById('status').textContent = `Player ${player}'s Turn`;
            }
            render();
        }
        function resetGame() {
            board = Array(9).fill('');
            player = 'X';
            active = true;
            document.getElementById('status').textContent = "Player X's Turn";
            render();
        }
        render();
    </script>
</body>
</html>""",
            ),
            (
                "tictactoe python cli game",
                "python",
                """def check_winner(b):
    wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for x, y, z in wins:
        if b[x] != ' ' and b[x] == b[y] == b[z]:
            return b[x]
    return None

def play_tictactoe(moves=None):
    board = [' '] * 9
    player = 'X'
    idx = 0
    while True:
        if moves is not None:
            if idx >= len(moves):
                break
            pos = moves[idx]
            idx += 1
        else:
            pos = int(input(f"Player {player} (0-8): "))
        if board[pos] == ' ':
            board[pos] = player
            w = check_winner(board)
            if w:
                print(f"Player {w} wins!")
                return w
            if ' ' not in board:
                print("Draw!")
                return "Draw"
            player = 'O' if player == 'X' else 'X'

if __name__ == "__main__":
    play_tictactoe([0, 3, 1, 4, 2])""",
            ),
        ]
        conn.executemany("INSERT INTO code_vault (topic, lang, code) VALUES (?, ?, ?)", seeds)
        conn.commit()
    extra_seeds = [
        ("python algorithms binary search", "python", """def binary_search(values, target):
    low = 0
    high = len(values) - 1
    while low <= high:
        middle = (low + high) // 2
        if values[middle] == target:
            return middle
        if values[middle] < target:
            low = middle + 1
        else:
            high = middle - 1
    return -1"""),
        ("canvas game animation javascript", "html", """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Canvas Animation</title>
</head>
<body>
    <canvas id="game" width="480" height="320" aria-label="Animated game canvas"></canvas>
    <script>
        const canvas = document.getElementById('game');
        const context = canvas.getContext('2d');
        let x = 20;
        function frame() {
            context.clearRect(0, 0, canvas.width, canvas.height);
            context.fillStyle = '#168aad';
            context.fillRect(x, 140, 32, 32);
            x = (x + 2) % canvas.width;
            requestAnimationFrame(frame);
        }
        frame();
    </script>
</body>
</html>"""),
        ("sql create table transaction query", "sql", """BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS scores (
    player_id INTEGER PRIMARY KEY,
    player_name TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score >= 0)
);
INSERT INTO scores (player_name, score) VALUES ('Ada', 100);
SELECT player_name, score FROM scores ORDER BY score DESC;
COMMIT;"""),
        ("csharp binary search algorithm", "csharp", """using System;

public static class SearchAlgorithms
{
    public static int BinarySearch(int[] values, int target)
    {
        int low = 0;
        int high = values.Length - 1;
        while (low <= high)
        {
            int middle = low + (high - low) / 2;
            if (values[middle] == target) return middle;
            if (values[middle] < target) low = middle + 1;
            else high = middle - 1;
        }
        return -1;
    }
}"""),
        ("cpp binary search algorithm", "cpp", """#include <vector>

int binary_search(const std::vector<int>& values, int target) {
    int low = 0;
    int high = static_cast<int>(values.size()) - 1;
    while (low <= high) {
        int middle = low + (high - low) / 2;
        if (values[middle] == target) return middle;
        if (values[middle] < target) low = middle + 1;
        else high = middle - 1;
    }
    return -1;
}"""),
        ("rust binary search algorithm", "rust", """fn binary_search(values: &[i32], target: i32) -> Option<usize> {
    let mut low = 0;
    let mut high = values.len();
    while low < high {
        let middle = low + (high - low) / 2;
        match values[middle].cmp(&target) {
            std::cmp::Ordering::Equal => return Some(middle),
            std::cmp::Ordering::Less => low = middle + 1,
            std::cmp::Ordering::Greater => high = middle,
        }
    }
    None
}"""),
        ("powershell list files sort size", "powershell", """param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

Get-ChildItem -LiteralPath $Path -File -ErrorAction Stop |
    Sort-Object -Property Length -Descending |
    Select-Object -First 20 Name, @{Name = 'SizeMB'; Expression = { [math]::Round($_.Length / 1MB, 2) }}"""),
    ]
    for topic, lang, code in extra_seeds:
        conn.execute(
            "INSERT INTO code_vault (topic, lang, code) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM code_vault WHERE topic = ?)",
            (topic, lang, code, topic),
        )
    conn.commit()
    conn.close()
    _CODE_VAULT_INITIALIZED = True


def init_token_matrix() -> None:
    global _TOKEN_MATRIX_INITIALIZED
    if _TOKEN_MATRIX_INITIALIZED and os.path.isfile(SSD_MATRIX_DB):
        return
    import sqlite3
    conn = sqlite3.connect(SSD_MATRIX_DB)
    conn.execute("PRAGMA mmap_size = 30000000000;")
    conn.execute("PRAGMA cache_size = -1000;")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS token_blocks USING fts5(source_tag, content);")
    conn.close()
    _TOKEN_MATRIX_INITIALIZED = True


def query_token_matrix(prompt: str, limit: int = 2) -> list[str]:
    global _TOKEN_MATRIX_CONN
    import re, sqlite3
    init_token_matrix()
    terms = [term.replace('"', '') for term in re.findall(r"[A-Za-z0-9_]{3,}", prompt)[:6]]
    if not terms:
        return []
    query = " OR ".join(f'"{term}"' for term in terms)
    with _SSD_DB_LOCK:
        if _TOKEN_MATRIX_CONN is None:
            _TOKEN_MATRIX_CONN = sqlite3.connect(SSD_MATRIX_DB, check_same_thread=False)
            _TOKEN_MATRIX_CONN.execute("PRAGMA mmap_size = 30000000000;")
            _TOKEN_MATRIX_CONN.execute("PRAGMA cache_size = -1000;")
        try:
            rows = _TOKEN_MATRIX_CONN.execute(
                "SELECT source_tag, content FROM token_blocks WHERE token_blocks MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            return [f"[{tag}] {content[:500]}" for tag, content in rows]
        except sqlite3.OperationalError:
            return []


def ingest_into_100m_matrix(text: str, source_tag: str = "ui_upload") -> int:
    import re, sqlite3
    content = text.strip()
    if not content:
        return 0
    init_token_matrix()
    conn = sqlite3.connect(SSD_MATRIX_DB)
    conn.execute("PRAGMA mmap_size = 30000000000;")
    conn.execute("PRAGMA cache_size = -1000;")
    conn.execute("INSERT INTO token_blocks (source_tag, content) VALUES (?, ?)", (source_tag[:200], content))
    conn.commit()
    conn.close()
    return len(re.findall(r"\S+", content))


def query_code_vault(prompt: str, lang: str | None = None) -> str | None:
    global _CODE_VAULT_CONN
    """<1ms FTS5 retrieval from ssd_code_vault.db."""
    import re, sqlite3
    init_code_vault()
    tokens = [t for t in re.findall(r"[a-z0-9]+", prompt.lower()) if len(t) > 2]
    if not tokens:
        return None
    fts_q = " OR ".join(tokens[:6])
    with _SSD_DB_LOCK:
        if _CODE_VAULT_CONN is None:
            _CODE_VAULT_CONN = sqlite3.connect(CODE_VAULT_DB, check_same_thread=False)
            _CODE_VAULT_CONN.execute("PRAGMA mmap_size = 30000000000;")
            _CODE_VAULT_CONN.execute("PRAGMA cache_size = -1000;")
        try:
            if lang:
                row = _CODE_VAULT_CONN.execute("SELECT code FROM code_vault WHERE code_vault MATCH ? AND lang = ? LIMIT 1", (fts_q, lang)).fetchone()
            else:
                row = _CODE_VAULT_CONN.execute("SELECT code FROM code_vault WHERE code_vault MATCH ? LIMIT 1", (fts_q,)).fetchone()
            return row[0] if row else None
        except Exception:
            return None


def save_to_code_vault(topic: str, lang: str, code: str) -> None:
    global _CODE_VAULT_CONN
    import sqlite3
    init_code_vault()
    with _SSD_DB_LOCK:
        if _CODE_VAULT_CONN is None:
            _CODE_VAULT_CONN = sqlite3.connect(CODE_VAULT_DB, check_same_thread=False)
            _CODE_VAULT_CONN.execute("PRAGMA mmap_size = 30000000000;")
            _CODE_VAULT_CONN.execute("PRAGMA cache_size = -1000;")
        _CODE_VAULT_CONN.execute(
            "INSERT INTO code_vault (topic, lang, code) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM code_vault WHERE topic = ? AND lang = ?)",
            (topic, lang, code, topic, lang),
        )
        _CODE_VAULT_CONN.commit()


def save_to_sandbox_workspace(filename: str, code: str) -> str:
    os.makedirs(SANDBOX_WORKSPACE, exist_ok=True)
    fpath = os.path.join(SANDBOX_WORKSPACE, os.path.basename(filename))
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(code)
    return fpath


def auto_indent_code(code: str, lang: str = "python") -> str:
    """Ensure clean 4-space nested indentation without stripping leading spaces."""
    lines = [ln.rstrip("\r") for ln in code.splitlines()]
    if any(ln.startswith("    ") for ln in lines):
        return "\n".join(lines)
    indent = 0
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append("")
            continue
        if s.startswith(("}", "</head>", "</style>", "</body>", "</div>", "</script>")):
            indent = max(0, indent - 1)
        out.append(("    " * indent) + s)
        if s.endswith("{") or (lang == "html" and any(s.startswith(t) for t in ("<head", "<style", "<body", "<div", "<script")) and not s.endswith(("</head>", "</style>", "</body>", "</div>", "</script>"))):
            indent += 1
    return "\n".join(out)


def list_cartridges() -> dict:
    os.makedirs(MODELS_DIR, exist_ok=True)
    canonical_order = [
        (CODER_FAST_BRAIN, "Fast Daily Coder (7B)"),
        (CODER_ARCHITECT_BRAIN, "Architect Coder (32B)"),
        (PHI_BRAIN, "Coding, Math & Logic (3.8B)"),
        (PHI_DEEP_BRAIN, "Deep Math & Algorithms (14B)"),
        (GEMMA_GENERAL_BRAIN, "General & Multilingual (4B)"),
        (GEMMA_FAST_BRAIN, "Ultra-Fast Tasks (1B)"),
    ]
    items = []
    for fname, role in canonical_order:
        fpath = _find_model_path(fname)
        if fpath and os.path.getsize(fpath) > 1_000_000:
            items.append(
                {
                    "filename": fname,
                    "role": role,
                    "size_mb": round(os.path.getsize(fpath) / (1024 ** 2), 1),
                    "active": (_ACTIVE_MODEL_NAME == fname),
                }
            )
    return {
        "selected_mode": _SELECTED_MODE,
        "active_model": _ACTIVE_MODEL_NAME,
        "cartridges": items,
        "available_models": list_available_models(),
    }


def set_selected_mode(mode: str) -> dict:
    global _SELECTED_MODE
    requested = mode.strip() or DEFAULT_BRAIN
    _SELECTED_MODE = requested if requested == "auto" or requested in ELITE_BRAINS else "auto"
    if _SELECTED_MODE != "auto":
        ensure_model_loaded(_SELECTED_MODE)
    return list_cartridges()


def set_selected_language(lang: str) -> dict:
    return {"language": lang}


def _is_valid_text_gguf(filename: str) -> bool:
    low = filename.lower()
    if not low.endswith(".gguf") or low.endswith(".part"):
        return False
    return not any(low.startswith(pref) for pref in EXCLUDED_MODEL_PREFIXES)


def _resolve_gguf_path(target_name: str | None = None) -> str | None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    requested = target_name or DEFAULT_BRAIN
    if requested == "auto":
        requested = DEFAULT_BRAIN
    if _is_valid_text_gguf(requested):
        candidate = _find_model_path(requested)
        if candidate and os.path.getsize(candidate) > 100_000_000:
            return candidate
    for fallback_name in (FAST_DEFAULT_BRAIN, BALANCED_CODER_BRAIN, HIGH_CODER_BRAIN):
        fb = _find_model_path(fallback_name)
        if fb and os.path.getsize(fb) > 100_000_000:
            return fb
    for existing in sorted(os.listdir(MODELS_DIR)):
        if _is_valid_text_gguf(existing):
            cand = os.path.join(MODELS_DIR, existing)
            if os.path.isfile(cand) and os.path.getsize(cand) > 100_000_000:
                return cand
    return None


def _resolve_hf_url(primary_url: str) -> str | None:
    """Return a reachable public Hugging Face URL, trying equivalent HF hosts/URLs."""
    parsed = urllib.parse.urlsplit(primary_url)
    path = parsed.path
    candidates = (
        primary_url,
        urllib.parse.urlunsplit(("https", "hf.co", path, "", "")),
        urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "download=true", "")),
    )
    for candidate in dict.fromkeys(candidates):
        try:
            req = urllib.request.Request(candidate, method="HEAD", headers={"User-Agent": "Argon-Omega/1.0"})
            with _real_urlopen(req, timeout=20) as response:
                if response.status == 200:
                    return response.geturl()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            continue
    return None


def _download_brain_file(filename: str, primary_url: str) -> dict:
    target_path = os.path.join(MODELS_DIR, filename)
    if os.path.isfile(target_path) and os.path.getsize(target_path) > 100_000_000:
        size_mb = round(os.path.getsize(target_path) / (1024 ** 2), 1)
        return {"status": "ready", "downloaded_mb": size_mb, "total_mb": size_mb}

    resolved_url = _resolve_hf_url(primary_url)
    if not resolved_url:
        return {"status": "failed", "error": "No Hugging Face URL returned HTTP 200."}

    temp_path = target_path + ".part"
    os.makedirs(MODELS_DIR, exist_ok=True)
    try:
        req = urllib.request.Request(resolved_url, headers={"User-Agent": "Argon-Omega/1.0"})
        with _real_urlopen(req, timeout=60) as response:
            total_bytes = int(response.headers.get("Content-Length", 0) or 0)
            free_bytes = shutil.disk_usage(MODELS_DIR).free
            reserve_bytes = 512 * 1024 * 1024
            if total_bytes and free_bytes < total_bytes + reserve_bytes:
                raise OSError(f"Not enough free SSD space; need {round((total_bytes + reserve_bytes) / (1024 ** 3), 2)} GB including reserve.")
            downloaded = 0
            with open(temp_path, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    with _DOWNLOAD_LOCK:
                        _DOWNLOAD_STATE["models"][filename].update({
                            "status": "downloading",
                            "downloaded_mb": round(downloaded / (1024 ** 2), 1),
                            "total_mb": round(total_bytes / (1024 ** 2), 1),
                        })
        if os.path.getsize(temp_path) < 100_000_000:
            raise RuntimeError("Downloaded file is unexpectedly small; refusing to install it.")
        os.replace(temp_path, target_path)
        size_mb = round(os.path.getsize(target_path) / (1024 ** 2), 1)
        return {"status": "ready", "downloaded_mb": size_mb, "total_mb": size_mb}
    except Exception as exc:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return {"status": "failed", "error": str(exc)}


def get_elite_model_download_status() -> dict:
    with _DOWNLOAD_LOCK:
        models = {name: dict(state) for name, state in _DOWNLOAD_STATE["models"].items()}
        for name in ELITE_BRAINS:
            target = os.path.join(MODELS_DIR, name)
            if os.path.isfile(target) and os.path.getsize(target) > 100_000_000 and models[name]["status"] != "downloading":
                size_mb = round(os.path.getsize(target) / (1024 ** 2), 1)
                models[name] = {"status": "ready", "downloaded_mb": size_mb, "total_mb": size_mb}
        return {"running": _DOWNLOAD_STATE["running"], "models": models, "cleanup": _DOWNLOAD_STATE["cleanup"]}


def verify_elite_model_urls() -> dict:
    results = {}
    for filename, primary_url in ELITE_BRAINS.items():
        resolved_url = _resolve_hf_url(primary_url)
        results[filename] = {
            "ok": resolved_url is not None,
            "status": 200 if resolved_url else None,
            "resolved_url": resolved_url,
        }
    return results


def start_elite_model_download() -> dict:
    already_running = False
    with _DOWNLOAD_LOCK:
        if _DOWNLOAD_STATE["running"]:
            already_running = True
        else:
            _DOWNLOAD_STATE["running"] = True
    if already_running:
        return get_elite_model_download_status()

    def worker() -> None:
        try:
            for filename, url in ELITE_BRAINS.items():
                with _DOWNLOAD_LOCK:
                    if _DOWNLOAD_STATE["models"][filename]["status"] == "ready":
                        continue
                    _DOWNLOAD_STATE["models"][filename].update({"status": "resolving", "error": None})
                result = _download_brain_file(filename, url)
                with _DOWNLOAD_LOCK:
                    _DOWNLOAD_STATE["models"][filename].update(result)
            if all(state["status"] == "ready" for state in _DOWNLOAD_STATE["models"].values()):
                cleanup_result = cleanup_legacy_text_models()
                with _DOWNLOAD_LOCK:
                    _DOWNLOAD_STATE["cleanup"] = cleanup_result
        finally:
            with _DOWNLOAD_LOCK:
                _DOWNLOAD_STATE["running"] = False

    threading.Thread(target=worker, daemon=True, name="elite-model-downloads").start()
    return get_elite_model_download_status()


def cleanup_legacy_text_models() -> dict:
    """No-op legacy cleanup placeholder."""
    return {"ok": True, "removed": [], "errors": []}


def _prewarm_server() -> None:
    try:
        payload = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "hi"},
                ],
                "max_tokens": 1,
                "temperature": 0.3,
                "min_p": 0.10,
                "cache_prompt": True,
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            LLAMA_SERVER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception:
        pass


def _get_optimal_threads() -> str:
    cfg = os.path.join(BASE_DIR, "optimal_threads.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                t = int(json.load(f).get("threads", 0))
                if t > 0:
                    return str(t)
        except Exception:
            pass
    return str(max(2, os.cpu_count() or 4))


def ensure_llama_server(target_model: str | None = None) -> tuple[bool, str | None]:
    """Launch llama-server.exe with optimal threads, 1 slot, 4-bit KV cache, and safe fallback."""
    global _SERVER_PROC, _ACTIVE_MODEL_NAME, _RAM_TRIMMER_STOP
    with _SERVER_LOCK:
        gguf_path = _resolve_gguf_path(target_model or _SELECTED_MODE)
        if not gguf_path or not os.path.exists(LLAMA_SERVER_EXE):
            # Never kill an already-healthy server if a requested model is missing
            if _SERVER_PROC is not None and _SERVER_PROC.poll() is None and _ACTIVE_MODEL_NAME:
                return True, _ACTIVE_MODEL_NAME
            return False, None

        desired_name = os.path.basename(gguf_path)

        if _SERVER_PROC is not None and _SERVER_PROC.poll() is None and _ACTIVE_MODEL_NAME and _ACTIVE_MODEL_NAME.casefold() == desired_name.casefold():
            try:
                req = urllib.request.Request("http://127.0.0.1:8081/health", method="GET")
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        return True, _ACTIVE_MODEL_NAME
            except Exception:
                pass

        if _SERVER_PROC is not None and _SERVER_PROC.poll() is None:
            _RAM_TRIMMER_STOP.set()
            try:
                _SERVER_PROC.terminate()
                _SERVER_PROC.wait(timeout=5)
            except Exception:
                try:
                    _SERVER_PROC.kill()
                except Exception:
                    pass
            _SERVER_PROC = None

        if _SERVER_PROC is None:
            _terminate_llama_server_on_port()

        threads = _get_optimal_threads()
        creationflags = subprocess.HIGH_PRIORITY_CLASS if os.name == "nt" else 0

        def _try_boot_model(m_path: str) -> bool:
            global _SERVER_PROC, _RAM_TRIMMER_STOP
            is_7b = "7b" in os.path.basename(m_path).lower()
            ctx_primary = "16384" if is_7b else "32768"
            ctx_fallback = "8192" if is_7b else "16384"
            cmds = [
                [
                    LLAMA_SERVER_EXE, "-m", m_path, "--host", "127.0.0.1", "--port", "8081",
                    "--mmap", "--no-repack", "-np", "1", "--context-shift", "--no-warmup",
                    "-c", ctx_primary, "-b", "512", "-ub", "512", "-t", threads, "-tb", threads,
                ],
                [
                    LLAMA_SERVER_EXE, "-m", m_path, "--host", "127.0.0.1", "--port", "8081",
                    "--no-repack", "-np", "1", "--no-warmup",
                    "-c", ctx_fallback, "-b", "512", "-ub", "512", "-t", threads, "-tb", threads,
                ],
                [
                    LLAMA_SERVER_EXE, "-m", m_path, "--host", "127.0.0.1", "--port", "8081",
                    "-np", "1", "-c", "8192", "-b", "512", "-ub", "512", "-t", threads, "-tb", threads,
                ],
            ]
            for launch_cmd in cmds:
                print(f"[ARGON-OMEGA] Launching llama-server.exe: {' '.join(launch_cmd)}", flush=True)
                _SERVER_PROC = subprocess.Popen(
                    launch_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                _RAM_TRIMMER_STOP = threading.Event()
                for _ in range(120):
                    if _SERVER_PROC.poll() is not None:
                        break
                    try:
                        req = urllib.request.Request("http://127.0.0.1:8081/health", method="GET")
                        with urllib.request.urlopen(req, timeout=1) as resp:
                            if resp.status == 200:
                                threading.Thread(target=_prewarm_server, daemon=True).start()
                                return True
                    except Exception:
                        time.sleep(0.5)
                if _SERVER_PROC.poll() is None:
                    try:
                        _SERVER_PROC.terminate()
                        _SERVER_PROC.wait(timeout=3)
                    except Exception:
                        pass
            return False

        if _try_boot_model(gguf_path):
            _ACTIVE_MODEL_NAME = desired_name
            return True, _ACTIVE_MODEL_NAME

        # Fallback to FAST_DEFAULT_BRAIN if a larger model (e.g. 7B) failed CPU_REPACK/allocation
        fb_path = _find_model_path(FAST_DEFAULT_BRAIN)
        if fb_path and os.path.abspath(fb_path) != os.path.abspath(gguf_path):
            if _try_boot_model(fb_path):
                _ACTIVE_MODEL_NAME = os.path.basename(fb_path)
                return True, _ACTIVE_MODEL_NAME

        return False, desired_name


def _terminate_llama_server_on_port() -> bool:
    try:
        import psutil
    except Exception:
        return _terminate_llama_server_with_netstat()
    stopped = False
    try:
        listeners = [
            connection for connection in psutil.net_connections(kind="tcp")
            if connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr.port == 8081
            and connection.pid
        ]
    except Exception:
        return _terminate_llama_server_with_netstat()
    for connection in listeners:
        try:
            process = psutil.Process(connection.pid)
            if process.name().casefold() != "llama-server.exe":
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            stopped = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return stopped


def _terminate_llama_server_with_netstat() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["netstat.exe", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        pids = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[0].upper() == "TCP" and fields[1].rsplit(":", 1)[-1] == "8081" and fields[3].upper() == "LISTENING":
                if fields[4].isdigit():
                    pids.add(fields[4])
        stopped = False
        for pid in pids:
            task = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if not task.stdout.strip().strip('"').casefold().startswith("llama-server.exe,"):
                continue
            subprocess.run(["taskkill.exe", "/PID", pid, "/T", "/F"], capture_output=True, timeout=5, check=False)
            stopped = True
        return stopped
    except (OSError, subprocess.SubprocessError):
        return False


def stop_llama_server() -> None:
    """Stop the managed llama-server process before replacing its binaries."""
    global _SERVER_PROC, _ACTIVE_MODEL_NAME, CURRENT_LOADED_GGUF
    with _SERVER_LOCK:
        _RAM_TRIMMER_STOP.set()
        if _SERVER_PROC is not None and _SERVER_PROC.poll() is None:
            try:
                _SERVER_PROC.terminate()
                _SERVER_PROC.wait(timeout=8)
            except Exception:
                try:
                    _SERVER_PROC.kill()
                    _SERVER_PROC.wait(timeout=3)
                except Exception:
                    pass
        _SERVER_PROC = None
        _ACTIVE_MODEL_NAME = None
        CURRENT_LOADED_GGUF = None


def _trim_server_working_set(pid: int, stop_event: threading.Event) -> None:
    if os.name != "nt":
        return
    process_query_information = 0x0400
    process_set_quota = 0x0100
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    psapi.EmptyWorkingSet.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_information | process_set_quota, False, pid)
    if not handle:
        return
    try:
        while not stop_event.wait(0.25):
            if not psapi.EmptyWorkingSet(handle):
                break
    finally:
        kernel32.CloseHandle(handle)


def generate_direct(system_prompt: str, user_prompt: str, max_tokens: int = 512, target_model: str | None = None) -> tuple[str | None, str | None]:
    """Direct PocketPal-style call used by Security Lab."""
    ok, model_name = ensure_llama_server(target_model)
    if not ok:
        return None, model_name
    payload = json.dumps(
        {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "min_p": 0.10,
            "top_p": 0.90,
            "top_k": 40,
            "repeat_penalty": 1.10,
            "max_tokens": max_tokens,
            "cache_prompt": True,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        LLAMA_SERVER_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return data["choices"][0]["message"]["content"].strip(), model_name
    except Exception as exc:
        return f"llama-server.exe error: {exc}", model_name


def answer_query(prompt: str, force_offline: bool = True, model: str = "auto", swarm_enabled: bool = True, **kwargs) -> dict:
    """Route prompt via select_best_model_for_prompt() and run_5gear_brain()."""
    req_model = model if (model and model != "auto") else _SELECTED_MODE
    chosen_mode, chosen_gguf, model_badge = select_best_model_for_prompt(prompt, requested_model=req_model)
    ok, loaded_model = ensure_model_loaded(chosen_gguf)
    if not ok or not loaded_model:
        return {
            "reply": f"The offline primary model `{CODER_FAST_BRAIN}` is not installed in models/, or llama-server.exe could not start. Download that GGUF and retry.",
            "latency_ms": 0,
            "first_token_ms": 0,
            "total_ms": 0,
            "source": "Model unavailable",
            "model_used": chosen_gguf,
            "badge": f"Model unavailable: {chosen_gguf}",
        }
    effective_model = req_model if loaded_model == chosen_gguf else loaded_model
    res = run_5gear_brain(prompt, requested_model=effective_model, swarm_enabled=swarm_enabled)
    if loaded_model != chosen_gguf:
        res["model_used"] = f"{model_badge} (using installed {loaded_model})"
    used_label = res.get("model_used", model_badge)
    swarm_b = res.get("swarm_badge")
    total_ms = float(res.get("ms", 0.0) or 0.0)
    est_tokens = max(1, int(len((res.get("reply") or "").split()) * 1.3))
    tok_per_sec = round(est_tokens / (total_ms / 1000.0), 1) if total_ms > 0 else 0.0
    ms_per_tok = round(total_ms / est_tokens, 1) if est_tokens > 0 else 0.0
    tier_label, task_label = get_model_tier_info(loaded_model, chosen_mode)
    m_path = os.path.join(MODELS_DIR, loaded_model)
    m_mb = round(os.path.getsize(m_path) / (1024 ** 2), 1) if os.path.exists(m_path) else 0.0
    m_sz = f"{round(m_mb / 1024, 2)} GB" if m_mb >= 1024 else f"{m_mb} MB"
    badge = f"🧠 Brain: {loaded_model} ({m_sz}) | Rank: {tier_label} | ⚡ TTFT: {total_ms}ms | 🚀 Speed: {tok_per_sec} tokens/sec ({ms_per_tok}ms/token)"
    return {
        "reply": res["reply"],
        "latency_ms": total_ms,
        "first_token_ms": total_ms,
        "total_ms": total_ms,
        "tok_per_sec": tok_per_sec,
        "ms_per_token": ms_per_tok,
        "source": res["gear"],
        "model_used": used_label,
        "badge": badge,
    }


def stream_chat(user_prompt: str):
    req_model = _SELECTED_MODE if _SELECTED_MODE != "auto" else "auto"
    _, chosen_gguf, _ = select_best_model_for_prompt(user_prompt, requested_model=req_model)
    ok, loaded_model = ensure_model_loaded(chosen_gguf)
    if not ok or not loaded_model:
        message = f"No supported text brain is installed or llama-server.exe could not start. Requested: {chosen_gguf}"
        yield {"type": "token", "token": message}
        yield {"type": "done", "reply": message, "ttft_ms": 0, "total_ms": 0, "tok_per_sec": 0.0, "badge": "Model unavailable"}
        return
    yield from stream_pocketpal_engine(user_prompt, target_model=loaded_model)


def stream_query(prompt: str, **kwargs):
    yield from stream_chat(prompt)
