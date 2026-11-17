import ctypes
import os
import re
import subprocess

from engine import generate_direct

SAST_PATTERNS = [
    ("CRITICAL", "Command Injection", r"\b(os\.system|subprocess\.\w+\([^)]*shell\s*=\s*True)\b", "Avoid shell=True or os.system with untrusted input; use subprocess.run([...], shell=False)."),
    ("CRITICAL", "Code Injection", r"\b(eval|exec|pickle\.loads)\s*\(", "Avoid eval/exec/pickle.loads on untrusted data; use ast.literal_eval or json.loads."),
    ("HIGH", "SQL Injection", r"(SELECT|INSERT|UPDATE|DELETE)\b.*(\+|%s|\{.*\}|f['\"])", "Use parameterized SQL queries (? placeholders) instead of string concatenation or f-strings."),
    ("HIGH", "XSS (Cross-Site Scripting)", r"(\.innerHTML\s*=|document\.write\s*\()", "Use textContent or sanitize HTML before inserting into the DOM."),
    ("HIGH", "Path Traversal", r"open\s*\([^)]*(\+|f['\"])", "Validate and canonicalize file paths with os.path.abspath / Path.resolve()."),
    ("MEDIUM", "Insecure Cryptography", r"\b(hashlib\.md5|hashlib\.sha1|random\.random)\b", "Use hashlib.sha256+ for hashing and secrets module for cryptographic randomness."),
    ("MEDIUM", "Buffer Overflow Risk (C/C++)", r"\b(strcpy|strcat|gets|sprintf)\s*\(", "Use bounded alternatives like strncpy, strncat, fgets, or snprintf."),
]


def _run_readonly_cmd(cmd: list[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=8, text=True, errors="replace")
        return out.strip()
    except Exception as exc:
        return f"Unavailable ({exc})"


def audit_laptop_security() -> dict:
    """Run safe, read-only local diagnostics and analyze with local mmap GGUF."""
    is_admin = False
    if os.name == "nt":
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            pass

    netstat_raw = _run_readonly_cmd(["netstat", "-ano"])
    listening = [ln.strip() for ln in netstat_raw.splitlines() if "LISTENING" in ln][:20]

    firewall_raw = _run_readonly_cmd(["netsh", "advfirewall", "show", "allprofiles", "state"]) if os.name == "nt" else "N/A"
    tasks_raw = _run_readonly_cmd(["tasklist"]) if os.name == "nt" else _run_readonly_cmd(["ps", "aux"])
    top_tasks = tasks_raw.splitlines()[:18]

    report_text = (
        f"Admin Privileges: {'YES (Elevated)' if is_admin else 'NO (Standard User)'}\n\n"
        f"Firewall Status:\n{firewall_raw}\n\n"
        f"Listening Ports (top 20):\n" + "\n".join(listening) + "\n\n"
        f"Background Processes (sample):\n" + "\n".join(top_tasks)
    )

    sys_prompt = (
        "You are ARGON-OMEGA, an offline cybersecurity auditor. "
        "Analyze the user's local Windows diagnostic report, highlight any security risks "
        "(exposed listening ports, disabled firewall, elevated privileges), and give concise lockdown steps."
    )
    ai_analysis, model_name = generate_direct(sys_prompt, report_text, max_tokens=512)
    if not ai_analysis:
        ai_analysis = (
            "Static Audit Summary:\n"
            f"- Elevated Admin: {is_admin}\n"
            f"- Listening Ports Found: {len(listening)}\n"
            "- Review any unexpected 0.0.0.0 listening ports and ensure Windows Defender Firewall is ON for all profiles."
        )

    return {
        "raw_report": report_text,
        "ai_analysis": ai_analysis,
        "model": model_name or "static-auditor",
    }


def scan_code_vulnerabilities(code: str) -> dict:
    """Static AST/regex SAST pass + deep neural review via local mmap GGUF."""
    findings = []
    lines = code.splitlines()
    for idx, line in enumerate(lines, start=1):
        for sev, vuln_type, pattern, fix_hint in SAST_PATTERNS:
            if re.search(pattern, line):
                findings.append(
                    {
                        "severity": sev,
                        "type": vuln_type,
                        "line": idx,
                        "code": line.strip(),
                        "fix": fix_hint,
                    }
                )

    static_summary = "\n".join(
        f"[{f['severity']}] Line {f['line']} ({f['type']}): {f['code']} -> {f['fix']}"
        for f in findings
    ) or "No obvious regex/AST pattern matches."

    sys_prompt = (
        "You are ARGON-OMEGA, an offline SAST & bug-hunting expert. "
        "Review the code for SQLi, XSS, Command Injection, Path Traversal, Buffer Overflows, insecure crypto, and logic bugs. "
        "Output a concise Severity Report (CRITICAL/HIGH/MEDIUM/LOW) with exact line numbers and explanations."
    )
    neural_review, model_name = generate_direct(
        sys_prompt,
        f"Static Findings:\n{static_summary}\n\nCode:\n```\n{code[:3500]}\n```",
        max_tokens=512,
    )

    return {
        "findings": findings,
        "static_summary": static_summary,
        "neural_review": neural_review or static_summary,
        "model": model_name or "static-sast",
    }


def auto_patch_code(code: str) -> dict:
    """Use local mmap GGUF to automatically patch vulnerabilities in the provided code."""
    sys_prompt = (
        "You are ARGON-OMEGA, an offline secure-coding expert. "
        "Fix all security vulnerabilities and bugs in the provided code. "
        "Return ONLY the patched, production-ready code."
    )
    patched, model_name = generate_direct(sys_prompt, code[:3500], max_tokens=768)
    return {
        "patched_code": patched or code,
        "model": model_name or "static",
    }
