import datetime
import hashlib
import os
import re

# Strictly forbidden write/modify/delete modes
FORBIDDEN_MODES = {"w", "wb", "a", "ab", "x", "xb", "r+", "w+", "a+", "+"}


def safe_readonly_read(filepath: str, max_bytes: int = 262144) -> bytes:
    """
    Opens any file strictly at the OS kernel level in READ-ONLY mode (os.O_RDONLY).
    Cannot write, replace, truncate, delete, or execute the file under any circumstance.
    """
    abs_path = os.path.abspath(filepath)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Path does not exist: {abs_path}")
    if os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a directory: {abs_path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(abs_path, flags)
    try:
        return os.read(fd, max_bytes)
    finally:
        os.close(fd)


def inspect_risky_file_readonly(filepath: str) -> dict:
    """
    Safely inspects any file (including risky .exe, .bat, .ps1, .vbs, .dll, .py, .js)
    in 100% Read-Only mode without executing or modifying it.
    """
    abs_path = os.path.abspath(filepath)
    stat = os.stat(abs_path)
    raw_bytes = safe_readonly_read(abs_path, max_bytes=524288)
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    text_preview = raw_bytes.decode("utf-8", errors="ignore")
    printable_strings = re.findall(r"[ -~]{6,}", text_preview)
    sample_text = "\n".join(printable_strings[:40])[:1500]

    risk_signatures = [
        ("Encoded PowerShell Payload", re.compile(r"powershell.*-(e|enc|encodedcommand)\b", re.I), "CRITICAL"),
        ("Hidden Window Execution", re.compile(r"-windowstyle\s+hidden|CreateNoWindow", re.I), "HIGH"),
        ("Registry Persistence (Autorun)", re.compile(r"CurrentVersion\\Run|HKCU\\Software\\Microsoft\\Windows", re.I), "HIGH"),
        ("Reverse Shell / Raw Socket", re.compile(r"socket\.socket|subprocess\.Popen.*shell\s*=\s*True|/bin/sh|cmd\.exe\s*/c", re.I), "HIGH"),
        ("Keylogger / Screen Hook API", re.compile(r"GetAsyncKeyState|SetWindowsHookEx|GetForegroundWindow", re.I), "CRITICAL"),
        ("Credential / Browser Stealer Target", re.compile(r"Login Data|Local State|CryptUnprotectData|lsass", re.I), "CRITICAL"),
        ("Suspicious Download Cradle", re.compile(r"Invoke-WebRequest|DownloadString|DownloadFile|bitsadmin|certutil\s+-urlcache", re.I), "HIGH"),
        ("OS Command Injection Pattern", re.compile(r"os\.system\(|eval\(|exec\(", re.I), "MEDIUM"),
    ]

    detected_risks = []
    for name, pattern, severity in risk_signatures:
        if pattern.search(text_preview):
            detected_risks.append((name, severity))

    ext = os.path.splitext(abs_path)[1].lower()
    is_executable_type = ext in {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi", ".com"}

    return {
        "path": abs_path,
        "size_kb": round(stat.st_size / 1024, 2),
        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": sha256_hash,
        "is_executable": is_executable_type,
        "risks": detected_risks,
        "preview": sample_text[:600] if not is_executable_type else "\n".join(printable_strings[:15])[:400],
    }


def resolve_special_folder(query: str) -> str:
    """Resolves user shortcuts like 'downloads', 'desktop', 'documents', 'startup', or explicit C:\\ paths."""
    q = query.lower()
    user_home = os.path.expanduser("~")
    path_match = re.search(r'([a-zA-Z]:\\[^\n\r"\'\?]+)', query)
    if path_match and os.path.exists(path_match.group(1).strip()):
        return path_match.group(1).strip()
    if "download" in q:
        return os.path.join(user_home, "Downloads")
    if "desktop" in q:
        return os.path.join(user_home, "Desktop")
    if "document" in q:
        return os.path.join(user_home, "Documents")
    if "startup" in q:
        return os.path.join(os.environ.get("APPDATA", user_home), r"Microsoft\Windows\Start Menu\Programs\Startup")
    return os.getcwd()
