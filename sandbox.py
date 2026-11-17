import ast
import os
import re
import subprocess
import sys
import tempfile
import time

# Destructive terminal commands that are strictly forbidden under Read-Only System Mode
FORBIDDEN_TERMINAL_PATTERNS = re.compile(
    r'(\bdel\b|\berase\b|\brd\b|\brmdir\b|\bformat\b|\bmove\b|\bren\b|\brename\b|'
    r'\bcopy\b|\bxcopy\b|\brobocopy\b|\breg\s+(add|delete|import)\b|'
    r'\bremove-item\b|\bset-content\b|\badd-content\b|\bclear-content\b|'
    r'\btaskkill\b|\bstop-process\b|\bshutdown\b|\brestart-computer\b|'
    r'\bnetsh\b.*\b(set|add|delete)\b|[12]?>+)',
    re.IGNORECASE,
)

# Allowed Read-Only diagnostic & inspection terminal commands
ALLOWED_TERMINAL_PREFIXES = (
    "dir", "ls", "whoami", "hostname", "ver", "systeminfo", "ipconfig",
    "netstat", "tasklist", "ping", "tracert", "pathping", "arp", "route",
    "nslookup", "where", "type", "findstr", "echo", "set", "date", "time",
    "driverquery", "wmic", "getmac", "netsh advfirewall show", "netsh wlan show",
    "git status", "git log", "git diff", "git branch", "python --version", "pip list",
)


def inspect_python_code_readonly_safety(code_str: str) -> tuple[bool, str]:
    """
    Scans Python AST before execution to block any file write, replace, delete,
    or shell execution calls inside the sandbox.
    """
    forbidden_calls = {
        "remove", "unlink", "rmdir", "removedirs", "rename", "replace",
        "rmtree", "move", "copy", "copy2", "copytree", "system", "popen",
        "Popen", "call", "check_call", "check_output",
    }
    forbidden_modules = {"shutil", "subprocess", "ctypes", "winreg"}

    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        return False, f"SyntaxError on line {e.lineno}: {e.msg}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden_modules:
                    return False, f"Sandbox Read-Only Guard: Importing `{alias.name}` is blocked to prevent file/system modification."
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in forbidden_modules:
                return False, f"Sandbox Read-Only Guard: Importing from `{node.module}` is blocked."
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                    if any(m in mode for m in ("w", "a", "x", "+")):
                        return False, f"Sandbox Read-Only Guard: `open(..., '{mode}')` is blocked. Files can only be opened in `'r'` (Read-Only) mode."
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                        if any(m in mode for m in ("w", "a", "x", "+")):
                            return False, f"Sandbox Read-Only Guard: `open(mode='{mode}')` is blocked."
            if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls:
                return False, f"Sandbox Read-Only Guard: Calling `.{node.func.attr}()` is blocked in Read-Only mode."
    return True, "OK"


RUNTIME_AUDIT_WRAPPER = r'''
import sys, builtins

def _argon_audit_hook(event, args):
    if event == "open":
        mode = str(args[1]) if len(args) > 1 else "r"
        if any(m in mode for m in ("w", "a", "x", "+")):
            raise PermissionError(f"ARGON Kernel Audit Hook: Write mode '{mode}' is strictly blocked.")
    if event in (
        "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace",
        "os.system", "os.posix_spawn", "os.spawnve", "subprocess.Popen",
        "socket.connect", "socket.bind", "shutil.rmtree", "shutil.move", "winreg.CreateKey", "winreg.SetValue"
    ):
        raise PermissionError(f"ARGON Kernel Audit Hook: System event '{event}' is strictly blocked.")

sys.addaudithook(_argon_audit_hook)
'''


def run_code_in_sandbox(code: str, timeout_sec: int = 6) -> dict:
    """Executes Python code in an isolated Read-Only sandbox subprocess with C-level audit hook."""
    t0 = time.perf_counter()
    clean_code = code.strip()
    clean_code = re.sub(r'^```\w*\s*\n?', '', clean_code)
    clean_code = re.sub(r'\n?```\s*$', '', clean_code).strip()

    is_safe, reason = inspect_python_code_readonly_safety(clean_code)
    if not is_safe:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"🔒 {reason}",
            "exit_code": 1,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    wrapped_code = RUNTIME_AUDIT_WRAPPER + "\n" + clean_code
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", wrapped_code],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=tempfile.gettempdir(),
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip()[:4000],
            "stderr": (proc.stderr or "").strip()[:2000],
            "exit_code": proc.returncode,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"⏱️ Sandbox Timeout: Execution exceeded {timeout_sec} seconds and was safely terminated.",
            "exit_code": 124,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


def run_safe_terminal_command(cmd_str: str, timeout_sec: int = 6) -> dict:
    """Runs read-only diagnostic Windows terminal commands while blocking file writes/deletes."""
    t0 = time.perf_counter()
    clean_cmd = cmd_str.strip()
    if clean_cmd.startswith("$"):
        clean_cmd = clean_cmd[1:].strip()

    if not clean_cmd:
        return {"ok": False, "stdout": "", "stderr": "Empty command.", "elapsed_ms": 0.0}

    if FORBIDDEN_TERMINAL_PATTERNS.search(clean_cmd):
        return {
            "ok": False,
            "stdout": "",
            "stderr": (
                "🔒 READ-ONLY TERMINAL GUARD: Command blocked.\n"
                "Writing (`>`), deleting (`del`/`rmdir`), replacing (`move`/`ren`/`copy`), "
                "or killing processes is strictly disabled to protect your device."
            ),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    py_match = re.match(r'^python3?\s+-c\s+["\'](.+)["\']$', clean_cmd, re.DOTALL)
    if py_match:
        return run_code_in_sandbox(py_match.group(1), timeout_sec=timeout_sec)

    low_cmd = clean_cmd.lower()
    if not any(low_cmd == p or low_cmd.startswith(p + " ") for p in ALLOWED_TERMINAL_PREFIXES):
        return {
            "ok": False,
            "stdout": "",
            "stderr": (
                f"🔒 READ-ONLY TERMINAL GUARD: `{clean_cmd.split()[0]}` is not in the Read-Only allowlist.\n"
                "Allowed read-only commands: dir, ipconfig, netstat, tasklist, whoami, systeminfo, ping, type, findstr, wmic, git status, pip list."
            ),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    try:
        exec_cmd = "dir" if low_cmd == "ls" else clean_cmd
        proc = subprocess.run(
            exec_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=os.getcwd(),
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip()[:5000],
            "stderr": (proc.stderr or "").strip()[:2000],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"⏱️ Terminal command timed out after {timeout_sec}s.",
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(e),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
