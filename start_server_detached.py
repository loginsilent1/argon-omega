import os
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(BASE_DIR, "app.py")


def is_server_alive() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def start_detached() -> None:
    if is_server_alive():
        print("[ARGON-OMEGA] Server is already running on http://127.0.0.1:8080")
        return

    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        [sys.executable, APP_PATH, "--no-browser"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    for _ in range(25):
        if is_server_alive():
            print(f"[ARGON-OMEGA] Detached server live (PID {proc.pid}) at http://127.0.0.1:8080")
            return
        time.sleep(0.25)
    print(f"[ARGON-OMEGA] Launched PID {proc.pid}; check http://127.0.0.1:8080")


if __name__ == "__main__":
    start_detached()
