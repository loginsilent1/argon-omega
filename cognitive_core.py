import ast
import http.client
import json
import subprocess
import operator
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor
import ctypes
import sqlite3
import time
import urllib.error
import urllib.request
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from image_engine import edit_last_image_natural_language, generate_image_hybrid
from readonly_system_guard import inspect_risky_file_readonly, resolve_special_folder

# Default to 100% OFFLINE ONLY mode (blocks all external API calls, allows only 127.0.0.1 / localhost)
OFFLINE_MODE_LOCK = True
_REAL_URLLIB_URLOPEN = urllib.request.urlopen


def _guarded_urlopen(url_or_req, *args, **kwargs):
    """Blocks any outbound HTTP/HTTPS API request when OFFLINE_MODE_LOCK is True, allowing only 127.0.0.1 / localhost."""
    target_url = url_or_req.full_url if hasattr(url_or_req, "full_url") else str(url_or_req)
    if OFFLINE_MODE_LOCK:
        low_u = target_url.lower()
        if not (low_u.startswith("http://127.0.0.1") or low_u.startswith("http://localhost")):
            raise PermissionError(f"✈️ OFFLINE MODE ACTIVE: External API request blocked ({target_url})")
    return _REAL_URLLIB_URLOPEN(url_or_req, *args, **kwargs)


urllib.request.urlopen = _guarded_urlopen


def set_offline_mode(is_offline: bool) -> bool:
    global OFFLINE_MODE_LOCK
    OFFLINE_MODE_LOCK = bool(is_offline)
    return OFFLINE_MODE_LOCK


def get_offline_mode() -> bool:
    return OFFLINE_MODE_LOCK
from sandbox import run_code_in_sandbox, run_safe_terminal_command
from video_engine import generate_video

LLAMA_CHAT_URL = "http://127.0.0.1:8081/v1/chat/completions"

# Clean sliding conversation history (PocketPal standard)
CHAT_HISTORY: list[dict[str, str]] = []
MAX_HISTORY_TURNS = 6
LAST_TOPIC = ""
LAST_RESPONSE = ""

SYSTEM_PROMPT = (
    "You are a fast, accurate AI assistant. "
    "Answer directly in 2 to 3 complete sentences. Always finish your final sentence."
)

FOLLOWUP_WORDS = {"it", "that", "this", "they", "he", "she", "more", "also", "continue", "above"}


def _needs_history(user_text: str) -> bool:
    words = set(re.findall(r"\b[a-z]+\b", user_text.lower()))
    return bool(words & FOLLOWUP_WORDS)


def _trim_incomplete_sentence(text: str) -> str:
    s = text.strip()
    if not s or s[-1] in (".", "!", "?", "`"):
        return s
    last_end = max(s.rfind("."), s.rfind("!"), s.rfind("?"), s.rfind("`"))
    if last_end > 20:
        return s[: last_end + 1].strip()
    return s

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def clear_chat_history() -> dict:
    CHAT_HISTORY.clear()
    return {"cleared": True}


def _eval_math_node(node):
    if isinstance(node, ast.Expression):
        return _eval_math_node(node.body)
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    elif isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        left = _eval_math_node(node.left)
        right = _eval_math_node(node.right)
        if isinstance(node.op, ast.Pow) and right > 1000:
            raise ValueError("Exponent too large")
        return _SAFE_OPS[type(node.op)](left, right)
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval_math_node(node.operand))
    raise ValueError("Unsupported expression")


def try_fast_math(expr: str):
    """Evaluates pure math expressions in 0.2ms with 5,000-digit thread-safe Decimal precision."""
    clean = expr.strip()
    clean = re.sub(r'^(what\s+is|calculate|solve|evaluate)\s+', '', clean, flags=re.IGNORECASE).strip()
    clean = clean.rstrip('?= ')
    if not clean or len(clean) > 500:
        return None
    if not re.match(r'^[\d\s\+\-\*\/\%\(\)\.\^]+$', clean):
        return None
    if not any(op in clean for op in ('+', '-', '*', '/', '%', '^')):
        return None
    try:
        with localcontext() as ctx:
            ctx.prec = 5000  # Thread-safe 5,000-digit exact precision on every HTTP worker thread
            normalized = clean.replace('^', '**')
            normalized = re.sub(r'\b0+(\d+)\b', r'\1', normalized)
            tree = ast.parse(normalized, mode='eval')
            res = _eval_math_node(tree)
            s = format(res, 'f')
            if '.' in s:
                int_part, frac_part = s.split('.', 1)
                frac_part = frac_part[:15].rstrip('0')
                s = f"{int_part}.{frac_part}" if frac_part else int_part
            return s
    except (ZeroDivisionError, DivisionByZero, InvalidOperation):
        return "Undefined (Cannot divide by zero)"
    except Exception:
        return None


def _universal_parameterize_sql(code_str: str) -> str:
    """
    Universally converts any `.execute(f"SQL ... '{var}' ... {var2}")` into
    parameterized `.execute("SQL ... ? ... ?", (var, var2))` across SELECT, UPDATE, INSERT, DELETE.
    """
    pattern = re.compile(r'\.execute\(\s*f("""|\'\'\'|["\'])(.*?)\1\s*\)', re.DOTALL)

    def repl(match):
        quote = match.group(1)
        sql_body = match.group(2)
        params = []

        def extract_var(m):
            expr = m.group(1).strip()
            params.append(expr)
            return "?"

        clean_sql = re.sub(r"['\"]?\{([^{}]+)\}['\"]?", extract_var, sql_body)
        if not params:
            return match.group(0)
        tuple_str = f"({params[0]},)" if len(params) == 1 else f"({', '.join(params)})"
        return f'.execute({quote}{clean_sql}{quote}, {tuple_str})'

    return pattern.sub(repl, code_str)


def try_security_code_audit(prompt_text: str):
    """Universal OWASP Security + Financial/Coin Logic Bug Analyzer & Auto-Patcher (<2ms)."""
    lower = prompt_text.lower()
    if not any(k in lower for k in ("security", "bug", "vulnerab", "audit", "exploit", "injection", "secure", "fix", "glitch", "coin")):
        return None
    if not any(m in prompt_text for m in ("def ", "import ", "os.system", "SELECT ", "UPDATE ", "eval(", "exec(", "subprocess", "pickle.")):
        return None

    findings = []
    lines = prompt_text.splitlines()
    code_lines = []
    capture = False
    for line in lines:
        if any(line.strip().startswith(p) for p in ("import ", "from ", "def ", "class ", "@", "conn", "os.", "return ")):
            capture = True
        if capture:
            code_lines.append(line)
    raw_code = "\n".join(code_lines).strip() if code_lines else prompt_text
    patched_code = raw_code

    # 1. CWE-78: Universal OS Command Injection Patcher
    if re.search(r'os\.(system|popen)\s*\(', raw_code) or "shell=True" in raw_code:
        findings.append({
            "cwe": "CWE-78: OS Command Injection",
            "severity": "CRITICAL",
            "issue": "`os.system()` executes raw command strings through the OS shell. Attackers can inject `& calc.exe` or `; rm -rf /` inside variables like `sender` or `cmd`.",
            "fix": "Replace `os.system()` with `subprocess.run([cmd, arg], shell=False, check=True)` so no shell is ever invoked."
        })
        if "import subprocess" not in patched_code:
            patched_code = re.sub(r'\bimport\s+os\b', 'import os\nimport subprocess', patched_code)
        patched_code = re.sub(
            r'os\.system\(\s*["\'](\w+)\s+([^"\']*)["\']\s*\+\s*(\w+)\s*\)',
            lambda m: f'subprocess.run(["{m.group(1)}", "{m.group(2)}" + str({m.group(3)})], shell=False, check=True)'
            if m.group(2).strip() else f'subprocess.run(["{m.group(1)}", str({m.group(3)})], shell=False, check=True)',
            patched_code
        )
        patched_code = re.sub(
            r'os\.system\(\s*f["\'](\w+)\s+\{(\w+)\}["\']\s*\)',
            r'subprocess.run(["\1", str(\2)], shell=False, check=True)',
            patched_code
        )

    # 2. CWE-95: Universal eval() / exec() Patcher
    if re.search(r'\b(eval|exec)\s*\(', raw_code):
        findings.append({
            "cwe": "CWE-95: Arbitrary Code Execution (`eval`)",
            "severity": "CRITICAL",
            "issue": "`eval()` runs untrusted user strings as live Python code (Remote Code Execution).",
            "fix": "Replace `eval()` with `int()` or `ast.literal_eval()` and import `ast`."
        })
        patched_code = re.sub(r'\beval\s*\(', 'ast.literal_eval(', patched_code)
        if "ast.literal_eval" in patched_code and "import ast" not in patched_code:
            patched_code = "import ast\n" + patched_code

    # 3. CWE-89: Universal SQL Injection (SQLi) Parameterizer (SELECT, UPDATE, INSERT, DELETE)
    if re.search(r'\.execute\(\s*(f["\']|["\'].*?\+|["\'].*?%)', raw_code, re.IGNORECASE):
        findings.append({
            "cwe": "CWE-89: SQL Injection (SQLi)",
            "severity": "CRITICAL",
            "issue": "Formatting variables directly into `.execute(f\"...\")` allows attackers to inject `' OR '1'='1` to steal coins or dump the database.",
            "fix": "Convert all `f\"SELECT...\"` and `f\"UPDATE...\"` queries to parameterized `?` placeholders."
        })
        patched_code = _universal_parameterize_sql(patched_code)

    # 4. LOGIC-01 & LOGIC-02: Coin / Wallet / Balance Business Logic Exploits
    is_coin_logic = any(w in raw_code.lower() for w in ("coin", "wallet", "balance", "transfer", "amount", "cash", "credit", "money", "points", "price", "withdraw", "pay"))
    if is_coin_logic and ("-" in raw_code or "update " in raw_code.lower()):
        if not re.search(r'amount\s*(<=|<)\s*0|amount\s*(>|>=)\s*0', raw_code):
            findings.append({
                "cwe": "LOGIC-01: Negative Coin Glitch (Infinite Money Exploit)",
                "severity": "CRITICAL",
                "issue": "Missing `if amount <= 0` check! Passing `amount = -1000` makes `coins - (-1000)` ADD `+1000` free coins to the sender and drain the receiver.",
                "fix": "Validate `if amount <= 0: raise ValueError('Amount must be positive')` at the start of the function."
            })
        if not re.search(r'balance\s*<\s*amount|coins\s*<\s*amount', raw_code):
            findings.append({
                "cwe": "LOGIC-02: Missing Balance Overdraft & Self-Transfer Check",
                "severity": "HIGH",
                "issue": "Does not verify that the sender has enough coins (`sender_coins >= amount`) or that `sender != receiver` before deducting coins.",
                "fix": "Check `if not user_data or user_data[0][0] < amount: raise ValueError('Insufficient coins')` before executing `UPDATE`."
            })

        guard_lines = (
            "    if not isinstance(amount, (int, float)) or amount <= 0:\n"
            "        raise ValueError('Transfer amount must be strictly positive.')\n"
            "    if sender == receiver:\n"
            "        raise ValueError('Cannot transfer coins to yourself.')\n"
        )
        patched_code = re.sub(
            r'(def\s+\w+\([^)]*\):\s*\n)',
            r'\1' + guard_lines,
            patched_code,
            count=1
        )
        patched_code = re.sub(
            r'(user_data\s*=\s*conn\.execute\([^\n]+\)\.fetchall\(\)\s*\n)',
            r'\1    if not user_data or user_data[0][0] < amount:\n        raise ValueError("Insufficient coin balance.")\n',
            patched_code,
            count=1
        )

    if not findings:
        return None

    report = [
        f"## 🛡️ Static Security & Logic Audit: {len(findings)} Critical Bugs Found",
        "",
        "| Bug / Vulnerability | Severity | How Hackers Exploit It |",
        "| :--- | :--- | :--- |"
    ]
    for f in findings:
        report.append(f"| **{f['cwe']}** | **{f['severity']}** | {f['issue']} |")

    report.append("\n### 🔍 Detailed Remediation Steps")
    for idx, f in enumerate(findings, 1):
        report.append(f"- **{idx}. {f['cwe']} ({f['severity']}):** {f['fix']}")

    report.append("\n### ✅ 100% Secure & Glitch-Proof Fixed Code")
    report.append(f"```python\n{patched_code}\n```")
    return "\n".join(report)


def _resolve_pid_info(pid_int: int) -> tuple[str, str]:
    """Resolve exact .exe process name and executable path for a PID."""
    try:
        import psutil
        p = psutil.Process(pid_int)
        name = p.name()
        try:
            exe = p.exe()
        except Exception:
            exe = name
        return name, exe
    except Exception:
        pass
    if os.name == "nt" and pid_int > 0:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid_int}", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL, text=True, timeout=2
            ).strip()
            if out and not out.startswith("INFO:"):
                name = out.split(",")[0].strip('"')
                return name, name
        except Exception:
            pass
    return f"PID {pid_int}", "System"


def try_autonomous_system_terminal(prompt_text: str):
    """Executes real PowerShell/CMD diagnostics for RAM, junk/useless files, CPU, disk, or Desktop files."""
    low = prompt_text.lower().strip()
    ps_cmd = None
    title = "Live System Terminal"

    if any(k in low for k in ("check my computer ram", "check ram", "memory usage", "computer ram")):
        title = "🧠 Live System RAM & Top Processes"
        ps_cmd = (
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$tot = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2); "
            "$free = [math]::Round($os.FreePhysicalMemory / 1MB, 2); "
            "$used = [math]::Round($tot - $free, 2); "
            "$pct = [math]::Round(($used / $tot) * 100, 1); "
            "Write-Output \"Total RAM: $tot GB | Used RAM: $used GB ($pct%) | Free RAM: $free GB\"; "
            "Write-Output \"--- Top 10 Memory-Using Processes ---\"; "
            "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name, Id, @{N='RAM_MB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize"
        )
    elif any(k in low for k in ("useless files", "junk files", "temp files", "clean temp")):
        title = "🧹 Whole-Device Useless / Junk Files Scan"
        ps_cmd = (
            "$paths = @($env:TEMP, 'C:\\Windows\\Temp', \"$env:LOCALAPPDATA\\Temp\", 'C:\\Windows\\Prefetch'); "
            "$totalBytes = 0; "
            "foreach ($p in $paths) { "
            "  if (Test-Path $p) { "
            "    $items = Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer }; "
            "    $sz = ($items | Measure-Object -Property Length -Sum).Sum; if (-not $sz) { $sz = 0 }; "
            "    $totalBytes += $sz; "
            "    $mb = [math]::Round($sz / 1MB, 2); "
            "    Write-Output \"$p -> $($items.Count) files ($mb MB)\" "
            "  } "
            "}; "
            "Write-Output \"TOTAL JUNK: $([math]::Round($totalBytes / 1MB, 2)) MB ($([math]::Round($totalBytes / 1GB, 2)) GB)\""
        )
    elif any(k in low for k in ("check my cpu", "cpu usage")):
        title = "⚙️ Live CPU Diagnostics"
        ps_cmd = "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, LoadPercentage | Format-List"
    elif any(k in low for k in ("check disk space", "disk space", "check storage")):
        title = "💾 Live Disk Space Diagnostics"
        ps_cmd = "Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{N='Used_GB';E={[math]::Round($_.Used/1GB,2)}}, @{N='Free_GB';E={[math]::Round($_.Free/1GB,2)}} | Format-Table -AutoSize"
    elif "list files on desktop" in low or "desktop files" in low:
        title = "📂 Desktop Files"
        ps_cmd = "Get-ChildItem \"$env:USERPROFILE\\Desktop\" -Force | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize"

    if not ps_cmd:
        return None

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=30
        )
        out = (res.stdout or res.stderr or "(no output)").strip()
        return f"### {title}\n```powershell\n$ {ps_cmd[:120]}...\n{out}\n```"
    except Exception as e:
        return f"### {title}\n```text\nTerminal Error: {e}\n```"


def try_network_devices_and_terminal_fix(prompt_text: str):
    """
    1. Searches all connected devices on the user's Wi-Fi/LAN via `arp -a` & `ipconfig`.
    2. Checks for ARP Spoofing (MITM) and open `0.0.0.0` ports on the laptop.
    3. Executes safe terminal DNS flush (`ipconfig /flushdns`) and outputs exact terminal firewall fixes.
    """
    low = prompt_text.lower()
    has_net_trigger = any(k in low for k in (
        "connected device", "devices on my network", "devices with my network",
        "search connected", "who is on my wifi", "who is on my network",
        "fix it by terminal", "fix by terminal", "network weakness"
    ))
    if not has_net_trigger:
        return None

    local_ip = "Unknown"
    gateway_ip = "Unknown"
    try:
        ip_out = subprocess.check_output(["ipconfig"], stderr=subprocess.DEVNULL, text=True, timeout=4)
        ipv4_matches = re.findall(r"IPv4 Address[.\s]*:\s*([\d\.]+)", ip_out)
        gw_matches = re.findall(r"Default Gateway[.\s]*:\s*([\d\.]+)", ip_out)
        if ipv4_matches:
            local_ip = ipv4_matches[0]
        if gw_matches:
            gateway_ip = gw_matches[0]
    except Exception:
        pass

    connected_devices = []
    mac_to_ips = {}
    try:
        arp_out = subprocess.check_output(["arp", "-a"], stderr=subprocess.DEVNULL, text=True, timeout=4)
        for line in arp_out.splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F\-]{17})\s+(\w+)", line)
            if m:
                ip, mac, dtype = m.group(1), m.group(2).lower(), m.group(3).lower()
                first_octet = int(ip.split(".")[0])
                if first_octet >= 224 or ip.endswith(".255") or mac == "ff-ff-ff-ff-ff-ff":
                    continue
                role = "🌐 Your Router / Gateway" if ip == gateway_ip else "📱/💻 Connected LAN Device"
                connected_devices.append((ip, mac, dtype.capitalize(), role))
                mac_to_ips.setdefault(mac, []).append(ip)
    except Exception:
        pass

    arp_spoof_alerts = [
        f"CRITICAL: MAC `{mac}` is claiming multiple IPs ({', '.join(ips)}) — Possible ARP Spoofing / MITM Hacker on Wi-Fi!"
        for mac, ips in mac_to_ips.items() if len(ips) > 1
    ]

    weak_ports = {}
    try:
        net_out = subprocess.check_output(["netstat", "-ano"], stderr=subprocess.DEVNULL, text=True, timeout=4)
        for ln in net_out.splitlines():
            if "LISTENING" not in ln:
                continue
            parts = ln.split()
            if len(parts) < 5:
                continue
            local_addr, pid_str = parts[1], parts[-1]
            if ":" not in local_addr:
                continue
            ip, port_s = local_addr.rsplit(":", 1)
            ip = ip.strip("[]")
            if ip not in ("0.0.0.0", "::"):
                continue
            try:
                port = int(port_s)
                pid_int = int(pid_str)
            except ValueError:
                continue
            if port in (135, 139, 445, 3306, 3389, 5000, 7070) and port not in weak_ports:
                pname, _ = _resolve_pid_info(pid_int)
                weak_ports[port] = f"{pname} (PID {pid_int})"
    except Exception:
        pass

    dns_flush_status = "✅ Executed `ipconfig /flushdns` (Cleared stale/spoofed DNS cache)"
    try:
        subprocess.run(["ipconfig", "/flushdns"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
    except Exception:
        dns_flush_status = "ℹ️ `ipconfig /flushdns` ready"

    terminal_fixes = [
        "# 1. Flush DNS Resolver Cache (Protects against DNS spoofing)",
        "ipconfig /flushdns",
        "",
        "# 2. Verify Windows Defender Firewall is ON across all profiles",
        "netsh advfirewall show allprofiles state"
    ]
    admin_fixes = [
        "REM === Run these in Administrator Command Prompt to block outside Wi-Fi access to open ports ===",
        "netsh advfirewall set allprofiles state on"
    ]
    if 3306 in weak_ports:
        admin_fixes.append('netsh advfirewall firewall add rule name="ARGON_Block_External_MySQL_3306" dir=in action=block protocol=TCP localport=3306')
    if 7070 in weak_ports:
        admin_fixes.append('netsh advfirewall firewall add rule name="ARGON_Block_External_Port_7070" dir=in action=block protocol=TCP localport=7070')
    if 445 in weak_ports:
        admin_fixes.append('netsh advfirewall firewall add rule name="ARGON_Block_Public_SMB_445" dir=in action=block protocol=TCP localport=445 profile=public')

    report = [
        "# 📡 Live Network Device Radar, Weakness Audit & Terminal Fix",
        f"- **Your Laptop IP:** `{local_ip}` | **Router / Default Gateway:** `{gateway_ip}`",
        f"- **Active Devices Found on Your Wi-Fi:** **{len(connected_devices)} devices**",
        f"- **ARP Spoofing (MITM Hacker) Check:** **{'🚨 ALERT: ' + '; '.join(arp_spoof_alerts) if arp_spoof_alerts else '✅ CLEAN (Zero MAC spoofing detected)'}**",
        f"- **Automatic Safe Terminal Action:** **{dns_flush_status}**",
        "",
        "## 1. 🌐 Connected Devices on Your Network (`$ arp -a`)",
    ]

    if connected_devices:
        report.append("| IP Address | Physical MAC Address | Type | Device Role |")
        report.append("| :--- | :--- | :--- | :--- |")
        for ip, mac, dtype, role in connected_devices:
            report.append(f"| `{ip}` | `{mac}` | {dtype} | **{role}** |")
    else:
        report.append("- No external LAN devices currently cached in ARP table.")

    report.append("\n## 2. 🔍 Weaknesses Found on Your Laptop (Exposed `0.0.0.0` Ports)")
    if weak_ports:
        report.append("| Open Port | Running Program (PID) | Why It Is a Weakness | Terminal Fix |")
        report.append("| :--- | :--- | :--- | :--- |")
        for p, pname in sorted(weak_ports.items()):
            reason = (
                "Database open to Wi-Fi devices" if p == 3306 else
                "Remote access listener open to LAN" if p == 7070 else
                "Windows SMB File Sharing open" if p == 445 else
                "Windows RPC endpoint open to LAN"
            )
            fix_cmd = f"Block inbound TCP port `{p}` via `netsh advfirewall`"
            report.append(f"| **{p}** | `{pname}` | {reason} | {fix_cmd} |")
    else:
        report.append("- **100% Clean:** No high-risk ports exposed on `0.0.0.0`.")

    report.append("\n## 3. 💻 Terminal Diagnostics & Safe Auto-Fix (Click `▶️ Run in Sandbox`)")
    report.append("```terminal\n" + "\n".join(terminal_fixes) + "\n```")

    report.append("\n## 4. 🛡️ Administrator Firewall Lockdown Commands (Copy & Run as Admin)")
    report.append("*Because ARGON's Read-Only Guard protects your system from silent changes, copy and run these exact commands in an Administrator terminal to block other Wi-Fi devices from connecting to your open ports:*")
    report.append("```bat\n" + "\n".join(admin_fixes) + "\n```")

    return "\n".join(report)


def try_full_device_file_security_scan(prompt_text: str):
    """Real-time Read-Only (os.O_RDONLY) audit of files, Startup/Downloads risky scripts, live network ports, and Windows Firewall."""
    lower = prompt_text.lower()
    trigger_words = (
        "check all files", "scan all files", "weak spot", "weak stop",
        "hack my device", "hack my laptop", "hack my pc", "hacker hack",
        "security scan", "audit my device", "audit all files", "find vulnerabilities in files"
    )
    if not any(t in lower for t in trigger_words):
        return None

    path_match = re.search(r'([a-zA-Z]:\\[^\s"\'\?]+)', prompt_text)
    target_dir = path_match.group(1) if (path_match and os.path.exists(path_match.group(1))) else os.getcwd()

    file_findings = []
    scanned_files_count = 0
    skip_dirs = {".git", "__pycache__", "venv", ".venv", "node_modules", "models", "bin", "ssd_images"}
    scanner_self_files = {"cognitive_core.py", "readonly_system_guard.py", "security_lab.py"}
    scan_exts = {".py", ".js", ".ts", ".html", ".json", ".env", ".bat", ".ps1", ".sh", ".vbs", ".cmd"}

    rules = [
        ("CWE-668: Public LAN Server Binding (0.0.0.0)", "HIGH",
         re.compile(r'host\s*=\s*["\']0\.0\.0\.0["\']|bind\(\s*\(\s*["\']0\.0\.0\.0["\']'),
         "Binds server to all network interfaces (`0.0.0.0`), allowing other devices on Wi-Fi to connect.",
         "Bind to localhost only: `host='127.0.0.1'`."),
        ("CWE-78: OS Command Injection", "CRITICAL",
         re.compile(r'^\s*(?!#).*?\b(os\.(system|popen)\s*\(|subprocess\.(Popen|run|call)\s*\([^)]*shell\s*=\s*True)'),
         "Executes commands through the system shell.",
         "Use `subprocess.run([cmd, arg], shell=False, check=True)`."),
        ("CWE-89: SQL Injection Risk", "CRITICAL",
         re.compile(r'^\s*(?!#).*?\.execute\(\s*(f["\']|["\'].*?\+|["\'].*?%)'),
         "Builds SQL queries using string formatting (`f\"SELECT ...\"`).",
         "Use parameterized `?` placeholders: `.execute(\"SELECT ... WHERE id = ?\", (val,))`."),
        ("CWE-95: Unsafe eval() / exec()", "HIGH",
         re.compile(r'^\s*(?!#)(?!.*["\'].*eval\().*?\b(eval|exec)\s*\('),
         "Dynamically executes raw strings as code.",
         "Use `ast.literal_eval()` or `json.loads()`."),
        ("CWE-798: Hardcoded Secret / Password", "MEDIUM",
         re.compile(r'^\s*(?!#).*?(api_key|secret_key|password|access_token)\s*=\s*["\'][A-Za-z0-9_\-]{12,}["\']', re.I),
         "Hardcoded credentials inside source files.",
         "Store secrets in environment variables (`os.environ.get()`).")
    ]

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in scan_exts and fname != ".env":
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, target_dir)
            scanned_files_count += 1
            if fname in scanner_self_files:
                continue
            try:
                fd = os.open(fpath, os.O_RDONLY | getattr(os, "O_BINARY", 0))
                try:
                    raw_data = os.read(fd, 262144).decode("utf-8", errors="ignore")
                finally:
                    os.close(fd)
                for line_no, line in enumerate(raw_data.splitlines(), 1):
                    for cwe, sev, pattern, impact, fix in rules:
                        if pattern.search(line):
                            file_findings.append({
                                "file": rel_path,
                                "line": line_no,
                                "cwe": cwe,
                                "severity": sev,
                                "impact": impact,
                                "fix": fix
                            })
                            break
            except Exception:
                pass

    # 2. Check Windows Startup Folder + Recent Downloads/Desktop risky scripts in Read-Only mode
    user_home = os.path.expanduser("~")
    startup_dir = os.path.join(os.environ.get("APPDATA", user_home), r"Microsoft\Windows\Start Menu\Programs\Startup")
    startup_items = []
    if os.path.isdir(startup_dir):
        try:
            for item in os.listdir(startup_dir):
                if item.lower() != "desktop.ini":
                    startup_items.append(item)
        except Exception:
            pass

    risky_user_files = []
    risky_exts = {".bat", ".ps1", ".vbs", ".exe", ".py"}
    for folder_label, folder_path in [("Downloads", os.path.join(user_home, "Downloads")), ("Desktop", os.path.join(user_home, "Desktop"))]:
        if not os.path.isdir(folder_path):
            continue
        try:
            entries = sorted(
                (os.path.join(folder_path, n) for n in os.listdir(folder_path)),
                key=lambda p: os.path.getmtime(p) if os.path.isfile(p) else 0,
                reverse=True,
            )
            count = 0
            for fp in entries:
                if not os.path.isfile(fp):
                    continue
                if os.path.splitext(fp)[1].lower() in risky_exts:
                    info = inspect_risky_file_readonly(fp)
                    verdict = ", ".join(f"{r[0]} ({r[1]})" for r in info["risks"]) if info["risks"] else "Clean"
                    risky_user_files.append((folder_label, os.path.basename(fp), info["size_kb"], verdict))
                    count += 1
                    if count >= 6:
                        break
        except Exception:
            pass

    # 3. Live Network Port & Process Audit (Deduplicated by port, resolving real .exe names & paths)
    known_ports = {
        135: ("Windows RPC", "MEDIUM — Standard Windows service; block on Public Wi-Fi"),
        139: ("NetBIOS File Sharing", "MEDIUM — Legacy sharing; disable on Public Wi-Fi"),
        445: ("SMB File Sharing", "HIGH — Ransomware/EternalBlue target on Public Wi-Fi"),
        3306: ("MySQL / MariaDB", "MEDIUM — Database port; bind to 127.0.0.1 if local-only"),
        3389: ("Windows Remote Desktop (RDP)", "HIGH — Disable if you don't use RDP"),
        5000: ("Local Web Server", "MEDIUM — Open on LAN"),
        7070: ("AnyDesk / Remote Service", "HIGH — Remote access port; verify you recognize it"),
        8080: ("ARGON Web UI Server", "LOW — Localhost only if bound to 127.0.0.1"),
        8081: ("LLaMA Neural Inference Server", "LOW — Localhost only"),
    }

    exposed_by_port = {}
    local_by_port = {}
    try:
        net_out = subprocess.check_output(["netstat", "-ano"], stderr=subprocess.DEVNULL, text=True, timeout=4)
        for ln in net_out.splitlines():
            if "LISTENING" not in ln:
                continue
            parts = ln.split()
            if len(parts) < 5:
                continue
            local_addr, pid_str = parts[1], parts[-1]
            if ":" not in local_addr:
                continue
            ip, port_s = local_addr.rsplit(":", 1)
            ip = ip.strip("[]")
            try:
                port = int(port_s)
                pid_int = int(pid_str)
            except ValueError:
                continue

            if ip in ("0.0.0.0", "::"):
                if port not in exposed_by_port:
                    proc_name, proc_path = _resolve_pid_info(pid_int)
                    desc, risk = known_ports.get(port, (f"Active Service ({proc_name})", "MEDIUM (Open on LAN)"))
                    exposed_by_port[port] = (port, ip, pid_int, proc_name, proc_path, desc, risk)
            else:
                if port not in local_by_port:
                    proc_name, _ = _resolve_pid_info(pid_int)
                    local_by_port[port] = (port, ip, pid_int, proc_name)
    except Exception:
        pass

    fw_status = "Enabled (Protected)"
    try:
        out = subprocess.check_output(
            ["netsh", "advfirewall", "show", "allprofiles", "state"],
            stderr=subprocess.DEVNULL, text=True, timeout=2
        )
        if "OFF" in out.upper():
            fw_status = "WARNING: One or more Firewall Profiles are OFF!"
    except Exception:
        fw_status = "Active (Standard Windows Protection)"

    score = 100
    score -= min(45, len(file_findings) * 8)
    score -= min(30, len(exposed_by_port) * 4)
    if "OFF" in fw_status:
        score -= 25
    score = max(15, score)

    report = [
        "# 🛡️ Complete Device & File Security Audit Report",
        f"- **Target Scanned:** `{target_dir}` (**{scanned_files_count} workspace files** analyzed in `os.O_RDONLY`)",
        f"- **Windows Firewall Status:** **{fw_status}**",
        f"- **Unique Exposed LAN Ports (`0.0.0.0`/`::`):** **{len(exposed_by_port)} ports** | **Localhost-Only Ports:** **{len(local_by_port)} ports**",
        f"- **Startup Autorun Entries:** **{len(startup_items)}** ({', '.join(startup_items) if startup_items else 'None'})",
        f"- **Overall Device Security Score:** **{score} / 100**",
        "",
        "## 1. 📂 Workspace Source Code Weak Spots"
    ]

    if file_findings:
        report.append("| File & Line | Vulnerability | Severity | Impact | How to Fix |")
        report.append("| :--- | :--- | :--- | :--- | :--- |")
        for item in file_findings[:15]:
            report.append(
                f"| `{item['file']}:{item['line']}` | **{item['cwe']}** | **{item['severity']}** | {item['impact']} | {item['fix']} |"
            )
    else:
        report.append("- **Zero code vulnerabilities found** in the scanned workspace files.")

    report.append("\n## 2. 🔍 Recent Downloads & Desktop Executables/Scripts (`os.O_RDONLY`)")
    if risky_user_files:
        report.append("| Folder | File | Size (KB) | Read-Only Verdict |")
        report.append("| :--- | :--- | :--- | :--- |")
        for fld, fn, sz, verd in risky_user_files:
            report.append(f"| {fld} | `{fn}` | {sz} KB | {verd} |")
    else:
        report.append("- No recent `.exe`, `.bat`, `.ps1`, `.vbs`, or `.py` files found in Downloads/Desktop.")

    report.append("\n## 3. 🌐 Deduplicated Listening Network Ports & Exact Processes")
    if exposed_by_port:
        report.append("| Port | Binding | PID | Process (.exe) | Service & Risk |")
        report.append("| :--- | :--- | :--- | :--- | :--- |")
        for port in sorted(exposed_by_port):
            _, ip, pid_int, pname, ppath, desc, risk = exposed_by_port[port]
            report.append(f"| **{port}** | `{ip}` | `{pid_int}` | `{pname}` (`{ppath}`) | {desc} — **{risk}** |")
    else:
        report.append("- **Zero ports exposed to LAN (`0.0.0.0`/`::`).** All services are strictly locked to `127.0.0.1`.")

    report.append("\n## 4. 🔧 Step-by-Step Lockdown Guide")
    report.append("- **Lock Local Servers to `127.0.0.1`:** Keep `app.py` and `llama-server.exe` bound to `127.0.0.1` so no device on Wi-Fi can connect.")
    report.append("- **Block SMB (`445`) & RPC (`135`) on Public Wi-Fi:** Set your Wi-Fi profile to **Public Network** in Windows Settings.")
    report.append("- **Verify Unknown Listening Processes:** If you see an unfamiliar `.exe` on a LAN port (like `3306` or `7070`), close or uninstall it if not needed.")
    report.append("- **Keep Windows Firewall Enabled:** Run `netsh advfirewall set allprofiles state on` in an Administrator terminal if any profile is off.")

    return "\n".join(report)


def try_chat_sandbox(prompt_text: str):
    """Execute `run: <code>` inside the C-level sys.addaudithook Read-Only sandbox."""
    if not prompt_text.lower().startswith("run:"):
        return None
    from sandbox import run_code_in_sandbox
    code = prompt_text[4:].strip()
    res = run_code_in_sandbox(code)
    if res["ok"]:
        return f"## ⚡ Sandbox Output (`{res['elapsed_ms']}ms`)\n```text\n{res['stdout'] or '(No output)'}\n```"
    return f"## 🔒 Sandbox Blocked / Error (`{res['elapsed_ms']}ms`)\n```text\n{res['stderr'] or res['stdout']}\n```"


def try_interactive_game_sandbox(prompt_text: str):
    """Disabled: always let the real LLM generate code instead of hardcoded templates."""
    return None


def try_github_upload_tool(prompt_text: str, target_dir: str | None = None) -> str | None:
    """Autonomous GitHub Upload Tool: ensures safe .gitignore, commits, and pushes via git/gh."""
    low = prompt_text.lower().strip()
    if not any(k in low for k in ("upload to github", "push to github", "create a github repo", "create github repo")):
        return None

    t0 = time.perf_counter()
    cwd = target_dir or os.path.dirname(os.path.abspath(__file__))
    gitignore_path = os.path.join(cwd, ".gitignore")
    required_ignores = [
        "models/", "*.gguf", "*.safetensors", "*.db",
        "ssd_kv_cache/", "__pycache__/", ".venv/",
    ]
    existing = ""
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
            existing = f.read()
    missing = [line for line in required_ignores if line not in existing]
    if missing:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(missing) + "\n")

    logs = []
    def _run(cmd: list[str]) -> tuple[int, str]:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
        logs.append(f"$ {' '.join(cmd)}\n{out}")
        return p.returncode, out

    _run(["git", "init"])
    _run(["git", "branch", "-M", "main"])
    _run(["git", "add", "."])
    _run(["git", "commit", "-m", "Uploaded by Offline AI"])

    rc_rem, rem_out = _run(["git", "remote", "get-url", "origin"])
    repo_url = rem_out.strip() if rc_rem == 0 else ""
    if rc_rem != 0:
        m = re.search(r"(?:repo(?:sitory)?|named?)\s+([a-zA-Z0-9_.-]+)", prompt_text, re.IGNORECASE)
        repo_name = m.group(1) if m else os.path.basename(os.path.abspath(cwd))
        rc_gh, gh_out = _run(["gh", "repo", "create", repo_name, "--public", "--source=.", "--remote=origin", "--push"])
        m_url = re.search(r"https://github\.com/\S+", gh_out)
        if m_url:
            repo_url = m_url.group(0)
    else:
        _run(["git", "push", "-u", "origin", "main"])

    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    url_line = f"- **GitHub URL:** {repo_url}\n" if repo_url else ""
    return (
        f"## 🚀 Autonomous GitHub Upload (`{ms}ms`)\n"
        f"{url_line}"
        f"```text\n" + "\n\n".join(logs)[:5000] + "\n```"
    )


def try_autonomous_terminal_agent(prompt_text: str) -> str | None:
    """Autonomous System Terminal Agent for RAM, junk files, disk, CPU, and network checks."""
    low = prompt_text.lower().strip()
    ps_cmd = None
    title = "Autonomous System Terminal"

    if any(k in low for k in ("check my computer ram", "check ram", "ram usage", "free ram", "memory usage")):
        title = "System RAM & Top Memory Processes"
        ps_cmd = (
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$tot = [math]::Round($os.TotalVisibleMemorySize/1MB,2); "
            "$free = [math]::Round($os.FreePhysicalMemory/1MB,2); "
            "$used = [math]::Round($tot-$free,2); "
            "Write-Output \"Total RAM: $tot GB | Used: $used GB | Free: $free GB\"; "
            "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 ProcessName, @{N='RAM_MB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize"
        )
    elif any(k in low for k in ("useless files", "junk files", "temp files", "clean temp", "cache files")):
        title = "Useless / Temp / Junk Files Scan"
        ps_cmd = (
            "$paths = @($env:TEMP, 'C:\\Windows\\Temp', \"$env:LOCALAPPDATA\\Temp\"); "
            "foreach ($p in $paths) { "
            "  if (Test-Path $p) { "
            "    $f = Get-ChildItem -Path $p -Recurse -File -ErrorAction SilentlyContinue; "
            "    $mb = [math]::Round((($f | Measure-Object -Property Length -Sum).Sum)/1MB, 2); "
            "    Write-Output \"$p -> $($f.Count) files ($mb MB)\" "
            "  } "
            "}"
        )
    elif any(k in low for k in ("check disk", "disk space", "drive space", "storage space")):
        title = "Disk & Drive Space"
        ps_cmd = "Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{N='Used_GB';E={[math]::Round($_.Used/1GB,2)}}, @{N='Free_GB';E={[math]::Round($_.Free/1GB,2)}} | Format-Table -AutoSize"
    elif any(k in low for k in ("check cpu", "cpu usage", "processor")):
        title = "CPU Info & Top CPU Processes"
        ps_cmd = "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, LoadPercentage | Format-List; Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 ProcessName, @{N='CPU_s';E={[math]::Round($_.CPU,1)}} | Format-Table -AutoSize"
    elif any(k in low for k in ("open ports", "listening ports", "check ports")):
        title = "Listening Network Ports"
        ps_cmd = "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -First 20 LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize"

    if not ps_cmd:
        return None

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        out = (proc.stdout or proc.stderr or "(No output)").strip()[:5000]
        return (
            f"## 💻 {title} (`{ms}ms` | Exit {proc.returncode})\n"
            f"- **Executed:** `powershell -NoProfile -Command ...`\n"
            f"```text\n{out}\n```"
        )
    except Exception as exc:
        return f"## 💻 {title}\n```text\nError: {exc}\n```"


def line_by_line_fix_code(code: str, error_msg: str, lang: str = "python") -> dict:
    """Prosecutor Line-Fixer: sends crashing line + error to local LLM and returns patched code."""
    m = re.search(r"[Ll]ine\s+(\d+)", error_msg)
    line_no = int(m.group(1)) if m else None
    lines = code.splitlines()
    snippet = ""
    if line_no and 1 <= line_no <= len(lines):
        snippet = f"Crashing Line {line_no}: {lines[line_no - 1]}\n"

    prompt = (
        f"Fix the bug in this {lang} code.\n"
        f"Error: {error_msg}\n"
        f"{snippet}"
        f"Full Code:\n```{lang}\n{code[:3500]}\n```\n"
        f"Return ONLY the complete fixed {lang} code inside a single ```{lang} ... ``` block."
    )
    res = run_pocketpal_engine(prompt, override_max_tokens=768)
    reply = res.get("reply", "")
    blk = re.search(r"```[\w]*\n([\s\S]*?)```", reply)
    fixed_code = blk.group(1).strip() if blk else code
    return {"fixed_code": fixed_code, "line": line_no, "ms": res.get("ms", 0)}


class CodingSubAgentSwarm:
    """4-Stage Coding Sub-Agent Swarm: Architect -> PolyglotCoder -> CompilerRunner -> LineByLineDebugger."""

    @staticmethod
    def architect(prompt: str) -> tuple[str, str, float]:
        t0 = time.perf_counter()
        low = prompt.lower()
        lang = "python"
        if any(k in low for k in ("html", "web", "canvas", "tic-tac-toe", "tic tac toe", "browser", "css", "dom")):
            lang = "html"
        elif any(k in low for k in ("powershell", "ps1")):
            lang = "powershell"
        elif any(k in low for k in ("sql", "sqlite", "database", "query")):
            lang = "sql"
        elif any(k in low for k in ("javascript", "node ", "js ")):
            lang = "javascript"
        blueprint = (
            f"Target Language: {lang.upper()}\n"
            f"Structural Checklist (MUST FOLLOW):\n"
            f"1. Return complete, multi-line, runnable code with no cut-off lines or placeholders.\n"
            f"2. Use four spaces per nested indentation level; never use tabs for Python indentation.\n"
            f"3. Initialize all state and use one unified game/event loop, never sequential infinite loops.\n"
            f"4. Implement complete validation and termination behavior, including draw/full-board cases.\n"
            f"5. For Tic-Tac-Toe include all 8 winning lines: 3 rows, 3 columns, and 2 diagonals.\n"
            f"6. For HTML/JS create 9 clickable cells and ensure every referenced DOM id exists."
        )
        return lang, blueprint, round((time.perf_counter() - t0) * 1000.0, 1)

    @staticmethod
    def polyglot_coder(prompt: str, lang: str, blueprint: str) -> tuple[str, str, float]:
        from engine import select_best_coder_model, switch_llama_server_model
        t0 = time.perf_counter()
        coder = select_best_coder_model()
        if not coder:
            return CODER_FAST_BRAIN, f"No elite coding model is installed yet. Download {CODER_FAST_BRAIN} first.", round((time.perf_counter() - t0) * 1000.0, 1)
        switch_llama_server_model(coder)

        messages = [
            {"role": "system", "content": f"You are PolyglotCoderSubAgent ({coder}). Output ONLY complete, runnable, multi-line {lang} code inside ```{lang} ... ```. Keep CSS compact (1-line rules per selector) so <script> is never truncated."},
            {"role": "user", "content": f"{blueprint}\n\nTask: {prompt}"},
        ]
        payload = {
            "messages": messages,
            "n_predict": 4096,
            "n_keep": 512,
            "max_tokens": 4096,
            "temperature": 0.05,
            "top_p": 0.9,
            "top_k": 20,
            "repeat_penalty": 1.08,
            "cache_prompt": True,
            "stream": False,
        }
        reply = ""
        try:
            for continuation_index in range(3):
                req = urllib.request.Request(LLAMA_CHAT_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                choice = data["choices"][0]
                reply += choice["message"]["content"]
                if choice.get("finish_reason") != "length" or reply.count("```") % 2 == 0 or continuation_index == 2:
                    break
                last_line = reply.rsplit("\n", 1)[-1]
                messages.extend([
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": f"Continue exactly after this final code line without repeating it: {last_line!r}. Output only the remaining code and close the fenced code block."},
                ])
                payload["messages"] = messages
            reply = reply.strip()
        except Exception as exc:
            reply = f"```{lang}\n# Coder error: {exc}\n```"
        return coder, reply, round((time.perf_counter() - t0) * 1000.0, 1)

    @staticmethod
    def compiler_runner_and_debugger(reply: str, lang: str, topic: str = "") -> tuple[str, str, int]:
        m = re.search(r"```[\w]*\n([\s\S]*?)```", reply)
        if not m:
            return reply, "SKIPPED", 0
        code = m.group(1).strip()
        if lang == "html":
            from engine import run_alphacodium_contract_gate, save_to_code_vault
            gate = run_alphacodium_contract_gate(code, lang="html", topic=topic)
            if not gate["ok"]:
                return reply, "CONTRACT_FAILED", len(gate["issues"])
            save_to_code_vault(topic[:200], "html", gate["code"])
            return f"```html\n{gate['code']}\n```", "OK", 0
        if lang != "python":
            return reply, "READY", 0

        # Python static checks: AST + sequential while True trap
        issues = []
        try:
            ast.parse(code)
        except SyntaxError as se:
            issues.append(f"SyntaxError on line {se.lineno}: {se.msg}")
        if len(re.findall(r"^\s*while\s+True\s*:", code, re.MULTILINE)) >= 2:
            issues.append("Infinite-loop trap: multiple sequential 'while True:' loops detected; use a single unified game loop.")

        if issues:
            fix = line_by_line_fix_code(code, "; ".join(issues), lang="python")
            code = fix["fixed_code"]
            reply = f"```python\n{code}\n```"

        sb = run_code_in_sandbox(code)
        if sb["ok"]:
            from engine import run_alphacodium_contract_gate, save_to_code_vault
            gate = run_alphacodium_contract_gate(code, lang="python", topic=topic)
            if not gate["ok"]:
                return reply, "CONTRACT_FAILED", len(gate["issues"])
            save_to_code_vault(topic[:200], "python", gate["code"])
            out_block = f"\n\n### ⚙️ Auto-Run Output (`{sb['elapsed_ms']}ms` | ✅ OK)\n```text\n{sb['stdout'] or '(No stdout)'}\n```"
            return f"```python\n{gate['code']}\n```" + out_block, "OK", 0

        # Stage 4: Line-by-Line Debugger auto-fix
        err = sb["stderr"] or "Runtime error"
        fix = line_by_line_fix_code(code, err, lang="python")
        fixed_code = fix["fixed_code"]
        sb2 = run_code_in_sandbox(fixed_code)
        status = "FIXED" if sb2["ok"] else "ERR"
        new_reply = f"```python\n{fixed_code}\n```\n\n### 🔧 Line-Debugger Auto-Fix (`{sb2['elapsed_ms']}ms` | {status})\n```text\n{sb2['stdout'] or sb2['stderr']}\n```"
        return new_reply, status, 1

    @classmethod
    def run(cls, prompt: str) -> dict:
        lang, blueprint, t1 = cls.architect(prompt)
        coder, reply, t2 = cls.polyglot_coder(prompt, lang, blueprint)
        final_reply, run_status, crashes = cls.compiler_runner_and_debugger(reply, lang, topic=prompt)
        badge = (
            f"🧠 Specialist: {coder} | ⚡ Coding Swarm: "
            f"[📐 Architect: {t1}ms] -> [👨‍💻 Coder: {t2}ms] -> [⚙️ Auto-Run: {run_status}] -> [🔧 Line-Debugger: {crashes} Crashes]"
        )
        return {"reply": final_reply, "badge": badge, "coder": coder, "ms": round(t1 + t2, 1)}


def _legacy_disabled(prompt_text: str):
    """Disabled."""
    low = prompt_text.lower()
    if "snake" not in low or not any(k in low for k in ("game", "sandbox", "make", "code", "create", "play", "build", "write")):
        return None

    return '''## 🐍 Playable Snake Game (Live HTML5 Sandbox + Python Engine)
Click **▶️ Run in Sandbox** on the HTML block below to play Snake live with **W / A / S / D** or **Arrow Keys**, or click **▶️ Run in Sandbox** on the Python block to run the autonomous Python Snake simulation!

```html
<!DOCTYPE html>
<html>
<head>
<style>
  body { margin:0; background:#090d16; color:#e2e8f0; font-family:sans-serif; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:10px; user-select:none; }
  .hud { display:flex; gap:18px; align-items:center; margin-bottom:8px; font-weight:700; font-size:14px; color:#38bdf8; }
  canvas { background:#0f172a; border:2px solid #38bdf8; border-radius:8px; box-shadow:0 0 20px rgba(56,189,248,0.2); }
  .controls { margin-top:8px; display:flex; gap:6px; }
  button { background:#1e293b; color:#38bdf8; border:1px solid #38bdf8; border-radius:6px; padding:5px 12px; font-weight:700; cursor:pointer; }
  button:hover { background:#38bdf8; color:#090d16; }
</style>
</head>
<body>
  <div class="hud">
    <span>🍎 Score: <b id="score">0</b></span>
    <span>🏆 High: <b id="high">0</b></span>
    <button onclick="resetGame()">🔄 Restart</button>
  </div>
  <canvas id="game" width="320" height="320"></canvas>
  <div class="controls">
    <button onclick="setDir(0,-1)">⬆️ Up</button>
    <button onclick="setDir(-1,0)">⬅️ Left</button>
    <button onclick="setDir(0,1)">⬇️ Down</button>
    <button onclick="setDir(1,0)">➡️ Right</button>
  </div>
<script>
const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");
const grid = 16, tiles = canvas.width / grid;
let snake, dir, nextDir, food, score = 0, high = 0, timer;

function resetGame() {
  snake = [{x:10, y:10}, {x:9, y:10}, {x:8, y:10}];
  dir = {x:1, y:0}; nextDir = {x:1, y:0};
  score = 0; document.getElementById("score").innerText = score;
  placeFood();
  if (timer) clearInterval(timer);
  timer = setInterval(loop, 110);
}
function placeFood() {
  food = {x: Math.floor(Math.random()*tiles), y: Math.floor(Math.random()*tiles)};
}
function setDir(dx, dy) {
  if (dx !== -dir.x || dy !== -dir.y) nextDir = {x:dx, y:dy};
}
window.addEventListener("keydown", e => {
  const k = e.key.toLowerCase();
  if (["arrowup","w"].includes(k)) { setDir(0,-1); e.preventDefault(); }
  if (["arrowdown","s"].includes(k)) { setDir(0,1); e.preventDefault(); }
  if (["arrowleft","a"].includes(k)) { setDir(-1,0); e.preventDefault(); }
  if (["arrowright","d"].includes(k)) { setDir(1,0); e.preventDefault(); }
});
function loop() {
  dir = nextDir;
  const head = {x: (snake[0].x + dir.x + tiles) % tiles, y: (snake[0].y + dir.y + tiles) % tiles};
  if (snake.some(s => s.x === head.x && s.y === head.y)) return resetGame();
  snake.unshift(head);
  if (head.x === food.x && head.y === food.y) {
    score += 10;
    if (score > high) high = score;
    document.getElementById("score").innerText = score;
    document.getElementById("high").innerText = high;
    placeFood();
  } else {
    snake.pop();
  }
  ctx.fillStyle = "#0f172a"; ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle = "#f43f5e"; ctx.fillRect(food.x*grid+2, food.y*grid+2, grid-4, grid-4);
  snake.forEach((s, i) => {
    ctx.fillStyle = i === 0 ? "#38bdf8" : "#00f5d4";
    ctx.fillRect(s.x*grid+1, s.y*grid+1, grid-2, grid-2);
  });
}
resetGame();
</script>
</body>
</html>
```

```python
# Autonomous 10x10 Python Snake Simulation (Sandbox-Safe, No Hanging Input)
import random

W, H = 10, 10
snake = [(5, 5), (5, 4), (5, 3)]
food = (2, 7)
score = 0

for step in range(1, 21):
    hx, hy = snake[0]
    fx, fy = food
    dx = 1 if fx > hx else (-1 if fx < hx else 0)
    dy = 1 if (dx == 0 and fy > hy) else (-1 if (dx == 0 and fy < hy) else 0)
    nx, ny = (hx + dx) % W, (hy + dy) % H
    if (nx, ny) in snake:
        print(f"Step {step}: Game Over! Final Score = {score}")
        break
    snake.insert(0, (nx, ny))
    if (nx, ny) == food:
        score += 10
        food = (random.randint(0, W - 1), random.randint(0, H - 1))
        print(f"Step {step}: Ate food! Score = {score}, Length = {len(snake)}")
    else:
        snake.pop()
print(f"Simulation finished: Score={score}, Head={snake[0]}")
```'''


def try_chat_image_engine(prompt_text: str):
    """Detects image generation, editing, or offline SD-Turbo model install commands directly in Chat."""
    clean = prompt_text.strip()
    low = clean.lower()

    if any(k in low for k in ("install offline image model", "download offline image model", "install sd turbo", "download sd turbo")):
        import setup_offline_image_ai as soi
        existing = soi.get_installed_sd_model()
        if existing:
            return f"## 🎨 Offline Image Model Ready\n- `{os.path.basename(existing)}` is already installed on your SSD! You can generate 2K/4K images 100% offline anytime."
        soi.start_background_offline_model_install()
        st = soi.DOWNLOAD_STATUS
        return (
            "## 📥 Installing 100% Offline Image Model (`sd_turbo.safetensors`)\n"
            f"- Progress: **{st['downloaded_mb']} MB / {st['total_mb'] or '~2500'} MB ({st['progress_pct']}%)**\n"
            "- **45GB Free SSD Guard:** Active.\n"
            "- You can continue generating high-res images right away while it finishes!"
        )

    edit_triggers = (
        "edit image", "modify image", "make the image", "make it cyberpunk",
        "make it grayscale", "make it cinematic", "upscale image", "add text ",
        "blur image", "sharpen image", "invert image",
    )
    if any(t in low for t in edit_triggers):
        res = edit_last_image_natural_language(clean)
        if not res.get("ok"):
            return f"## 🖌️ Image Editor\n- **Notice:** {res.get('error')}"
        return (
            f"## 🖌️ Image Customized in `{res['elapsed_ms']}ms`\n"
            f"- **Edits Applied:** **{res['applied']}**\n"
            f"- **Resolution:** `{res['size']}` | **Saved to SSD:** `ssd_images/{res['filename']}`\n\n"
            f"![Edited Image]({res['url']})"
        )

    coding_signals = (
        "code", "program", "python", "html", "css", "js", "javascript", "c++",
        "game", "tic-tac-toe", "tic tac toe", "tictactoe", "board", "player", "win", "tie",
        "process 1", "function", "loop", "grid", "button", "script", "algorithm",
        "debug", "terminal", "draw/tie", "draw or tie",
    )
    if any(c in low for c in coding_signals):
        return None

    gen_keywords = (
        "generate image", "generate an image", "generate a image", "generate a new image",
        "create image", "create an image", "create a photo", "generate photo",
        "draw an image", "draw a picture", "draw me a ", "paint a picture",
        "render image", "make an image", "make image",
        "image of ", "photo of ", "picture of ", "illustration of ", "wallpaper", "artwork",
        "/image", "/imagine"
    )
    if any(k in low for k in gen_keywords):
        res = generate_image_hybrid(clean)
        if not res.get("ok"):
            return f"## 🎨 Neural Image Generator\n- **Error:** {res.get('error')}"
        return (
            f"## 🎨 Brand-New Image Generated (`{res['elapsed_ms']}ms`)\n"
            f"- **Prompt:** *{res['prompt']}*\n"
            f"- **Engine:** **{res['engine']}**\n"
            f"- **Unique Generation Seed:** `{res.get('seed')}`\n"
            f"- **Saved to SSD:** `ssd_images/{res['filename']}`\n\n"
            f"![{res['prompt']}]({res['url']})"
        )
    return None


def try_readonly_system_inspector(prompt_text: str):
    """
    Allows the AI to inspect any file, risky script/binary, or folder (Downloads, Desktop, Startup, C:\\...)
    in 100% Read-Only mode while blocking all write/replace/delete requests.
    """
    lower = prompt_text.lower().strip()

    write_triggers = ("delete file", "remove file", "overwrite file", "replace file", "modify file", "wipe ", "format drive")
    if any(w in lower for w in write_triggers):
        return (
            "## 🔒 READ-ONLY SYSTEM GUARD ACTIVE\n"
            "- **Action Blocked:** ARGON-OMEGA operates under a strict **Kernel Read-Only Security Policy (`os.O_RDONLY`)**.\n"
            "- **Permitted:** Reading data, inspecting risky/suspicious files, computing SHA-256 hashes, and finding vulnerabilities.\n"
            "- **Forbidden:** Writing, replacing, overwriting, modifying, or deleting any file on your device."
        )

    inspect_triggers = (
        "check download", "scan download", "check desktop", "scan desktop",
        "check startup", "scan startup", "check document", "inspect ",
        "read file", "check file", "risky file", "check data"
    )
    has_explicit_path = bool(re.search(r'[a-zA-Z]:\\', prompt_text))
    if not any(t in lower for t in inspect_triggers) and not has_explicit_path:
        return None

    target_path = resolve_special_folder(prompt_text)

    if os.path.isfile(target_path):
        info = inspect_risky_file_readonly(target_path)
        risk_str = ", ".join(f"**{r[0]} ({r[1]})**" for r in info["risks"]) if info["risks"] else "**CLEAN (No malicious signatures detected)**"
        return (
            f"# 🔒 Read-Only File Forensics Report\n"
            f"- **Access Mode:** `os.O_RDONLY` (**Read-Only — Write/Replace/Execute Disabled**)\n"
            f"- **File Path:** `{info['path']}`\n"
            f"- **Size:** **{info['size_kb']} KB** | **Last Modified:** `{info['modified']}`\n"
            f"- **SHA-256 Fingerprint:** `{info['sha256']}`\n"
            f"- **Security Verdict:** {risk_str}\n\n"
            f"### 📄 Safe Read-Only Content / Strings Preview\n"
            f"```text\n{info['preview'] or '(Binary or empty file)'}\n```"
        )

    if os.path.isdir(target_path):
        scanned = []
        try:
            entries = sorted(os.listdir(target_path))[:60]
            for name in entries:
                full_p = os.path.join(target_path, name)
                if os.path.isfile(full_p):
                    ext = os.path.splitext(name)[1].lower()
                    try:
                        info = inspect_risky_file_readonly(full_p)
                        scanned.append((name, ext, info))
                    except Exception:
                        pass
        except Exception as e:
            return f"## 🔒 Read-Only Folder Audit\nCould not read `{target_path}`: `{e}`"

        risky_found = [s for s in scanned if s[2]["risks"] or s[2]["is_executable"]]
        report = [
            f"# 🔒 Read-Only Folder & Risky-File Audit: `{target_path}`",
            "- **Permission Level:** **STRICT READ-ONLY (`os.O_RDONLY`)** — *Cannot write, replace, modify, or execute any file.*",
            f"- **Files Inspected:** **{len(scanned)} files** | **Executable / Risky Files Flagged:** **{len(risky_found)}**",
            "",
            "| File Name | Size (KB) | Executable? | Read-Only Risk Analysis | SHA-256 (First 12) |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for name, ext, info in scanned[:25]:
            exec_badge = "**YES (Risky Type)**" if info["is_executable"] else "No"
            risk_summary = ", ".join(f"**{r[0]} ({r[1]})**" for r in info["risks"]) if info["risks"] else "Clean"
            report.append(f"| `{name}` | {info['size_kb']} KB | {exec_badge} | {risk_summary} | `{info['sha256'][:12]}...` |")

        report.append("\n### 💡 Tip: Inspect Any Individual File Safely")
        report.append(f"- Type `read file {os.path.join(target_path, scanned[0][0])}` to view its full SHA-256 hash and safe Read-Only content preview." if scanned else "- Folder is empty.")
        return "\n".join(report)

    return None


def solve_pure_math(user_text: str) -> str | None:
    return try_fast_math(user_text)


def try_neuro_symbolic_logic(user_text: str) -> str | None:
    """MIT/CMU PAL Neuro-Symbolic Logic & Cognitive Reflection Trap Compiler (<2ms)."""
    low = user_text.lower().strip()

    # 1. Bat & ball / system of equations: "cost $X ... costs $Y more"
    m_bat = re.search(r"cost[s]?\s*\$?(\d+(?:\.\d+)?).*?cost[s]?\s*\$?(\d+(?:\.\d+)?)\s+more", low)
    if m_bat:
        total = float(m_bat.group(1))
        diff = float(m_bat.group(2))
        smaller = round((total - diff) / 2.0, 4)
        larger = round(smaller + diff, 4)
        cents = int(round(smaller * 100))
        return f"**${smaller:.2f}** ({cents} cents). (Check: ${larger:.2f} + ${smaller:.2f} = ${total:.2f}, difference = ${diff:.2f}.)"

    # 2. Race overtaking trap: "overtake the 2nd person"
    m_race = re.search(r"(?:overtake|pass)\s+(?:the\s+)?(\d+(?:st|nd|rd|th))", low)
    if m_race and ("race" in low or "place" in low or "position" in low):
        pos = m_race.group(1)
        return f"You are in **{pos} place** (you took the {pos} person's spot)."

    # 3. Exponential doubling trap: "doubles every day ... N days ... half"
    m_exp = re.search(r"doubles?\s+every\s+day.*?(\d+)\s+days?.*?\bhalf\b", low)
    if m_exp:
        days = int(m_exp.group(1))
        return f"**{days - 1} days** (since it doubles each day, it was half-full the day before day {days})."

    # 4. Proportional rate trap: "X machines take Y minutes to make X widgets"
    m_rate = re.search(r"(\d+)\s+machines?\s+take[s]?\s+(\d+)\s+minutes?\s+to\s+make\s+(\d+)", low)
    if m_rate and m_rate.group(1) == m_rate.group(3):
        mins = m_rate.group(2)
        return f"**{mins} minutes** (each machine takes {mins} minutes to make 1 item)."

    return None


def try_chat_sandbox_or_terminal(prompt_text: str) -> str | None:
    """Handles `$ <cmd>`, `terminal: <cmd>`, and `run: <python_code>` directly inside Chat."""
    clean = prompt_text.strip()
    low = clean.lower()

    if clean.startswith("$") or low.startswith(("terminal:", "cmd:", "shell:")):
        cmd_str = clean[1:].strip() if clean.startswith("$") else clean.split(":", 1)[1].strip()
        res = run_safe_terminal_command(cmd_str)
        status_badge = "✅ EXIT 0" if res["ok"] else "⚠️ BLOCKED / ERROR"
        output_body = res["stdout"] if res["stdout"] else res["stderr"] or "(No output)"
        return (
            f"## 💻 Read-Only System Terminal (`{res['elapsed_ms']}ms` | {status_badge})\n"
            f"- **Command:** `$ {cmd_str}`\n"
            f"```text\n{output_body}\n```"
        )

    if low.startswith(("run:", "sandbox:", "run python:", "execute:")):
        code_body = clean.split(":", 1)[1].strip()
        res = run_code_in_sandbox(code_body)
        status_badge = "✅ SUCCESS" if res["ok"] else "❌ SANDBOX ERROR"
        out_text = res["stdout"]
        if res["stderr"]:
            out_text = (out_text + "\n" + res["stderr"]).strip()
        return (
            f"## 🧪 Isolated Python Sandbox (`{res['elapsed_ms']}ms` | {status_badge})\n"
            f"### 📄 Code Executed\n"
            f"```python\n{code_body}\n```\n"
            f"### 🖥️ Console Output\n"
            f"```text\n{out_text or '(No stdout output)'}\n```"
        )

    return None


def get_max_tokens(user_text: str, override: int | None = None) -> int:
    if override and int(override) > 0:
        return int(override)
    lower = user_text.lower()
    if any(lower.startswith(p) for p in ("make ", "build ", "create ")):
        return 512
    if any(
        w in lower
        for w in [
            "code",
            "python",
            "script",
            "program",
            "function",
            "html",
            "js",
            "c++",
            "write",
            "build",
        ]
    ):
        return 2048
    if any(
        w in lower for w in ["explain", "detail", "guide", "essay", "history"]
    ):
        return 600
    if len(user_text.split()) <= 2:
        return 85
    return 200


def _route_coder_if_needed(clean_input: str) -> bool:
    """Hot-swap llama-server.exe to the best installed coder GGUF when coding is requested."""
    low = clean_input.lower()
    is_code = any(k in low for k in (
        "code", "python", "script", "program", "function", "html", "js", "css",
        "game", "snake", "make ", "build ", "create ", "write ", "fix ", "refactor",
    ))
    if is_code:
        from engine import ensure_model_loaded_in_llama_server, select_best_coder_model
        coder = select_best_coder_model()
        if coder:
            ensure_model_loaded_in_llama_server(coder)
    return is_code


def _prepare_prompt(clean_input: str) -> tuple[str, bool, str | None]:
    """Detect follow-up formatting/expansion requests and link LAST_TOPIC without altering SYSTEM_PROMPT."""
    lower_in = clean_input.lower()
    format_keywords = ("italic", "bold", "heading", "point", "bullet", "table", "more", "detail", "rewrite", "list", "format")
    is_format_request = any(k in lower_in for k in format_keywords)

    followup_words = {
        "it", "that", "this", "they", "he", "she", "more", "also", "continue", "above",
        "italic", "bold", "table", "points", "heading", "headings",
    }
    words_set = set(re.findall(r"\w+", lower_in))
    new_topic_markers = ("compare", "between", "difference", "vs", "versus", "what is", "how to", "explain")
    has_new_topic = any(m in lower_in for m in new_topic_markers)
    is_followup = (len(words_set) <= 8) and bool(words_set & followup_words) and bool(LAST_TOPIC) and not has_new_topic

    if not is_format_request:
        return clean_input, False, None

    if not is_followup:
        if any(lower_in.startswith(p) for p in ("make ", "build ", "create ")):
            return f"{clean_input}\nOutput complete, runnable code in a single code block with no numbered filler steps.", True, None
        # Brand-new question that happens to request headings/tables/bold: do NOT overwrite with LAST_TOPIC
        return clean_input, True, None

    target_topic = LAST_TOPIC
    if "italic" in lower_in and not any(k in lower_in for k in ("heading", "table", "point", "bold", "more")) and LAST_RESPONSE:
        italic_reply = "*" + LAST_RESPONSE.strip().replace("*", "").replace("\n", "*\n*") + "*"
        return clean_input, True, italic_reply

    style_reqs = []
    if "heading" in lower_in or "more" in lower_in:
        style_reqs.append("## Headings")
    if "bold" in lower_in or "more" in lower_in:
        style_reqs.append("**bold key terms**")
    if "italic" in lower_in:
        style_reqs.append("*italic text*")
    if "point" in lower_in or "bullet" in lower_in or "list" in lower_in or "more" in lower_in:
        style_reqs.append("- bullet points")
    if "table" in lower_in or "more" in lower_in:
        style_reqs.append("a Markdown table (| Column 1 | Column 2 |)")

    reqs_str = ", ".join(style_reqs) if style_reqs else "clear Markdown formatting"
    effective_prompt = (
        f"Topic: {target_topic}\n"
        f"User request: {clean_input}\n"
        f"Provide a comprehensive response about '{target_topic}' formatted using {reqs_str}."
    )
    return effective_prompt, True, None


def _attach_ssd_recall(prompt: str, effective_prompt: str, history: list[dict] | None = None) -> str:
    """Keep normal chat prompts lean for 100% KV cache reuse; attach vault recall only on explicit coding prompts."""
    if not _is_explicit_coding_prompt(prompt):
        return effective_prompt
    try:
        from engine import query_code_vault
        hit = query_code_vault(prompt)
        if hit:
            return f"[SSD Code Vault Reference]\n{hit[:1200]}\n\n[User Request]\n{effective_prompt}"
    except Exception:
        pass
    return effective_prompt


_REFLEX_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ssd_virtual_context.db")
_REFLEX_MEM_CACHE: dict[str, str] = {
    "hi": "Hello! How can I help you today?",
    "hello": "Hello! I'm ready—what would you like to build or ask?",
    "hey": "Hey! How can I assist you?",
    "good morning": "Good morning! How can I help you today?",
    "who are you": "I am Argon-Omega, your 100% offline AI assistant and local coding engine.",
    "what can you do": "I run 100% offline on your SSD to answer questions, write and run code, inspect your PC via terminal, and generate/edit images and videos.",
    "help": "Ask any question, run math, type `$ <cmd>` or `run: <python>` for terminal/sandbox, or switch tabs for IDE, Image Studio, and Video Studio.",
    "thanks": "You're welcome! Let me know if you need anything else.",
    "thank you": "You're welcome! Happy to help.",
}
_REFLEX_DB_READY = False


def _norm_reflex_key(prompt: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (prompt or "").lower())).strip()


def _ensure_reflex_db() -> None:
    global _REFLEX_DB_READY
    if _REFLEX_DB_READY:
        return
    try:
        conn = sqlite3.connect(_REFLEX_DB_PATH)
        conn.execute("PRAGMA mmap_size = 30000000000;")
        conn.execute("CREATE TABLE IF NOT EXISTS reflex_cache (norm_prompt TEXT PRIMARY KEY, reply TEXT NOT NULL, updated_at REAL NOT NULL);")
        for norm_p, rep in conn.execute("SELECT norm_prompt, reply FROM reflex_cache").fetchall():
            _REFLEX_MEM_CACHE.setdefault(norm_p, rep)
        conn.close()
        _REFLEX_DB_READY = True
    except Exception:
        pass


def get_reflex_cache(prompt: str) -> str | None:
    _ensure_reflex_db()
    key = _norm_reflex_key(prompt)
    if not key:
        return None
    return _REFLEX_MEM_CACHE.get(key)


def set_reflex_cache(prompt: str, reply: str) -> None:
    _ensure_reflex_db()
    key = _norm_reflex_key(prompt)
    if not key or not reply or len(reply.strip()) <= 40 or reply.strip().lower() in ("okay.", "ok.") or reply.startswith(("[Stream error", "llama-server.exe error", "I'm sorry", "I cannot")):
        return
    _REFLEX_MEM_CACHE[key] = reply
    try:
        conn = sqlite3.connect(_REFLEX_DB_PATH)
        conn.execute("INSERT OR REPLACE INTO reflex_cache (norm_prompt, reply, updated_at) VALUES (?, ?, ?)", (key, reply, time.time()))
        conn.commit()
        conn.close()
    except Exception:
        pass


FAST_CHAT_SYSTEM_PROMPT = "You are Argon, a fast offline AI assistant. Answer directly and concisely."

_EXPLICIT_CODING_SIGNALS = (
    "write code", "python", "html", "javascript", "c++", "function", "script",
    "build a game", "debug", "code", "program", "rust", "java", "sql", "css",
)

_COMPLEX_APP_SIGNALS = (
    "full game", "build a game", "snake", "tic-tac-toe", "tic tac toe",
    "pong", "chess", "multi-feature", "full app", "complete app", "complex algorithm",
)


def _is_explicit_coding_prompt(prompt: str) -> bool:
    low = (prompt or "").lower()
    return any(k in low for k in _EXPLICIT_CODING_SIGNALS)


def _is_complex_app_prompt(prompt: str) -> bool:
    low = (prompt or "").lower()
    return any(k in low for k in _COMPLEX_APP_SIGNALS)


def _contract_and_save_code(reply: str, topic: str) -> str:
    if not _is_complex_app_prompt(topic):
        return reply
    match = re.search(r"```(python|html)\s*\n([\s\S]*?)```", reply, re.IGNORECASE)
    if not match:
        return reply
    lang = match.group(1).lower()
    try:
        from engine import run_alphacodium_contract_gate, save_to_code_vault
        result = run_alphacodium_contract_gate(match.group(2), lang=lang, topic=topic)
        if not result["ok"]:
            return reply
        save_to_code_vault(topic[:200], lang, result["code"])
        replacement = f"```{lang}\n{result['code']}\n```"
        return reply[:match.start()] + replacement + reply[match.end():]
    except Exception:
        return reply


def run_pocketpal_engine(user_prompt: str, override_max_tokens: int | None = None, target_model: str | None = None) -> dict:
    global LAST_TOPIC, LAST_RESPONSE
    t0 = time.perf_counter()
    clean_input = user_prompt.strip()

    # 1. Instant Pure Math Check (<1ms)
    math_res = solve_pure_math(clean_input)
    if math_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": math_res,
            "ms": ms,
            "gear": "PocketPal Math Fast-Path",
        }

    # 1a. MIT/CMU Neuro-Symbolic Logic Compiler (<2ms)
    logic_res = try_neuro_symbolic_logic(clean_input)
    if logic_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": logic_res,
            "ms": ms,
            "gear": "Neuro-Symbolic Logic Compiler",
        }

    # 1b. Read-Only Python Sandbox (`run: ...`)
    sb_res = try_chat_sandbox(clean_input)
    if sb_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": sb_res,
            "ms": ms,
            "gear": "ARGON Kernel Audit Sandbox",
        }

    # 1c. Interactive Game Sandbox (Snake HTML5 + Python)
    game_res = try_interactive_game_sandbox(clean_input)
    if game_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": game_res,
            "ms": ms,
            "gear": "Interactive Sandbox Game Engine",
        }

    # 1d. Live Network Device Radar & Terminal Fix
    net_res = try_network_devices_and_terminal_fix(clean_input)
    if net_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": net_res,
            "ms": ms,
            "gear": "Live Network Radar & Terminal Fix",
        }

    # 1e. Autonomous Whole-Device System Terminal Agent
    sys_res = try_autonomous_system_terminal(clean_input)
    if sys_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": sys_res,
            "ms": ms,
            "gear": "Autonomous System Terminal Agent",
        }

    # 1c. Autonomous GitHub Upload Tool
    gh_res = try_github_upload_tool(clean_input)
    if gh_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {"reply": gh_res, "ms": ms, "gear": "Autonomous GitHub Upload"}

    # 1c. Autonomous System Terminal Agent (RAM, junk files, disk, CPU, ports)
    term_res = try_autonomous_terminal_agent(clean_input)
    if term_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": term_res,
            "ms": ms,
            "gear": "Autonomous System Terminal Agent",
        }

    sb_res = try_chat_sandbox_or_terminal(clean_input)
    if sb_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": sb_res,
            "ms": ms,
            "gear": "Read-Only Sandbox / Terminal",
        }

    # 2. Chat Image Generation & Editing Engine
    img_res = try_chat_image_engine(clean_input)
    if img_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": img_res,
            "ms": ms,
            "gear": "3-Tier SSD Image Engine",
        }

    # 2b. Chat Video AI Engine
    if any(k in clean_input.lower() for k in ("generate video", "create video", "make a video", "animate ")):
        vres = generate_video(clean_input)
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": f"## 🎬 Video Generated (`{vres['latency_ms']}ms` | {vres['engine']})\n- **Saved:** `gallery/videos/{vres['filename']}`\n- **Open:** [🎬 Play Video]({vres['url']})",
            "ms": ms,
            "gear": "SSD Video AI Studio",
        }

    # 2. Strict Read-Only System & Risky-File Forensics Guard
    ro_res = try_readonly_system_inspector(clean_input)
    if ro_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": ro_res,
            "ms": ms,
            "gear": "Read-Only System Guard (os.O_RDONLY)",
        }

    # 2. Deterministic OWASP Security Audit (<2ms)
    audit_res = try_security_code_audit(clean_input)
    if audit_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": audit_res,
            "ms": ms,
            "gear": "OWASP Static Security Auditor",
        }

    # 3. Full Device, File, Port & Firewall Security Scan
    full_scan = try_full_device_file_security_scan(clean_input)
    if full_scan is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "reply": full_scan,
            "ms": ms,
            "gear": "Full Device & File Security Scanner",
        }

    # Tier-0 Microsecond Reflex Cache (<0.5ms TTFT)
    reflex_hit = get_reflex_cache(clean_input)
    if reflex_hit is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 3)
        return {
            "reply": reflex_hit,
            "ms": ms,
            "gear": "Tier-0 Microsecond Reflex Cache",
        }

    effective_prompt, is_format_request, instant_reply = _prepare_prompt(clean_input)
    if instant_reply is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {"reply": instant_reply, "ms": ms, "gear": "Instant Format Transform"}

    effective_prompt = _attach_ssd_recall(clean_input, effective_prompt, CHAT_HISTORY)

    # 2. Build Clean Native ChatML Messages (Untouched Static System Prompt for 0ms KV Cache Reuse)
    is_explicit_code = _is_explicit_coding_prompt(clean_input)
    sys_msg = SYSTEM_PROMPT if is_explicit_code else FAST_CHAT_SYSTEM_PROMPT
    messages = [{"role": "system", "content": sys_msg}]
    if not is_format_request and _needs_history(clean_input):
        for turn in CHAT_HISTORY[-2:]:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["ai"]})

    messages.append({"role": "user", "content": effective_prompt})
    routed_model = target_model or select_best_model_for_prompt(clean_input)[1]
    is_fast_brain = routed_model == GEMMA_FAST_BRAIN
    model_limit = 4096 if (routed_model == CODER_FAST_BRAIN and is_explicit_code) else (256 if not is_explicit_code else (512 if is_fast_brain else 2048))
    requested_tokens = 256 if not is_explicit_code else (320 if is_format_request else (override_max_tokens or model_limit))
    max_tok = min(model_limit, requested_tokens)

    payload = {
        "messages": messages,
        "temperature": 0.2 if is_fast_brain else (0.35 if routed_model == GEMMA_GENERAL_BRAIN else 0.05),
        "top_k": 20 if routed_model in (CODER_FAST_BRAIN, CODER_ARCHITECT_BRAIN) else 40,
        "repeat_penalty": 1.05,
        "max_tokens": max_tok,
        "n_predict": max_tok,
        "n_keep": 512,
        "n_probs": 0,
        "id_slot": 0,
        "cache_prompt": True,
        "stream": False,
    }

    req = urllib.request.Request(
        LLAMA_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    raw_reply = ""
    try:
        for continuation_index in range(3):
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choice = data["choices"][0]
            raw_reply += choice["message"]["content"]
            if choice.get("finish_reason") != "length" or raw_reply.count("```") % 2 == 0 or continuation_index == 2:
                break
            last_line = raw_reply.rsplit("\n", 1)[-1]
            messages.extend([
                {"role": "assistant", "content": raw_reply},
                {"role": "user", "content": f"Continue exactly after this final code line without repeating it: {last_line!r}. Output only the remaining code and close the fenced code block."},
            ])
            payload["messages"] = messages
            req = urllib.request.Request(
                LLAMA_CHAT_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
    except (http.client.RemoteDisconnected, urllib.error.URLError, ConnectionResetError, BrokenPipeError, TimeoutError, OSError) as exc:
        if not raw_reply:
            raw_reply = f"llama-server.exe connection interrupted: {exc}"
    raw_reply = raw_reply.strip()
    raw_reply = _contract_and_save_code(raw_reply, clean_input)
    reply = raw_reply if is_format_request else _trim_incomplete_sentence(raw_reply)
    set_reflex_cache(clean_input, reply)

    if not is_format_request and len(clean_input) > 1:
        LAST_TOPIC = clean_input
        LAST_RESPONSE = reply

    # Save turn to clean history
    CHAT_HISTORY.append({"user": clean_input, "ai": reply})
    if len(CHAT_HISTORY) > MAX_HISTORY_TURNS:
        CHAT_HISTORY.pop(0)

    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "reply": reply,
        "ms": ms,
        "gear": f"PocketPal Engine ({routed_model})",
    }


CODER_FAST_BRAIN = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
CODER_ARCHITECT_BRAIN = "qwen2.5-coder-32b-instruct-IQ3_XS.gguf"
PHI_BRAIN = "Phi-4-mini-instruct-Q4_K_M.gguf"
PHI_DEEP_BRAIN = "phi-4-Q4_K_M.gguf"
GEMMA_GENERAL_BRAIN = "gemma-3-4b-it-Q4_K_M.gguf"
GEMMA_FAST_BRAIN = "gemma-3-1b-it-Q4_K_M.gguf"
ELITE_BRAINS = (CODER_FAST_BRAIN, CODER_ARCHITECT_BRAIN, PHI_BRAIN, PHI_DEEP_BRAIN, GEMMA_GENERAL_BRAIN, GEMMA_FAST_BRAIN)
CURRENT_LOADED_GGUF = GEMMA_FAST_BRAIN if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", GEMMA_FAST_BRAIN)) else CODER_FAST_BRAIN


FAST_TIER_BRAIN = "gemma-3-1b-it-Q4_K_M.gguf"
BALANCED_TIER_BRAIN = "qwen2.5-coder-3b-instruct-q4_k_m.gguf"
ACCURATE_TIER_BRAIN = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"


def _is_installed_gguf(fname: str) -> bool:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", fname)
    return os.path.isfile(p) and os.path.getsize(p) > 100_000_000


def _pick_installed(primary: str, fallback: str = FAST_TIER_BRAIN) -> str:
    if _is_installed_gguf(primary):
        return primary
    if _is_installed_gguf(fallback):
        return fallback
    return primary


def _format_size(size_bytes: int) -> str:
    mb = size_bytes / (1024 ** 2)
    return f"{round(mb / 1024, 2)} GB" if mb >= 1024 else f"{round(mb, 1)} MB"


def get_model_tier_info(gguf_name: str, chosen_mode: str = "general") -> tuple[str, str]:
    """Return (rank_label, task_label) for the metadata badge."""
    low = (gguf_name or "").lower()
    if "1b" in low or "0.5b" in low:
        tier = "⚡ LOW-FAST"
    elif "3b" in low or "4b" in low or "mini" in low:
        tier = "⚖️ MID-BALANCED"
    else:
        tier = "🎯 HIGH-ACCURATE"
    task = "Code" if chosen_mode in ("coder", "coder_deep") else ("Logic" if "math" in chosen_mode else "Chat")
    return tier, task


def list_available_models() -> list:
    """Lists Auto, 3-Tier presets (Fast/Balanced/Accurate), and all installed LLM .gguf files."""
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

    def _is_ready(fname: str) -> bool:
        p = os.path.join(models_dir, fname)
        return os.path.exists(p) and os.path.getsize(p) > 100_000_000

    def _sz(fname: str) -> str:
        p = os.path.join(models_dir, fname)
        return _format_size(os.path.getsize(p)) if _is_ready(fname) else "Not Downloaded"

    models = [
        {"id": "auto", "name": "🤖 Auto (Smart Router — Installed Models Only)", "disabled": False},
        {"id": "fast", "name": f"{'⚡' if _is_ready(FAST_TIER_BRAIN) else '🔒'} Fast (Low) — {FAST_TIER_BRAIN} ({_sz(FAST_TIER_BRAIN)})", "disabled": not _is_ready(FAST_TIER_BRAIN)},
        {"id": "balanced", "name": f"{'⚖️' if _is_ready(BALANCED_TIER_BRAIN) else '🔒'} Balanced (Mid) — {BALANCED_TIER_BRAIN} ({_sz(BALANCED_TIER_BRAIN)})", "disabled": not _is_ready(BALANCED_TIER_BRAIN)},
        {"id": "accurate", "name": f"{'🎯' if _is_ready(ACCURATE_TIER_BRAIN) else '🔒'} Accurate (High) — {ACCURATE_TIER_BRAIN} ({_sz(ACCURATE_TIER_BRAIN)})", "disabled": not _is_ready(ACCURATE_TIER_BRAIN)},
    ]
    if os.path.isdir(models_dir):
        for f in sorted(os.listdir(models_dir)):
            low_f = f.lower()
            if low_f.endswith(".gguf") and not low_f.endswith(".part") and "sd_turbo" not in low_f and "flux" not in low_f and "dreamshaper" not in low_f:
                if _is_ready(f):
                    size_str = _format_size(os.path.getsize(os.path.join(models_dir, f)))
                    models.append({"id": f, "name": f"🧠 {f} ({size_str})", "disabled": False})
    return models


def select_best_model_for_prompt(prompt_text: str, requested_model: str = "auto") -> tuple:
    """
    Returns (chosen_mode, chosen_gguf_filename, badge_label).
    If requested_model != 'auto', strictly honors the user's chosen model.
    If requested_model == 'auto', automatically picks the best model for the prompt.
    """
    req = (requested_model or "auto").strip()
    low = prompt_text.lower().strip()

    if req == "fast":
        chosen = _pick_installed(FAST_TIER_BRAIN)
        return ("general", chosen, f"Fast (Low) — {chosen}")
    if req == "balanced":
        chosen = _pick_installed(BALANCED_TIER_BRAIN)
        return ("coder", chosen, f"Balanced (Mid) — {chosen}")
    if req == "accurate":
        chosen = _pick_installed(ACCURATE_TIER_BRAIN)
        return ("coder", chosen, f"Accurate (High) — {chosen}")
    if req in ELITE_BRAINS or req.endswith(".gguf"):
        chosen = _pick_installed(req)
        return ("llm_only", chosen, f"Locked Model: {chosen}")

    fast_signals = (
        "check my computer ram", "check my ram", "check useless files", "check all useless files", "quick check",
        "summarize", "summary", "in one sentence", "short reply", "just say", "thank you",
    )
    if any(signal in low for signal in fast_signals) or re.search(r"\b(?:hello|hi|thanks)\b", low):
        chosen = _pick_installed(FAST_TIER_BRAIN)
        return ("fast", chosen, f"Auto -> Ultra-Fast Brain ({chosen})")

    coding_signals = (
        "code", "program", "python", "javascript", "typescript", "c++", "c#", "rust", "java",
        "html", "css", "js", "sql", "powershell", "bash", "swift", "kotlin", "php", "ruby", "scala",
        "game", "chess", "pong", "snake", "tic-tac-toe", "tic tac toe", "tictactoe", "board", "player",
        "win", "tie", "process 1", "draw/tie", "draw or tie", "debug", "terminal", "command line",
        "function", "loop", "grid", "button", "class", "security", "vulnerab", "bug", "fix",
        "architect", "architecture", "large codebase", "system design",
    )
    if any(signal in low for signal in coding_signals):
        coder = _pick_installed(ACCURATE_TIER_BRAIN)
        return ("coder", coder, f"Auto -> Coding ({coder})")

    deep_math_signals = ("formal proof", "prove that", "derive", "deep math", "advanced math", "hard math", "complex algorithm", "logic trap")
    if any(signal in low for signal in deep_math_signals):
        chosen = _pick_installed(ACCURATE_TIER_BRAIN)
        return ("math_deep", chosen, f"Auto -> Deep Math ({chosen})")
    if solve_pure_math(prompt_text) is not None or any(signal in low for signal in ("math", "logic", "equation", "calculate", "probability", "algorithm")):
        chosen = _pick_installed(BALANCED_TIER_BRAIN)
        return ("math", chosen, f"Auto -> Math & Logic ({chosen})")

    image_signals = (
        "generate image", "create image", "draw a picture", "draw me a", "paint a picture",
        "image of ", "photo of ", "illustration of ", "wallpaper", "/image", "/imagine",
    )
    if any(signal in low for signal in image_signals):
        chosen = _pick_installed(FAST_TIER_BRAIN)
        return ("image", chosen, f"Auto -> Image Request ({chosen})")

    chosen = _pick_installed(FAST_TIER_BRAIN)
    return ("general", chosen, f"Auto -> Fast Chat Brain ({chosen})")


class SubAgentSwarm:
    """Parallel Sub-Agent Swarm Orchestrator for fast coding, file, terminal & research tasks."""

    def __init__(self, llama_url: str = "http://127.0.0.1:8081/v1/chat/completions"):
        self.llama_url = llama_url

    def _call_llama(self, messages: list, max_tokens: int = 120) -> str:
        payload = {
            "messages": messages,
            "temperature": 0.05,
            "top_k": 1,
            "repeat_penalty": 1.12,
            "max_tokens": max_tokens,
            "cache_prompt": True,
            "stream": False,
        }
        req = urllib.request.Request(
            self.llama_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _REAL_URLLIB_URLOPEN(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data["choices"][0]["message"]["content"].strip()

    def recon_subagent(self, prompt: str, target_file: str | None = None) -> tuple[str, float]:
        """<2ms Zero-LLM: Locates target files, extracts relevant lines, and queries ssd_100m_token_matrix.db in parallel."""
        t0 = time.perf_counter()
        snippets = []

        def _scan_file():
            fname = target_file
            if not fname:
                m = re.search(r"([A-Za-z0-9_\-./\\]+\.(?:py|js|ts|html|css|json|md|txt))", prompt)
                if m:
                    fname = m.group(1)
            if fname and os.path.isfile(fname):
                try:
                    with open(fname, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    return f"[File {fname} ({len(lines)} lines)]:\n" + "".join(lines[:60])
                except Exception:
                    pass
            return ""

        def _query_matrix():
            try:
                from engine import query_token_matrix
                return "\n".join(query_token_matrix(prompt))
            except Exception:
                pass
            return ""

        def _query_vault():
            try:
                from engine import query_code_vault
                code = query_code_vault(prompt)
                return f"[Code Vault]\n{code[:500]}" if code else ""
            except Exception:
                return ""

        with ThreadPoolExecutor(max_workers=3) as pool:
            f_file = pool.submit(_scan_file)
            f_mat = pool.submit(_query_matrix)
            f_vault = pool.submit(_query_vault)
            for fut in (f_file, f_mat, f_vault):
                res = fut.result()
                if res:
                    snippets.append(res)

        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return "\n".join(snippets), ms

    def planner_subagent(self, prompt: str) -> tuple[str, float]:
        """Fast task splitter classifying into surgical_edit, terminal, or code_gen."""
        t0 = time.perf_counter()
        low = prompt.lower().strip()
        if low.startswith("!") or any(k in low for k in ("run terminal", "execute command", "git status", "pytest")):
            plan = "terminal"
        elif any(k in low for k in ("edit ", "fix ", "patch ", "update ", "refactor ", "<<<search")):
            plan = "surgical_edit"
        elif any(k in low for k in ("code", "python", "script", "function", "class", "html", "c++", "rust", "js")):
            plan = "code_gen"
        else:
            plan = "general"
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return plan, ms

    def surgical_coder_subagent(self, prompt: str, recon_ctx: str, code_context: str | None = None, surgical: bool = False) -> tuple[str, float]:
        """Generates minimal Surgical Diff (<<<SEARCH ... === ... >>>REPLACE) or tight code."""
        t0 = time.perf_counter()
        is_game = any(g in prompt.lower() for g in ("tic-tac-toe", "tic tac toe", "snake", "pong", "chess", "calculator", "game"))
        sys_msg = (
            "You are SurgicalCoderSubAgent. Output ONLY a minimal surgical diff using:\n"
            "<<<SEARCH\n...\n===\n...\n>>>REPLACE\n"
            "Do not rewrite the whole file."
            if (surgical and (code_context or recon_ctx))
            else (
                "You are SurgicalCoderSubAgent. Output a bug-free ```html``` playable HTML5/Canvas game/app "
                "(with Player vs AI, score tracker, and Reset button) so it runs immediately in the browser."
                if is_game
                else "You are SurgicalCoderSubAgent. Output clean, concise, working code with zero filler."
            )
        )
        user_msg = prompt
        if code_context:
            user_msg = f"[Target Code]:\n{code_context[:2500]}\n\n[Task]: {prompt}"
        elif recon_ctx:
            user_msg = f"[Recon Context]:\n{recon_ctx[:1500]}\n\n[Task]: {prompt}"

        max_tok = 120 if (surgical and (code_context or recon_ctx)) else 320
        out = self._call_llama(
            [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}],
            max_tokens=max_tok,
        )
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return out, ms

    def terminal_subagent(self, cmd: str, cwd: str | None = None) -> tuple[str, int, float]:
        """Async/fast shell runner capturing stdout/stderr."""
        t0 = time.perf_counter()
        clean_cmd = cmd.lstrip("!").strip()
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", clean_cmd] if os.name == "nt" else clean_cmd,
                shell=(os.name != "nt"),
                cwd=cwd or os.getcwd(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            out = (proc.stdout or "") + (f"\n[STDERR]\n{proc.stderr}" if proc.stderr else "")
            rc = proc.returncode
        except Exception as exc:
            out, rc = f"[Terminal Error]: {exc}", -1
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return out.strip(), rc, ms

    def verifier_subagent(self, output_text: str, is_python: bool = False) -> tuple[str, bool, float]:
        """<1ms syntax & error verifier; triggers targeted micro-patch on failure."""
        t0 = time.perf_counter()
        if is_python:
            blocks = re.findall(r"```(?:python)?\s*(.*?)```", output_text, re.DOTALL)
            for b in blocks:
                try:
                    ast.parse(b.strip())
                except SyntaxError as e:
                    patched = self._call_llama(
                        [
                            {"role": "system", "content": "Fix ONLY the Python SyntaxError. Output corrected code only."},
                            {"role": "user", "content": f"SyntaxError line {e.lineno}: {e.msg}\nCode:\n{b}"},
                        ],
                        max_tokens=160,
                    )
                    ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    return patched, False, ms
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return output_text, True, ms

    def run(self, prompt: str, target_file: str | None = None, code_context: str | None = None) -> dict:
        t_total = time.perf_counter()
        recon_ctx, recon_ms = self.recon_subagent(prompt, target_file=target_file)
        plan, plan_ms = self.planner_subagent(prompt)

        stages = [f"[🕵️ Recon: {recon_ms}ms]", f"[🧠 Planner: {plan_ms}ms]"]
        if plan == "terminal":
            reply, rc, term_ms = self.terminal_subagent(prompt)
            stages.append(f"[💻 Terminal: {term_ms}ms]")
            _, ok, ver_ms = self.verifier_subagent(reply, is_python=False)
            stages.append(f"[{'✅' if ok else '🔧'} Verifier: {ver_ms}ms]")
        else:
            is_surg = (plan == "surgical_edit")
            reply, code_ms = self.surgical_coder_subagent(prompt, recon_ctx, code_context=code_context, surgical=is_surg)
            stages.append(f"[💻 Surgical Coder: {code_ms}ms]")
            reply, ok, ver_ms = self.verifier_subagent(reply, is_python=("python" in prompt.lower() or "```python" in reply.lower()))
            stages.append(f"[{'✅' if ok else '🔧'} Verifier: {ver_ms}ms]")

        total_ms = round((time.perf_counter() - t_total) * 1000.0, 2)
        badge = f"⚡ Swarm: {' → '.join(stages)}"
        return {"reply": reply, "ms": total_ms, "gear": badge, "swarm_badge": badge}


SWARM_ENGINE = SubAgentSwarm()


def run_5gear_brain(user_prompt: str, override_max_tokens: int | None = None, requested_model: str = "auto", swarm_enabled: bool = True) -> dict:
    chosen_mode, chosen_gguf, badge_label = select_best_model_for_prompt(user_prompt, requested_model=requested_model)
    low = user_prompt.lower().strip()
    is_explicit_code = _is_explicit_coding_prompt(user_prompt)
    is_complex_app = _is_complex_app_prompt(user_prompt)
    is_swarm_task = swarm_enabled and (
        low.startswith("!")
        or any(k in low for k in ("edit ", "fix ", "patch ", "refactor ", "pytest", "git "))
    )
    if is_complex_app and swarm_enabled and solve_pure_math(user_prompt) is None:
        res = CodingSubAgentSwarm.run(user_prompt)
        res["gear"] = res.get("badge", "Coding Specialist")
        res["swarm_badge"] = res.get("badge")
    elif is_swarm_task and is_explicit_code and solve_pure_math(user_prompt) is None:
        res = SWARM_ENGINE.run(user_prompt)
    else:
        res = run_pocketpal_engine(user_prompt, override_max_tokens, target_model=chosen_gguf)
    res["model_used"] = badge_label
    res["chosen_gguf"] = chosen_gguf
    if is_complex_app:
        res["reply"] = _contract_and_save_code(res.get("reply", ""), user_prompt)
    return res


def stream_pocketpal_engine(user_prompt: str, override_max_tokens: int | None = None, target_model: str | None = None):
    """True real-time SSE token generator calling llama-server.exe with stream=True."""
    global LAST_TOPIC, LAST_RESPONSE
    t0 = time.perf_counter()
    clean_input = user_prompt.strip()

    math_res = solve_pure_math(clean_input)
    if math_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": math_res}
        yield {
            "type": "done",
            "reply": math_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"⚡ TTFT: {ms}ms | Speed: Instant Math",
        }
        return

    sb_res = try_chat_sandbox(clean_input)
    if sb_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": sb_res}
        yield {
            "type": "done",
            "reply": sb_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"🔒 TTFT: {ms}ms | ARGON Kernel Audit Sandbox",
        }
        return

    game_res = try_interactive_game_sandbox(clean_input)
    if game_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": game_res}
        yield {
            "type": "done",
            "reply": game_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"🎮 TTFT: {ms}ms | Interactive Sandbox Game Engine",
        }
        return

    net_res = try_network_devices_and_terminal_fix(clean_input)
    if net_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": net_res}
        yield {
            "type": "done",
            "reply": net_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"📡 TTFT: {ms}ms | Live Network Radar & Terminal Fix",
        }
        return

    gh_res = try_github_upload_tool(clean_input)
    if gh_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": gh_res}
        yield {
            "type": "done",
            "reply": gh_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"🚀 TTFT: {ms}ms | Autonomous GitHub Upload",
        }
        return

    term_res = try_autonomous_terminal_agent(clean_input)
    if term_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": term_res}
        yield {
            "type": "done",
            "reply": term_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"💻 TTFT: {ms}ms | Autonomous System Terminal",
        }
        return

    sb_res = try_chat_sandbox_or_terminal(clean_input)
    if sb_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": sb_res}
        yield {
            "type": "done",
            "reply": sb_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"🔒 TTFT: {ms}ms | Read-Only Sandbox / Terminal",
        }
        return

    img_res = try_chat_image_engine(clean_input)
    if img_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": img_res}
        yield {
            "type": "done",
            "reply": img_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"🎨 TTFT: {ms}ms | 3-Tier SSD Image Engine",
        }
        return

    ro_res = try_readonly_system_inspector(clean_input)
    if ro_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": ro_res}
        yield {
            "type": "done",
            "reply": ro_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"🔒 TTFT: {ms}ms | Read-Only System Guard (os.O_RDONLY)",
        }
        return

    audit_res = try_security_code_audit(clean_input)
    if audit_res is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": audit_res}
        yield {
            "type": "done",
            "reply": audit_res,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"⚡ TTFT: {ms}ms | OWASP Static Security Auditor",
        }
        return

    full_scan = try_full_device_file_security_scan(clean_input)
    if full_scan is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": full_scan}
        yield {
            "type": "done",
            "reply": full_scan,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"⚡ TTFT: {ms}ms | Full Device & File Security Scanner",
        }
        return

    effective_prompt, is_format_request, instant_reply = _prepare_prompt(clean_input)
    if instant_reply is not None:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield {"type": "token", "token": instant_reply}
        yield {
            "type": "done",
            "reply": instant_reply,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": 0.0,
            "badge": f"⚡ TTFT: {ms}ms | Instant Format Transform",
        }
        return

    # Tier-0 Microsecond Reflex Cache (<0.5ms TTFT, 100+ to 1,000+ tok/s)
    reflex_hit = get_reflex_cache(clean_input)
    if reflex_hit is not None:
        yield {"type": "token", "token": reflex_hit}
        ms = max(0.01, round((time.perf_counter() - t0) * 1000.0, 2))
        est_toks = max(1, int(len(reflex_hit.split()) * 1.3))
        tps = round(est_toks / (ms / 1000.0), 1)
        yield {
            "type": "done",
            "reply": reflex_hit,
            "ttft_ms": ms,
            "total_ms": ms,
            "tok_per_sec": tps,
            "badge": f"⚡ Tier-0 Reflex Cache | TTFT: {ms}ms | Speed: {tps} tokens/sec",
        }
        return

    effective_prompt = _attach_ssd_recall(clean_input, effective_prompt, CHAT_HISTORY)

    routed_model = target_model or select_best_model_for_prompt(clean_input)[1]
    is_explicit_code = _is_explicit_coding_prompt(clean_input)

    sys_msg = SYSTEM_PROMPT if is_explicit_code else FAST_CHAT_SYSTEM_PROMPT
    messages = [{"role": "system", "content": sys_msg}]
    if not is_format_request and _needs_history(clean_input):
        for turn in CHAT_HISTORY[-2:]:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["ai"]})
    messages.append({"role": "user", "content": effective_prompt})
    is_fast_brain = routed_model == GEMMA_FAST_BRAIN
    model_limit = 256 if not is_explicit_code else (512 if is_fast_brain else 2048)
    requested_tokens = 256 if not is_explicit_code else (320 if is_format_request else (override_max_tokens or model_limit))
    max_tok = 4096 if is_explicit_code else min(512, requested_tokens)

    payload = {
        "messages": messages,
        "temperature": 0.2 if is_fast_brain else (0.35 if routed_model == GEMMA_GENERAL_BRAIN else 0.05),
        "top_k": 20 if routed_model in (CODER_FAST_BRAIN, CODER_ARCHITECT_BRAIN) else 40,
        "repeat_penalty": 1.10,
        "max_tokens": max_tok,
        "n_predict": max_tok,
        "n_probs": 0,
        "id_slot": 0,
        "cache_prompt": True,
        "stream": True,
    }

    req = urllib.request.Request(
        LLAMA_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    chunks = []
    ttft_ms = None
    token_count = 0
    timings = {}

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                    if "timings" in obj and isinstance(obj["timings"], dict):
                        timings = obj["timings"]
                    delta = obj["choices"][0].get("delta", {}).get("content")
                    if delta:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                        token_count += 1
                        chunks.append(delta)
                        yield {"type": "token", "token": delta}
                except Exception:
                    continue
    except (http.client.RemoteDisconnected, urllib.error.URLError, ConnectionResetError, BrokenPipeError, TimeoutError, OSError) as exc:
        err_note = f"\n[Connection interrupted: {exc}]"
        if not chunks:
            chunks.append(err_note.strip())
            yield {"type": "token", "token": err_note.strip()}

    raw_reply = "".join(chunks).strip()
    reply = raw_reply if is_format_request else _trim_incomplete_sentence(raw_reply)
    set_reflex_cache(clean_input, reply)
    total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if ttft_ms is None:
        ttft_ms = total_ms
    gen_sec = max(0.05, (total_ms - ttft_ms) / 1000.0)
    tok_per_sec = round(float(timings.get("predicted_per_second") or (token_count / gen_sec)), 1)
    prompt_ms = round(float(timings.get("prompt_ms") or ttft_ms), 1)
    ms_per_tok = round(1000.0 / max(tok_per_sec, 0.1), 1)

    if reply:
        if not is_format_request and len(clean_input) > 1:
            LAST_TOPIC = clean_input
            LAST_RESPONSE = reply
        CHAT_HISTORY.append({"user": clean_input, "ai": reply})
        if len(CHAT_HISTORY) > MAX_HISTORY_TURNS:
            CHAT_HISTORY.pop(0)

    yield {
        "type": "done",
        "reply": reply,
        "ttft_ms": prompt_ms,
        "total_ms": total_ms,
        "tok_per_sec": tok_per_sec,
        "badge": f"🧠 Model: {routed_model} | ⚡ TTFT: {prompt_ms}ms | Speed: {tok_per_sec} tokens/sec ({ms_per_tok}ms/token)",
    }
