import base64
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cognitive_core import clear_chat_history, line_by_line_fix_code, get_offline_mode, set_offline_mode
from database import check_storage_safety, detect_drives, init_db, set_db_path
from engine import (
    answer_query,
  cleanup_legacy_text_models,
    ensure_llama_server,
  get_elite_model_download_status,
  ingest_into_100m_matrix,
  init_code_vault,
  init_token_matrix,
    list_cartridges,
    set_selected_language,
    set_selected_mode,
  start_elite_model_download,
  stop_llama_server,
    stream_query,
  verify_elite_model_urls,
)
from harvester import harvester, scan_import_folder
from image_editor import apply_image_edit
from image_engine import SSD_IMAGES_DIR, generate_image, generate_image_hybrid, get_flux_rom_status, get_image_model_vault, list_ssd_images
from rom_vault import download_cve_datasets, search_cve_vault
from sandbox import run_code_in_sandbox, run_safe_terminal_command
from security_lab import audit_laptop_security, auto_patch_code, scan_code_vulnerabilities
from setup_engine import download_llama_server
from video_engine import VIDEOS_DIR, generate_video, get_video_studio_status, start_video_model_download

HOST = "0.0.0.0"
PORT = 8080
BOOT_ID = str(time.time())
TERMINAL_CWD = os.path.expanduser("~")
LLAMA_UPDATE_STATUS = {"running": False, "message": "llama.cpp updater idle"}
QUANTUM_MODE = {"enabled": True}
init_code_vault()
init_token_matrix()


def _update_llama_server_background() -> None:
  LLAMA_UPDATE_STATUS.update({"running": True, "message": "Downloading latest Windows AVX2 llama.cpp release..."})
  try:
    stop_llama_server()
    download_llama_server(force=True)
    LLAMA_UPDATE_STATUS.update({"running": False, "message": "llama.cpp update completed; the server will restart on the next request."})
  except Exception as exc:
    LLAMA_UPDATE_STATUS.update({"running": False, "message": f"llama.cpp update failed: {exc}"})

HTML_UI = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ARGON-OMEGA | Offline AI</title>
  <style>
    :root {
      --bg-main: #212121;
      --bg-sidebar: #171717;
      --bg-surface: #2f2f2f;
      --bg-surface-hover: #3a3a3a;
      --border: rgba(255, 255, 255, 0.1);
      --border-subtle: rgba(255, 255, 255, 0.06);
      --text: #ececec;
      --text-muted: #9b9b9b;
      --accent: #10a37f;
      --gemini-grad: linear-gradient(135deg, #4285f4, #9b72cb, #d96570);
      --sidebar-w: 280px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--bg-main);
      color: var(--text);
      height: 100vh;
      display: flex;
      overflow: hidden;
      -webkit-font-smoothing: antialiased;
    }

    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 999px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.28); }

    /* Sidebar */
    .sidebar {
      width: var(--sidebar-w);
      background: var(--bg-sidebar);
      border-right: 1px solid var(--border-subtle);
      display: flex;
      flex-direction: column;
      padding: 12px;
      gap: 10px;
      transition: width 0.22s ease, padding 0.22s ease, opacity 0.2s ease;
      overflow-y: auto;
      flex-shrink: 0;
    }
    .sidebar.collapsed {
      width: 0;
      padding: 12px 0;
      opacity: 0;
      pointer-events: none;
      border-right: none;
    }

    .sidebar-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .btn-new-chat {
      flex: 1;
      display: flex;
      align-items: center;
      gap: 8px;
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 9px 12px;
      border-radius: 10px;
      font-size: 0.88rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-new-chat:hover { background: var(--bg-surface); }

    .icon-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      width: 36px;
      height: 36px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 1.1rem;
      transition: background 0.15s, color 0.15s;
    }
    .icon-btn:hover { background: var(--bg-surface); color: var(--text); }

    .sidebar-section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 6px 8px 2px;
    }
    .sidebar-section-title {
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .clear-all-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 0.72rem;
      cursor: pointer;
      padding: 2px 6px;
      border-radius: 5px;
    }
    .clear-all-btn:hover { background: var(--bg-surface); color: #f87171; }

    .history-list {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-height: 80px;
    }
    .history-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 0.85rem;
      color: var(--text-muted);
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .history-item-title {
      flex: 1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .history-item:hover, .history-item.active {
      background: var(--bg-surface);
      color: var(--text);
    }
    .history-del-btn {
      opacity: 0;
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 0.85rem;
      padding: 2px 5px;
      border-radius: 5px;
      line-height: 1;
      transition: opacity 0.15s, color 0.15s, background 0.15s;
    }
    .history-item:hover .history-del-btn,
    .history-item.active .history-del-btn {
      opacity: 1;
    }
    .history-del-btn:hover {
      color: #f87171;
      background: rgba(239, 68, 68, 0.15);
    }

    .telemetry-stack {
      display: flex;
      flex-direction: column;
      gap: 8px;
      border-top: 1px solid var(--border-subtle);
      padding-top: 10px;
    }

    .card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .card-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-label {
      font-size: 0.72rem;
      color: var(--text-muted);
      font-weight: 500;
    }
    .metric {
      font-size: 0.92rem;
      font-weight: 600;
      color: var(--text);
    }
    .sub-metric {
      font-size: 0.72rem;
      color: var(--text-muted);
      line-height: 1.35;
    }

    .progress-bar {
      width: 100%;
      height: 5px;
      background: rgba(255,255,255,0.08);
      border-radius: 999px;
      overflow: hidden;
      margin: 2px 0;
    }
    .progress-fill {
      height: 100%;
      width: 0%;
      background: var(--gemini-grad);
      transition: width 0.3s ease;
    }

    .badge-guard {
      font-size: 0.68rem;
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(16, 163, 127, 0.15);
      border: 1px solid rgba(16, 163, 127, 0.4);
      color: #34d399;
      font-weight: 600;
    }
    .badge-guard.danger {
      background: rgba(239, 68, 68, 0.15);
      border-color: rgba(239, 68, 68, 0.4);
      color: #f87171;
    }

    .btn-firehose {
      width: 100%;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .btn-firehose:hover { background: var(--bg-surface-hover); }
    .btn-firehose.running {
      background: rgba(16, 163, 127, 0.18);
      border-color: var(--accent);
      color: #34d399;
    }

    select, input[type="text"], textarea {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 8px;
      padding: 7px 10px;
      font-size: 0.82rem;
      outline: none;
      font-family: inherit;
    }
    select:focus, input[type="text"]:focus, textarea:focus {
      border-color: rgba(255,255,255,0.3);
    }

    .btn-subtle {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 10px;
      border-radius: 8px;
      font-size: 0.78rem;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-subtle:hover { background: var(--bg-surface-hover); }

    /* Main Area */
    .main {
      flex: 1;
      display: flex;
      flex-direction: column;
      height: 100vh;
      min-width: 0;
      position: relative;
    }

    /* Topbar */
    .topbar {
      height: 56px;
      padding: 0 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .topbar-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .model-pill {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-radius: 10px;
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--text);
      cursor: pointer;
      transition: background 0.15s;
    }
    .model-pill:hover { background: var(--bg-surface); }
    .model-pill span.tag {
      font-size: 0.72rem;
      font-weight: 500;
      color: var(--text-muted);
      background: var(--bg-surface);
      padding: 2px 7px;
      border-radius: 6px;
    }

    .tabs {
      display: flex;
      gap: 4px;
      background: var(--bg-sidebar);
      padding: 4px;
      border-radius: 10px;
      border: 1px solid var(--border-subtle);
    }
    .tab-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 6px 12px;
      border-radius: 7px;
      font-size: 0.82rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .tab-btn.active {
      background: var(--bg-surface);
      color: var(--text);
    }

    .tab-view {
      display: none;
      flex: 1;
      flex-direction: column;
      overflow: hidden;
    }
    .tab-view.active { display: flex; }

    /* Chat Stream */
    .chat-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 20px 16px 32px;
      display: flex;
      flex-direction: column;
    }
    .chat-container {
      width: 100%;
      max-width: 768px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 24px;
      flex: 1;
    }

    /* Welcome Screen */
    .welcome-screen {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 40px 16px;
      gap: 24px;
    }
    .welcome-title {
      font-size: 1.85rem;
      font-weight: 600;
      background: var(--gemini-grad);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -0.02em;
    }
    .welcome-sub {
      color: var(--text-muted);
      font-size: 0.92rem;
      max-width: 480px;
      margin-top: -14px;
    }
    .suggestion-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      width: 100%;
      max-width: 620px;
    }
    .suggestion-chip {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 12px 14px;
      text-align: left;
      color: var(--text);
      font-size: 0.85rem;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .suggestion-chip:hover {
      background: var(--bg-surface-hover);
      border-color: var(--border);
    }
    .suggestion-chip span {
      font-size: 0.76rem;
      color: var(--text-muted);
    }

    /* Messages */
    .msg-row {
      display: flex;
      width: 100%;
      gap: 14px;
      animation: fadeIn 0.18s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .msg-row.user {
      justify-content: flex-end;
    }
    .msg-user-wrap {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      max-width: 78%;
      gap: 4px;
    }
    .msg-bubble-user {
      background: var(--bg-surface);
      color: var(--text);
      padding: 11px 16px;
      border-radius: 20px;
      font-size: 0.95rem;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .msg-user-actions {
      display: flex;
      gap: 6px;
      opacity: 0;
      transition: opacity 0.15s;
    }
    .msg-row.user:hover .msg-user-actions {
      opacity: 1;
    }
    .msg-action-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 0.74rem;
      padding: 2px 6px;
      border-radius: 5px;
    }
    .msg-action-btn:hover { background: var(--bg-surface); color: var(--text); }
    .msg-action-btn.del:hover { color: #f87171; background: rgba(239, 68, 68, 0.14); }

    .msg-row.ai {
      align-items: flex-start;
    }
    .ai-avatar {
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background: var(--gemini-grad);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.9rem;
      flex-shrink: 0;
      margin-top: 2px;
      box-shadow: 0 0 12px rgba(155, 114, 203, 0.25);
    }
    .msg-ai-body {
      flex: 1;
      min-width: 0;
      font-size: 0.95rem;
      line-height: 1.65;
      color: var(--text);
    }
    .msg-ai-content {
      white-space: pre-wrap;
      word-break: break-word;
    }
    .msg-ai-content pre {
      background: #141414;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
      overflow-x: auto;
      margin: 10px 0;
      font-family: ui-monospace, Consolas, monospace;
      font-size: 0.85rem;
      line-height: 1.5;
    }
    .msg-ai-content code {
      background: rgba(255,255,255,0.08);
      padding: 2px 5px;
      border-radius: 5px;
      font-family: ui-monospace, Consolas, monospace;
      font-size: 0.86em;
    }
    .msg-ai-content pre code {
      background: transparent;
      padding: 0;
    }
    .msg-footer {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 8px;
      font-size: 0.74rem;
      color: var(--text-muted);
    }
    .copy-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 0.74rem;
      padding: 2px 6px;
      border-radius: 5px;
    }
    .copy-btn:hover { background: var(--bg-surface); color: var(--text); }

    /* Typing dots */
    .typing-dots {
      display: inline-flex;
      gap: 4px;
      padding: 8px 0;
    }
    .typing-dots span {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--text-muted);
      animation: pulse 1.2s infinite ease-in-out;
    }
    .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
    .typing-dots span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes pulse {
      0%, 80%, 100% { opacity: 0.3; transform: scale(0.85); }
      40% { opacity: 1; transform: scale(1.05); }
    }

    /* Floating Input Bar */
    .input-wrapper {
      padding: 0 16px 16px;
      flex-shrink: 0;
    }
    .input-pill {
      max-width: 768px;
      margin: 0 auto;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 10px 12px 10px 18px;
      display: flex;
      align-items: flex-end;
      gap: 10px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
      transition: border-color 0.15s;
    }
    .input-pill:focus-within {
      border-color: rgba(255, 255, 255, 0.25);
    }
    .input-pill textarea {
      flex: 1;
      background: transparent;
      border: none;
      color: var(--text);
      font-size: 0.95rem;
      line-height: 1.5;
      max-height: 180px;
      resize: none;
      padding: 4px 0;
      outline: none;
    }
    .send-btn {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: none;
      background: #ffffff;
      color: #171717;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      flex-shrink: 0;
      font-size: 1rem;
      font-weight: 700;
      transition: opacity 0.15s, transform 0.1s;
    }
    .send-btn:hover { opacity: 0.9; }
    .send-btn:active { transform: scale(0.95); }
    .send-btn:disabled {
      background: rgba(255,255,255,0.2);
      color: rgba(255,255,255,0.4);
      cursor: not-allowed;
    }
    .disclaimer {
      text-align: center;
      font-size: 0.72rem;
      color: var(--text-muted);
      margin-top: 8px;
    }

    .img-toggle-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 6px 11px;
      border-radius: 16px;
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
      flex-shrink: 0;
      transition: all 0.15s;
    }
    .img-toggle-btn:hover { background: var(--bg-surface-hover); color: var(--text); }
    .img-toggle-btn.active {
      background: linear-gradient(135deg, #4285f4, #9b72cb);
      border-color: transparent;
      color: #ffffff;
    }

    .chat-image-card {
      margin-top: 10px;
      background: #141414;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 10px;
      max-width: 440px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .chat-image-card img {
      width: 100%;
      border-radius: 10px;
      display: block;
      cursor: pointer;
    }
    .chat-image-actions {
      display: flex;
      gap: 8px;
    }

    .gallery-container {
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      max-width: 1100px;
      width: 100%;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .gallery-toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
    }
    .gallery-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
      gap: 14px;
    }
    .gallery-card {
      background: var(--bg-sidebar);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .gallery-card img {
      width: 100%;
      aspect-ratio: 1 / 1;
      object-fit: cover;
      border-radius: 8px;
      cursor: pointer;
      background: #0b0f19;
    }
    .gallery-prompt {
      font-size: 0.8rem;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .lightbox-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.88);
      z-index: 200;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .lightbox-modal.open { display: flex; }
    .lightbox-modal img {
      max-width: 92vw;
      max-height: 88vh;
      border-radius: 12px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.6);
    }

    /* Image Editor */
    .editor-container {
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      max-width: 1100px;
      width: 100%;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .editor-bar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .quick-edit-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .split-wrap {
      position: relative;
      width: 100%;
      max-width: 680px;
      aspect-ratio: 1 / 1;
      margin: 0 auto;
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #111;
    }
    .split-wrap img {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
      user-select: none;
    }
    .split-after {
      clip-path: inset(0 0 0 50%);
    }
    .split-slider {
      width: 100%;
      max-width: 680px;
      margin: 0 auto;
      display: block;
    }

    /* Security Lab */
    .lab-container {
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      max-width: 1100px;
      width: 100%;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      align-content: start;
    }
    .lab-panel {
      background: var(--bg-sidebar);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .lab-panel.full { grid-column: 1 / -1; }
    .output-pre {
      background: #111111;
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 12px;
      font-family: ui-monospace, Consolas, monospace;
      font-size: 0.82rem;
      white-space: pre-wrap;
      max-height: 260px;
      overflow-y: auto;
      color: #d4d4d4;
    }
    textarea.code-editor {
      width: 100%;
      min-height: 150px;
      font-family: ui-monospace, Consolas, monospace;
      font-size: 0.85rem;
      resize: vertical;
    }

    @media (max-width: 768px) {
      body { height: 100dvh; }
      .suggestion-grid, .lab-container { grid-template-columns: 1fr; }
      .sidebar { position: fixed; left: 0; top: 0; bottom: 0; z-index: 100; width: 82vw; max-width: 300px; box-shadow: 4px 0 24px rgba(0,0,0,0.6); }
      .sidebar.collapsed { transform: translateX(-100%); margin-left: 0 !important; }
      .topbar { padding: 8px 10px; gap: 6px; flex-wrap: wrap; }
      .topbar-controls { gap: 6px !important; }
      .topbar-controls select { max-width: 140px !important; font-size: 0.78rem; }
      .tabs { width: 100%; overflow-x: auto; display: flex; gap: 6px; padding-bottom: 2px; }
      .tab-btn { white-space: nowrap; font-size: 0.78rem; padding: 6px 10px; }
      #matrixCounter { display: none; }
      .chat-scroll { padding: 12px 10px; }
      .msg-row.user .msg-bubble-user, .msg-row.ai { max-width: 94%; }
      .md-content table { display: block; overflow-x: auto; white-space: nowrap; }
      .md-content pre { overflow-x: auto; max-width: 100%; }
      .input-bar, .composer-wrap { padding: 8px 10px; }
      #promptInput { font-size: 16px !important; min-height: 44px; }
      .icon-btn, button { min-height: 38px; }
    }
    .typing-cursor {
      display: inline-block;
      animation: blink 0.8s infinite;
      color: #00ffcc;
      font-weight: bold;
      margin-left: 2px;
    }
    @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
    .md-content h1 { font-size: 1.55em; font-weight: 800; color: #38bdf8; margin: 10px 0 6px 0; border-bottom: 1px solid rgba(56,189,248,0.25); padding-bottom: 4px; }
    .md-content h2 { font-size: 1.35em; font-weight: 800; color: #7dd3fc; margin: 9px 0 5px 0; }
    .md-content h3 { font-size: 1.18em; font-weight: 700; color: #bae6fd; margin: 8px 0 4px 0; }
    .md-content strong, .md-content b { font-weight: 800; font-size: 1.12em; color: #ffffff; letter-spacing: 0.01em; }
    .md-content em, .md-content i { font-style: italic; color: #e2e8f0; }
    .md-content ul, .md-content ol { margin: 6px 0 6px 22px; padding: 0; }
    .md-content li { margin: 4px 0; line-height: 1.5; }
    .md-content table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.95em; background: rgba(15, 23, 42, 0.6); border-radius: 8px; overflow: hidden; }
    .md-content th { background: rgba(56, 189, 248, 0.2); color: #38bdf8; font-weight: 800; font-size: 1.05em; text-align: left; padding: 8px 12px; border: 1px solid rgba(255,255,255,0.12); }
    .md-content td { padding: 7px 12px; border: 1px solid rgba(255,255,255,0.08); }
    .md-content tr:nth-child(even) td { background: rgba(255,255,255,0.03); }
    .math-block { display: block; margin: 10px 0; padding: 10px 14px; background: rgba(15, 23, 42, 0.75); border-left: 3px solid #38bdf8; border-radius: 6px; font-family: 'Cambria Math', 'Times New Roman', serif; font-size: 1.08em; color: #e0f2fe; }
    .math-inline { font-family: 'Cambria Math', 'Times New Roman', serif; font-style: italic; color: #bae6fd; background: rgba(56,189,248,0.1); padding: 1px 5px; border-radius: 4px; }
    .code-card { margin: 10px 0; border-radius: 10px; overflow: hidden; border: 1px solid rgba(255,255,255,0.12); background: #1e1e1e; box-shadow: 0 4px 16px rgba(0,0,0,0.35); }
    .code-card-header { display: flex; align-items: center; justify-content: space-between; padding: 6px 12px; background: #252526; border-bottom: 1px solid rgba(255,255,255,0.08); font-size: 0.76rem; color: #cbd5e1; gap: 8px; }
    .code-card-body { display: flex; flex-direction: column; align-items: stretch; width: 100%; padding: 10px 12px; overflow-x: auto; white-space: pre; font-family: 'Consolas', 'Fira Code', monospace; font-size: 0.84rem; line-height: 1.5; color: #d4d4d4; background: #1e1e1e; margin: 0; }
    .code-line { display: flex; flex-direction: row; width: 100%; min-height: 20px; line-height: 1.5; white-space: pre; gap: 12px; }
    .code-line-num { color: #858585; user-select: none; min-width: 28px; text-align: right; flex-shrink: 0; }
    .tok-kw { color: #c586c0; font-weight: 600; }
    .tok-decl { color: #569cd6; font-weight: 600; }
    .tok-tag { color: #569cd6; }
    .tok-attr { color: #9cdcfe; }
    .tok-sel { color: #d7ba7d; }
    .tok-fn { color: #dcdcaa; }
    .tok-obj { color: #4ec9b0; }
    .tok-str { color: #ce9178; }
    .tok-num { color: #b5cea8; }
    .tok-com { color: #6a9955; font-style: italic; }
    .inline-run-box { padding: 10px 12px; background: #0b1120; border-top: 1px solid rgba(56,189,248,0.25); font-family: monospace; font-size: 0.82rem; color: #e2e8f0; }
    .inline-run-frame { width: 100%; height: 340px; border: 1px solid rgba(56,189,248,0.3); border-radius: 8px; background: #0f172a; }

    /* ==================================================================
       ARGON v2 — visual layer: texture, depth, chat UX
       Appended last so it overrides the base rules above.
       ================================================================== */
    :root {
      --bg-main: #0a0e14;
      --bg-sidebar: #060a10;
      --bg-surface: #101722;
      --bg-surface-hover: #17202c;
      --border: rgba(148, 163, 184, 0.16);
      --border-subtle: rgba(148, 163, 184, 0.09);
      --text: #e9eef6;
      --text-muted: #8ea1b7;
      --accent: #10b981;
      --accent-2: #22d3ee;
      --accent-3: #8b5cf6;
      --accent-soft: rgba(16, 185, 129, 0.14);
      --grad-brand: linear-gradient(135deg, #10b981, #22d3ee 55%, #8b5cf6);
      --grad-user: linear-gradient(135deg, rgba(16,185,129,.95), rgba(13,148,136,.92));
      --radius-lg: 20px;
      --radius: 14px;
      --radius-sm: 10px;
      --shadow-1: 0 1px 2px rgba(0,0,0,.45), 0 14px 30px -20px rgba(0,0,0,.9);
      --shadow-glow: 0 0 0 1px rgba(16,185,129,.28), 0 16px 40px -18px rgba(16,185,129,.42);
      --font-sans: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
      --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }

    html, body { height: 100%; }
    body {
      font-family: var(--font-sans);
      color: var(--text);
      letter-spacing: 0.1px;
      background:
        radial-gradient(1100px 760px at 8% -12%, rgba(16,185,129,.13), transparent 62%),
        radial-gradient(900px 620px at 102% -4%, rgba(34,211,238,.10), transparent 58%),
        radial-gradient(760px 520px at 50% 118%, rgba(139,92,246,.10), transparent 60%),
        var(--bg-main);
      background-attachment: fixed;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    /* fine grain + vignette so flat panels read as surfaces, not voids */
    body::before {
      content: "";
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      opacity: .55; mix-blend-mode: soft-light;
      background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='3' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='180' height='180' filter='url(%23n)' opacity='0.38'/></svg>");
    }
    body::after {
      content: "";
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background: radial-gradient(120% 90% at 50% 0%, transparent 52%, rgba(2,6,12,.55) 100%);
    }
    .sidebar, .main { position: relative; z-index: 1; }

    ::selection { background: rgba(16,185,129,.32); color: #f8fffb; }
    * { scrollbar-width: thin; scrollbar-color: rgba(148,163,184,.32) transparent; }
    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb {
      background: rgba(148,163,184,.26); border-radius: 99px;
      border: 3px solid transparent; background-clip: content-box;
    }
    ::-webkit-scrollbar-thumb:hover { background: rgba(148,163,184,.45); background-clip: content-box; }

    :focus-visible { outline: 2px solid rgba(34,211,238,.75); outline-offset: 2px; border-radius: 6px; }

    /* ---------------- sidebar ---------------- */
    .sidebar {
      width: 272px;
      background:
        linear-gradient(180deg, rgba(16,185,129,.05), transparent 22%),
        linear-gradient(180deg, rgba(6,10,16,.96), rgba(6,10,16,.99));
      border-right: 1px solid var(--border-subtle);
      backdrop-filter: blur(18px) saturate(120%);
    }
    .sidebar-header { padding: 14px 12px 10px; gap: 8px; align-items: center; }
    .sidebar-brand {
      display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;
      font-weight: 800; font-size: .95rem; letter-spacing: .3px;
    }
    .sidebar-brand .orb {
      width: 28px; height: 28px; border-radius: 9px; flex: none;
      background: var(--grad-brand); display: grid; place-items: center;
      color: #05100c; font-size: .95rem; font-weight: 900;
      box-shadow: 0 0 0 1px rgba(255,255,255,.14) inset, 0 8px 20px -10px rgba(16,185,129,.8);
    }
    .sidebar-brand .brand-text { display: flex; flex-direction: column; line-height: 1.1; min-width: 0; }
    .sidebar-brand .brand-text small { color: var(--text-muted); font-size: .62rem; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; }

    .btn-new-chat {
      background: linear-gradient(180deg, rgba(16,185,129,.16), rgba(16,185,129,.06));
      border: 1px solid rgba(16,185,129,.35);
      color: #d8ffee; font-weight: 700; border-radius: 12px;
      padding: 10px 12px; transition: transform .15s ease, box-shadow .2s ease, background .2s ease;
    }
    .btn-new-chat:hover {
      transform: translateY(-1px);
      box-shadow: 0 10px 26px -16px rgba(16,185,129,.9);
      background: linear-gradient(180deg, rgba(16,185,129,.24), rgba(16,185,129,.1));
    }
    .btn-new-chat:active { transform: translateY(0) scale(.99); }

    .icon-btn {
      border-radius: 10px; border: 1px solid transparent; color: var(--text-muted);
      transition: background .18s ease, color .18s ease, border-color .18s ease;
    }
    .icon-btn:hover { background: var(--bg-surface); color: var(--text); border-color: var(--border-subtle); }

    .sidebar-search {
      margin: 2px 12px 8px; display: flex; align-items: center; gap: 8px;
      padding: 8px 10px; border-radius: 10px;
      background: rgba(148,163,184,.06); border: 1px solid var(--border-subtle);
      transition: border-color .18s ease, background .18s ease;
    }
    .sidebar-search:focus-within { border-color: rgba(34,211,238,.45); background: rgba(34,211,238,.06); }
    .sidebar-search span { font-size: .8rem; color: var(--text-muted); }
    .sidebar-search input {
      flex: 1; min-width: 0; background: transparent; border: none; outline: none;
      color: var(--text); font-size: .82rem; font-family: inherit;
    }

    .sidebar-section-header { padding: 4px 16px 6px; }
    .sidebar-section-title {
      font-size: .66rem; letter-spacing: .16em; text-transform: uppercase;
      color: var(--text-muted); font-weight: 700;
    }
    .clear-all-btn { font-size: .68rem; border-radius: 8px; padding: 3px 8px; }
    .history-list { padding: 0 10px 10px; gap: 2px; }
    .history-item {
      border-radius: 10px; padding: 9px 10px; position: relative;
      transition: background .16s ease, transform .16s ease;
    }
    .history-item:hover { background: var(--bg-surface); transform: translateX(1px); }
    .history-item.active { background: rgba(16,185,129,.1); }
    .history-item.active::before {
      content: ""; position: absolute; left: 0; top: 8px; bottom: 8px; width: 2px;
      border-radius: 2px; background: var(--grad-brand);
    }
    .history-item-title { font-size: .82rem; color: #cfdae8; }
    .history-item.active .history-item-title { color: #eafff7; font-weight: 600; }
    .history-empty {
      padding: 10px 14px; font-size: .76rem; color: var(--text-muted);
      line-height: 1.5;
    }

    .telemetry-stack { padding: 0 12px 14px; gap: 8px; }
    .card {
      background: linear-gradient(180deg, rgba(148,163,184,.07), rgba(148,163,184,.03));
      border: 1px solid var(--border-subtle); border-radius: var(--radius);
      padding: 11px 12px; backdrop-filter: blur(6px);
    }
    .card-label { font-size: .66rem; letter-spacing: .12em; text-transform: uppercase; color: var(--text-muted); }
    .metric { font-variant-numeric: tabular-nums; font-weight: 700; }
    .progress-bar { height: 6px; border-radius: 99px; background: rgba(148,163,184,.14); overflow: hidden; }
    .progress-fill { border-radius: 99px; background: var(--grad-brand); transition: width .5s ease; }
    .sidebar-foot {
      padding: 10px 16px; border-top: 1px solid var(--border-subtle);
      display: flex; align-items: center; gap: 8px;
      font-size: .7rem; color: var(--text-muted);
    }
    .sidebar-foot .dot {
      width: 7px; height: 7px; border-radius: 99px; background: var(--accent);
      box-shadow: 0 0 0 3px rgba(16,185,129,.18); flex: none;
    }

    /* ---------------- top bar ---------------- */
    .main { background: transparent; }
    .topbar {
      position: sticky; top: 0; z-index: 20;
      padding: 10px 16px; gap: 10px;
      background: rgba(10,14,20,.72);
      border-bottom: 1px solid var(--border-subtle);
      backdrop-filter: blur(18px) saturate(130%);
    }
    .model-pill {
      border-radius: 12px; padding: 6px 12px; gap: 8px;
      border: 1px solid var(--border-subtle);
      background: linear-gradient(180deg, rgba(148,163,184,.08), rgba(148,163,184,.03));
      font-weight: 700; letter-spacing: .2px;
    }
    .model-pill:hover { border-color: rgba(16,185,129,.35); background: rgba(16,185,129,.08); }
    .model-pill span.tag {
      background: var(--grad-brand); color: #04120c; font-weight: 800;
      border-radius: 99px; padding: 2px 8px; font-size: .62rem; letter-spacing: .04em;
    }
    .top-chip {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: .7rem; font-weight: 700; padding: 5px 10px; border-radius: 99px;
      border: 1px solid rgba(251,191,36,.38); background: rgba(251,191,36,.12); color: #fcd34d;
    }
    .topbar-controls select, .lab-container select, .gallery-toolbar select {
      background: rgba(16,23,34,.9); color: var(--text);
      border: 1px solid var(--border); border-radius: 10px;
      padding: 6px 9px; font-size: .76rem; font-family: inherit;
      transition: border-color .18s ease;
    }
    .topbar-controls select:hover, select:hover { border-color: rgba(34,211,238,.4); }

    .tabs { gap: 6px; padding: 4px 0 0; border-top: 1px solid var(--border-subtle); margin-top: 4px; }
    .tab-btn {
      border-radius: 10px; padding: 7px 12px; font-size: .78rem; font-weight: 600;
      color: var(--text-muted); background: transparent; border: 1px solid transparent;
      transition: color .16s ease, background .16s ease, border-color .16s ease;
    }
    .tab-btn:hover { color: var(--text); background: rgba(148,163,184,.08); }
    .tab-btn.active {
      color: #d9fff1; background: linear-gradient(.0deg, rgba(16,185,129,.16), rgba(16,185,129,.08));
      border-color: rgba(16,185,129,.4); box-shadow: 0 8px 20px -18px rgba(16,185,129,.9);
    }

    /* ---------------- chat ---------------- */
    .chat-scroll { padding: 18px 16px 8px; scroll-behavior: smooth; }
    .chat-container { max-width: 860px; margin: 0 auto; gap: 18px; }

    .welcome-screen { padding: 6vh 8px 0; gap: 12px; }
    .welcome-orb {
      width: 62px; height: 62px; border-radius: 20px; margin: 0 auto 4px;
      background: var(--grad-brand); display: grid; place-items: center;
      font-size: 1.7rem; color: #04120c;
      box-shadow: 0 20px 50px -22px rgba(16,185,129,.9), 0 0 0 1px rgba(255,255,255,.14) inset;
      animation: aFloat 6s ease-in-out infinite;
    }
    @keyframes aFloat { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
    .welcome-title {
      font-size: clamp(1.5rem, 3.4vw, 2.15rem); font-weight: 800; letter-spacing: -.6px;
      background: linear-gradient(100deg, #f4fffb, #a7f3d0 45%, #67e8f9);
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .welcome-sub { color: var(--text-muted); font-size: .88rem; max-width: 560px; }
    .welcome-badge {
      display: inline-flex; align-items: center; gap: 7px; margin: 2px auto 0;
      padding: 5px 12px; border-radius: 99px; font-size: .72rem; font-weight: 600;
      color: #a7f3d0; background: rgba(16,185,129,.1); border: 1px solid rgba(16,185,129,.28);
    }

    .suggestion-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 14px; width: 100%; max-width: 700px; }
    .suggestion-chip {
      display: flex; align-items: flex-start; gap: 11px; text-align: left;
      padding: 13px 14px; border-radius: var(--radius);
      border: 1px solid var(--border-subtle);
      background: linear-gradient(180deg, rgba(148,163,184,.09), rgba(148,163,184,.035));
      transition: transform .16s ease, border-color .18s ease, box-shadow .2s ease, background .2s ease;
    }
    .suggestion-chip::before {
      content: attr(data-icon); display: grid; place-items: center;
      width: 30px; height: 30px; border-radius: 9px; flex: none; font-size: .95rem;
      background: rgba(16,185,129,.13); border: 1px solid rgba(16,185,129,.26);
    }
    .suggestion-chip strong { display: block; font-size: .84rem; color: #e9fbf4; margin-bottom: 2px; }
    .suggestion-chip span { font-size: .74rem; color: var(--text-muted); }
    .suggestion-chip:hover {
      transform: translateY(-2px); border-color: rgba(16,185,129,.4);
      box-shadow: 0 18px 34px -24px rgba(16,185,129,.85);
      background: linear-gradient(180deg, rgba(16,185,129,.12), rgba(148,163,184,.04));
    }

    .msg-row { gap: 12px; padding: 2px 0; animation: msgIn .26s ease both; }
    @keyframes msgIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
    .msg-row.user { align-items: flex-end; }
    .msg-bubble-user {
      background: var(--grad-user); color: #04140e; font-weight: 500;
      border-radius: 18px 18px 4px 18px; padding: 11px 15px;
      box-shadow: 0 14px 30px -22px rgba(16,185,129,.95), 0 1px 0 rgba(255,255,255,.14) inset;
      max-width: min(78%, 620px);
    }
    .msg-user-actions { opacity: 0; transition: opacity .18s ease; }
    .msg-row.user:hover .msg-user-actions, .msg-row.user:focus-within .msg-user-actions { opacity: 1; }
    .msg-action-btn {
      border-radius: 8px; font-size: .7rem; padding: 3px 8px;
      color: var(--text-muted); border: 1px solid transparent;
      transition: background .16s ease, color .16s ease;
    }
    .msg-action-btn:hover { background: rgba(148,163,184,.12); color: var(--text); }

    .ai-avatar {
      width: 30px; height: 30px; border-radius: 10px; font-size: .85rem;
      background: var(--grad-brand); color: #04120c; font-weight: 900;
      box-shadow: 0 0 0 1px rgba(255,255,255,.16) inset, 0 10px 22px -14px rgba(16,185,129,.9);
      margin-top: 2px;
    }
    .msg-ai-body { gap: 8px; max-width: 100%; }
    .msg-ai-content, .msg-ai-text {
      font-size: .93rem; line-height: 1.72; color: #dfe8f3;
      background: linear-gradient(180deg, rgba(148,163,184,.055), rgba(148,163,184,.02));
      border: 1px solid var(--border-subtle); border-radius: 4px 18px 18px 18px;
      padding: 13px 16px;
    }
    .msg-ai-content p, .msg-ai-text p { margin: 0 0 .65em; }
    .msg-ai-content p:last-child, .msg-ai-text p:last-child { margin-bottom: 0; }
    .msg-ai-content h1, .msg-ai-content h2, .msg-ai-content h3, .msg-ai-text h3 {
      font-size: .98rem; margin: .2em 0 .5em; color: #f2fff9; letter-spacing: -.2px;
    }
    .msg-ai-content ul, .msg-ai-content ol { padding-left: 1.15em; margin: .4em 0 .7em; }
    .msg-ai-content li { margin: .22em 0; }
    .msg-ai-content blockquote {
      border-left: 2px solid rgba(16,185,129,.5); margin: .6em 0; padding: .2em 0 .2em .9em;
      color: var(--text-muted);
    }
    .msg-ai-content table { border-collapse: collapse; width: 100%; margin: .6em 0; font-size: .84rem; }
    .msg-ai-content th, .msg-ai-content td { border: 1px solid var(--border-subtle); padding: 6px 9px; text-align: left; }
    .msg-ai-content th { background: rgba(148,163,184,.08); font-weight: 700; }
    .msg-ai-content code:not(pre code) {
      background: rgba(34,211,238,.1); border: 1px solid rgba(34,211,238,.18);
      color: #a5f3fc; border-radius: 6px; padding: 1px 5px; font-size: .86em;
      font-family: var(--font-mono);
    }
    .msg-ai-content a { color: #67e8f9; text-decoration: underline; text-underline-offset: 2px; }
    .muted { color: var(--text-muted); font-size: .78em; }

    .msg-footer { gap: 6px; opacity: .35; transition: opacity .18s ease, transform .18s ease; }
    .msg-row.ai:hover .msg-footer, .msg-row.ai:focus-within .msg-footer { opacity: 1; }
    .msg-footer > span:first-child {
      font-size: .68rem; color: var(--text-muted); font-family: var(--font-mono);
      letter-spacing: -.1px;
    }
    .copy-btn {
      border-radius: 8px; font-size: .7rem; padding: 3px 9px;
      border: 1px solid var(--border-subtle); background: rgba(148,163,184,.06);
      color: var(--text-muted); transition: all .16s ease;
    }
    .copy-btn:hover { background: rgba(16,185,129,.14); color: #d9fff1; border-color: rgba(16,185,129,.35); }

    /* code cards */
    .code-card {
      border-radius: var(--radius); border: 1px solid rgba(148,163,184,.16);
      background: linear-gradient(180deg, #0b1220, #080d16);
      overflow: hidden; margin: .7em 0; box-shadow: var(--shadow-1);
    }
    .code-card-header {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      padding: 7px 10px; background: rgba(148,163,184,.07);
      border-bottom: 1px solid var(--border-subtle);
      font-family: var(--font-mono); font-size: .68rem; letter-spacing: .08em;
      text-transform: uppercase; color: var(--text-muted);
    }
    .code-card-body { padding: 12px 14px; overflow-x: auto; }
    .code-card-body pre, .code-card pre {
      margin: 0; background: transparent; border: none; padding: 0;
      font-family: var(--font-mono); font-size: .82rem; line-height: 1.62;
    }
    .code-card .code-status { text-transform: none; letter-spacing: 0; }
    .typing-cursor { color: var(--accent-2); animation: caret 1s steps(2) infinite; }
    @keyframes caret { 50% { opacity: 0; } }
    .typing-dots span { background: var(--accent-2); }

    /* ---------------- composer ---------------- */
    .input-wrapper { padding: 10px 16px 14px; }
    .input-wrapper::before {
      content: ""; position: absolute; left: 0; right: 0; top: -34px; height: 34px;
      background: linear-gradient(180deg, transparent, rgba(10,14,20,.9));
      pointer-events: none;
    }
    .composer {
      max-width: 860px; margin: 0 auto; padding: 10px 12px 8px;
      border-radius: var(--radius-lg);
      background: linear-gradient(180deg, rgba(22,30,42,.96), rgba(13,19,27,.97));
      border: 1px solid var(--border);
      box-shadow: var(--shadow-1);
      transition: border-color .2s ease, box-shadow .24s ease, transform .2s ease;
    }
    .composer:focus-within {
      border-color: rgba(16,185,129,.55);
      box-shadow: var(--shadow-glow);
    }
    .composer-row { display: flex; align-items: flex-end; gap: 8px; }
    .composer textarea {
      flex: 1; min-height: 26px; max-height: 220px; resize: none;
      background: transparent; border: none; outline: none; color: var(--text);
      font-family: inherit; font-size: .93rem; line-height: 1.6; padding: 6px 2px;
    }
    .composer textarea::placeholder { color: #6f8299; }
    .composer-tools {
      display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
      padding-top: 6px; margin-top: 4px; border-top: 1px solid var(--border-subtle);
    }
    .composer-tools .spacer { flex: 1; }
    .tool-btn {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 5px 9px; border-radius: 9px; font-size: .72rem; font-weight: 600;
      color: var(--text-muted); background: rgba(148,163,184,.07);
      border: 1px solid var(--border-subtle); cursor: pointer;
      transition: all .16s ease;
    }
    .tool-btn:hover { color: var(--text); background: rgba(148,163,184,.14); transform: translateY(-1px); }
    .tool-btn.active {
      color: #d9fff1; border-color: rgba(16,185,129,.45);
      background: rgba(16,185,129,.16); box-shadow: 0 6px 18px -14px rgba(16,185,129,.9);
    }
    .composer-hint {
      max-width: 860px; margin: 7px auto 0; display: flex; align-items: center;
      justify-content: space-between; gap: 10px; font-size: .68rem; color: #6f8299;
    }
    .composer-hint kbd {
      font-family: var(--font-mono); font-size: .64rem; padding: 1px 5px;
      border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 5px;
      background: rgba(148,163,184,.08); color: #b9c8d9;
    }
    .send-btn {
      width: 38px; height: 38px; border-radius: 12px; font-size: 1.05rem; font-weight: 800;
      background: var(--grad-brand); color: #04120c;
      box-shadow: 0 12px 28px -16px rgba(16,185,129,.95), 0 1px 0 rgba(255,255,255,.22) inset;
      transition: transform .16s ease, filter .16s ease, opacity .16s ease;
    }
    .send-btn:hover:not(:disabled) { transform: translateY(-1px) scale(1.03); filter: brightness(1.06); }
    .send-btn:active:not(:disabled) { transform: scale(.96); }
    .send-btn:disabled { opacity: .45; filter: grayscale(.4); cursor: not-allowed; }
    .send-btn.stopping { background: linear-gradient(135deg, #f87171, #ef4444); color: #1a0505; }

    .demo-banner {
      max-width: 860px; margin: 0 auto 8px; display: flex; align-items: center; gap: 9px;
      padding: 8px 12px; border-radius: 12px; font-size: .76rem; line-height: 1.45;
      color: #fde68a; background: rgba(251,191,36,.09);
      border: 1px solid rgba(251,191,36,.3);
    }
    .demo-banner strong { color: #fcd34d; }
    .demo-banner button {
      margin-left: auto; flex: none; font-size: .7rem; font-weight: 700;
      padding: 4px 10px; border-radius: 8px; cursor: pointer;
      background: rgba(251,191,36,.16); border: 1px solid rgba(251,191,36,.4); color: #fde68a;
    }
    .demo-banner button:hover { background: rgba(251,191,36,.26); }

    /* buttons elsewhere */
    .btn-subtle {
      border-radius: 10px; border: 1px solid var(--border); background: rgba(148,163,184,.06);
      color: #cfdae8; font-size: .76rem; font-weight: 600; padding: 7px 11px;
      transition: all .16s ease;
    }
    .btn-subtle:hover { background: rgba(148,163,184,.14); color: #fff; transform: translateY(-1px); }
    .btn-firehose {
      border-radius: 11px; border: 1px solid rgba(16,185,129,.42);
      background: linear-gradient(135deg, rgba(16,185,129,.95), rgba(13,148,136,.92));
      color: #04120c; font-weight: 800; letter-spacing: .2px;
      box-shadow: 0 14px 30px -20px rgba(16,185,129,.95);
      transition: transform .16s ease, filter .16s ease;
    }
    .btn-firehose:hover { transform: translateY(-1px); filter: brightness(1.06); }
    .lab-panel { border-radius: var(--radius); background: linear-gradient(180deg, rgba(148,163,184,.06), rgba(148,163,184,.02)); border: 1px solid var(--border-subtle); }
    input[type="text"], input[type="search"], textarea {
      font-family: inherit; color: var(--text);
    }
    .lab-container input[type="text"], .gallery-toolbar input[type="text"] {
      background: rgba(9,13,19,.85); border: 1px solid var(--border); border-radius: 10px;
      padding: 8px 11px; font-size: .82rem;
    }
    .lab-container input[type="text"]:focus, .gallery-toolbar input[type="text"]:focus {
      outline: none; border-color: rgba(34,211,238,.5); box-shadow: 0 0 0 3px rgba(34,211,238,.1);
    }

    /* ---------------- toasts + fab ---------------- */
    .toast-host {
      position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%);
      z-index: 400; display: flex; flex-direction: column; gap: 8px; align-items: center;
      pointer-events: none; width: min(92vw, 460px);
    }
    .toast {
      pointer-events: auto; width: 100%;
      display: flex; align-items: center; gap: 9px;
      padding: 10px 13px; border-radius: 12px; font-size: .8rem; font-weight: 500;
      background: rgba(16,23,34,.97); color: #e8f3ff;
      border: 1px solid var(--border); box-shadow: 0 22px 46px -26px rgba(0,0,0,.95);
      animation: toastIn .26s cubic-bezier(.2,.9,.3,1.2) both;
      backdrop-filter: blur(12px);
    }
    .toast.ok { border-color: rgba(16,185,129,.5); }
    .toast.err { border-color: rgba(248,113,113,.55); color: #ffe4e6; }
    .toast.info { border-color: rgba(34,211,238,.45); }
    .toast .ico { flex: none; font-size: .95rem; }
    @keyframes toastIn { from { opacity: 0; transform: translateY(12px) scale(.97); } to { opacity: 1; transform: none; } }
    .toast.leaving { animation: toastOut .2s ease forwards; }
    @keyframes toastOut { to { opacity: 0; transform: translateY(8px) scale(.98); } }

    .scroll-fab {
      position: absolute; right: 22px; bottom: 18px; z-index: 30;
      width: 40px; height: 40px; border-radius: 50%;
      display: none; place-items: center; cursor: pointer;
      color: #d9fff1; font-size: 1rem;
      background: rgba(16,23,34,.95); border: 1px solid rgba(16,185,129,.4);
      box-shadow: 0 18px 34px -20px rgba(0,0,0,.95);
      transition: transform .16s ease, background .16s ease;
    }
    .scroll-fab.show { display: grid; }
    .scroll-fab:hover { transform: translateY(-2px); background: rgba(16,185,129,.2); }

    .sidebar-overlay {
      position: fixed; inset: 0; z-index: 90; display: none;
      background: rgba(3,7,12,.6); backdrop-filter: blur(2px);
    }
    .sidebar-overlay.show { display: block; }

    @media (max-width: 820px) {
      .suggestion-grid { grid-template-columns: 1fr; max-width: 100%; }
      .chat-container { max-width: 100%; }
      .msg-bubble-user { max-width: 88%; }
      .msg-footer { opacity: .8; }
      .composer-hint .kbd-hint { display: none; }
      .demo-banner { font-size: .72rem; flex-wrap: wrap; }
      .scroll-fab { right: 14px; bottom: 12px; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
      .welcome-orb { animation: none; }
    }


    /* chat shell + misc polish */
    #tab-chat { position: relative; }
    .msg-badge {
      font-family: var(--font-mono); font-size: .67rem; letter-spacing: -.1px;
      color: var(--text-muted); line-height: 1.35;
    }
    .history-del-btn {
      opacity: 0; border-radius: 7px; transition: opacity .15s ease, background .15s ease, color .15s ease;
    }
    .history-item:hover .history-del-btn, .history-item.active .history-del-btn { opacity: 1; }
    .history-del-btn:hover { background: rgba(248,113,113,.18); color: #fca5a5; }
    .sub-metric { font-size: .7rem; color: var(--text-muted); }
    .card-row { align-items: baseline; }


    /* models panel */
    .models-modal {
      position: fixed; inset: 0; z-index: 500; display: none;
      align-items: center; justify-content: center; padding: 20px;
      background: rgba(3,7,12,.72); backdrop-filter: blur(6px);
    }
    .models-modal.open { display: flex; }
    .models-panel {
      width: min(760px, 100%); max-height: 86vh; display: flex; flex-direction: column;
      border-radius: 20px; overflow: hidden;
      background: linear-gradient(180deg, #101823, #0a0f17);
      border: 1px solid var(--border);
      box-shadow: 0 40px 90px -40px rgba(0,0,0,.95);
      animation: msgIn .22s ease both;
    }
    .models-head {
      display: flex; align-items: flex-start; gap: 12px; padding: 16px 18px 12px;
      border-bottom: 1px solid var(--border-subtle);
    }
    .models-head h2 { font-size: 1rem; margin: 0 0 3px; letter-spacing: -.2px; }
    .models-head p { margin: 0; font-size: .76rem; color: var(--text-muted); line-height: 1.5; }
    .models-head .icon-btn { margin-left: auto; }
    .models-list { padding: 12px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
    .model-row {
      display: flex; align-items: center; gap: 12px; padding: 11px 12px;
      border-radius: 12px; border: 1px solid var(--border-subtle);
      background: rgba(148,163,184,.05);
    }
    .model-row .m-name { font-size: .82rem; font-weight: 600; color: #e6eef8; word-break: break-all; }
    .model-row .m-meta { font-size: .7rem; color: var(--text-muted); margin-top: 2px; }
    .model-row .m-state { margin-left: auto; flex: none; display: flex; align-items: center; gap: 8px; }
    .state-pill {
      font-size: .66rem; font-weight: 700; padding: 3px 8px; border-radius: 99px;
      border: 1px solid var(--border); color: var(--text-muted); white-space: nowrap;
    }
    .state-pill.ready { color: #6ee7b7; border-color: rgba(16,185,129,.45); background: rgba(16,185,129,.12); }
    .state-pill.downloading { color: #7dd3fc; border-color: rgba(56,189,248,.45); background: rgba(56,189,248,.12); }
    .state-pill.missing { color: #fcd34d; border-color: rgba(251,191,36,.35); background: rgba(251,191,36,.1); }
    .model-row button {
      font-size: .7rem; font-weight: 700; padding: 5px 10px; border-radius: 8px; cursor: pointer;
      color: #d9fff1; background: rgba(16,185,129,.16); border: 1px solid rgba(16,185,129,.4);
    }
    .model-row button:hover { background: rgba(16,185,129,.26); }
    .model-row button:disabled { opacity: .45; cursor: not-allowed; }
    .models-empty { padding: 18px; text-align: center; color: var(--text-muted); font-size: .8rem; }
    .models-foot {
      display: flex; align-items: center; gap: 10px; justify-content: space-between;
      padding: 10px 18px; border-top: 1px solid var(--border-subtle);
      font-size: .72rem; color: var(--text-muted);
    }
    .models-foot code { font-family: var(--font-mono); color: #a5f3fc; font-size: .95em; }

    /* composer meta layout */
    .composer-tools select {
      background: rgba(9,13,19,.9); border: 1px solid var(--border); border-radius: 9px;
      padding: 4px 7px; font-size: .72rem; color: var(--text);
    }


    /* ==================================================================
       ARGON v3 — compact shell, sidebar tools, Arena-style sandbox
       ================================================================== */

    /* ---------- sidebar: compact controls ---------- */
    .sidebar .btn-new-chat,
    .btn-new-chat.compact {
      margin: 0 12px 10px;
      height: 32px;
      padding: 0 10px;
      font-size: .76rem;
      font-weight: 700;
      border-radius: 9px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      width: calc(100% - 24px);
    }
    .btn-new-chat.compact span { font-size: .76rem; }

    .history-item {
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 7px 6px 7px 10px;
    }
    .history-item-title {
      flex: 1;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: .8rem;
    }
    .history-actions {
      display: flex;
      gap: 1px;
      opacity: 0;
      transform: translateX(3px);
      transition: opacity .14s ease, transform .14s ease;
    }
    .history-item:hover .history-actions,
    .history-item.active .history-actions,
    .history-item:focus-within .history-actions { opacity: 1; transform: none; }
    .hist-act {
      width: 24px; height: 24px;
      display: grid; place-items: center;
      border: none; border-radius: 7px;
      background: transparent; color: var(--text-muted);
      font-size: .74rem; line-height: 1; cursor: pointer;
      transition: background .14s ease, color .14s ease;
    }
    .hist-act:hover { background: rgba(148,163,184,.16); color: #fff; }
    .hist-act.danger:hover { background: rgba(248,113,113,.18); color: #fca5a5; }
    .hist-act.ok:hover { background: rgba(16,185,129,.2); color: #6ee7b7; }
    @media (hover: none) {
      .history-actions { opacity: .85; transform: none; }
    }

    .archive-block { margin: 6px 10px 0; padding-top: 6px; border-top: 1px solid var(--border-subtle); }
    .archive-toggle {
      display: flex; align-items: center; gap: 6px; width: 100%;
      padding: 6px 8px; border: none; border-radius: 8px; background: transparent;
      color: var(--text-muted); font-size: .68rem; font-weight: 700;
      letter-spacing: .1em; text-transform: uppercase; cursor: pointer;
    }
    .archive-toggle:hover { background: rgba(148,163,184,.09); color: var(--text); }
    .archive-count {
      background: rgba(148,163,184,.18); border-radius: 99px;
      padding: 1px 6px; font-size: .62rem; letter-spacing: 0; color: #cfdae8;
    }
    .archive-list { display: none; flex-direction: column; gap: 2px; padding: 2px 0 4px; }
    .archive-list.open { display: flex; }
    .archive-list .history-item { opacity: .82; }
    .history-empty { padding: 8px 12px; font-size: .74rem; color: var(--text-muted); line-height: 1.5; }

    .tool-nav {
      display: flex; flex-direction: column; gap: 1px;
      margin: 8px 10px 0; padding-top: 8px; border-top: 1px solid var(--border-subtle);
    }
    .tool-nav .sidebar-section-title { padding: 0 8px 4px; }
    .tool-nav-btn {
      display: flex; align-items: center; gap: 9px;
      padding: 7px 9px; border-radius: 9px;
      border: 1px solid transparent; background: transparent;
      color: #c2d0e0; font-size: .79rem; font-weight: 600;
      font-family: inherit; text-align: left; cursor: pointer;
      min-height: 34px;
      transition: background .15s ease, color .15s ease, border-color .15s ease;
    }
    .tool-nav-btn .ico { width: 18px; text-align: center; font-size: .86rem; flex: none; }
    .tool-nav-btn:hover { background: rgba(148,163,184,.1); color: #fff; }
    .tool-nav-btn.active {
      background: rgba(16,185,129,.13);
      border-color: rgba(16,185,129,.34);
      color: #dcfff2;
    }
    .tool-nav-btn .kbd-mini {
      margin-left: auto; font-family: var(--font-mono); font-size: .6rem;
      color: var(--text-muted); opacity: .8;
    }

    .system-strip {
      margin: 8px 10px 0; padding: 9px 10px; cursor: pointer;
      border-radius: 11px; border: 1px solid var(--border-subtle);
      background: linear-gradient(180deg, rgba(148,163,184,.07), rgba(148,163,184,.03));
      display: flex; flex-direction: column; gap: 5px;
      transition: border-color .16s ease, background .16s ease;
    }
    .system-strip:hover { border-color: rgba(16,185,129,.3); background: rgba(16,185,129,.06); }
    .system-strip .ss-row {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      font-size: .66rem; color: var(--text-muted); letter-spacing: .02em;
    }
    .system-strip .ss-row strong { color: #dbe7f3; font-size: .7rem; font-variant-numeric: tabular-nums; }
    .system-strip .progress-bar { height: 4px; }
    .system-strip .engine { display: inline-flex; align-items: center; gap: 5px; }
    .system-strip .engine-dot {
      width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
      box-shadow: 0 0 0 3px rgba(16,185,129,.16);
    }
    .system-strip .engine-dot.demo { background: #fbbf24; box-shadow: 0 0 0 3px rgba(251,191,36,.16); }

    /* ---------- top bar: one clean row ---------- */
    .topbar {
      display: flex; align-items: center; gap: 8px; flex-wrap: nowrap;
      padding: 8px 12px;
    }
    .topbar .topbar-spacer { flex: 1; }
    .topbar .brand-mark {
      width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center;
      background: var(--grad-brand); color: #04120c; font-size: .72rem; font-weight: 900;
    }
    .model-pill { display: inline-flex; align-items: center; gap: 7px; padding: 4px 10px; font-size: .8rem; }
    .model-pill span.tag { background: rgba(16,185,129,.16); color: #8af5cd; border: 1px solid rgba(16,185,129,.32); }
    .chip-btn {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 6px 10px; border-radius: 9px; min-height: 32px;
      border: 1px solid var(--border-subtle); background: rgba(148,163,184,.07);
      color: #cfdae8; font-size: .74rem; font-weight: 600; font-family: inherit; cursor: pointer;
      transition: background .15s ease, border-color .15s ease, color .15s ease;
    }
    .chip-btn:hover { background: rgba(148,163,184,.15); color: #fff; border-color: var(--border); }
    .chip-btn.accent { border-color: rgba(16,185,129,.34); color: #8af5cd; }
    .chip-btn.on { border-color: rgba(16,185,129,.5); color: #6ee7b7; background: rgba(16,185,129,.12); }
    #offlineModeBtn { padding: 6px 9px; }

    /* ---------- composer: model picker lives here ---------- */
    .composer-tools { gap: 5px; }
    .composer-tools .model-select {
      max-width: 168px; min-height: 28px;
      font-size: .7rem; font-weight: 600;
      padding: 3px 6px; border-radius: 8px;
      background: rgba(9,13,19,.92);
      border: 1px solid var(--border-subtle);
      color: #cfdae8;
    }
    .composer-tools .model-select:hover { border-color: rgba(34,211,238,.4); }
    .composer-tools select { min-height: 28px; }
    .tool-btn { min-height: 30px; }
    @media (max-width: 560px) {
      .composer-tools .t-label { display: none; }
      .composer-tools .model-select { max-width: 116px; }
    }

    /* ---------- sandbox: editor + run panel like a hosted playground ---------- */
    .sandbox-shell {
      display: flex; flex-direction: column; gap: 10px;
      width: 100%; max-width: 1400px; margin: 0 auto;
      padding: 12px; height: 100%; min-height: 0;
    }
    .sandbox-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .sandbox-modes {
      display: inline-flex; gap: 2px; padding: 3px;
      background: rgba(148,163,184,.09); border: 1px solid var(--border-subtle);
      border-radius: 10px;
    }
    .sandbox-mode {
      padding: 5px 12px; border: none; border-radius: 8px; cursor: pointer;
      background: transparent; color: var(--text-muted);
      font-size: .75rem; font-weight: 700; font-family: inherit;
      transition: background .15s ease, color .15s ease;
    }
    .sandbox-mode:hover { color: var(--text); }
    .sandbox-mode.active {
      background: linear-gradient(135deg, rgba(16,185,129,.92), rgba(13,148,136,.88));
      color: #04120c;
      box-shadow: 0 6px 16px -12px rgba(16,185,129,.9);
    }
    .sandbox-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .sandbox-actions .btn-firehose { padding: 7px 14px; font-size: .78rem; }

    .sandbox-body {
      display: grid; grid-template-columns: 1.05fr 1fr; gap: 10px;
      flex: 1; min-height: 0;
    }
    .sandbox-pane {
      display: flex; flex-direction: column; min-height: 300px; overflow: hidden;
      border: 1px solid var(--border-subtle); border-radius: 14px;
      background: rgba(9,13,19,.62);
      box-shadow: var(--shadow-1);
    }
    .pane-head {
      display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
      padding: 6px 10px; min-height: 34px;
      background: rgba(148,163,184,.06);
      border-bottom: 1px solid var(--border-subtle);
      font-size: .68rem; font-weight: 700; letter-spacing: .08em;
      text-transform: uppercase; color: var(--text-muted);
    }
    .pane-head .file {
      font-family: var(--font-mono); font-size: .74rem; font-weight: 600;
      letter-spacing: 0; text-transform: none; color: #cfe3f5;
    }
    .pane-head .pane-meta { margin-left: auto; font-family: var(--font-mono); font-size: .64rem; letter-spacing: 0; }
    .editor-wrap { display: flex; flex: 1; min-height: 0; overflow: hidden; }
    #sandboxLineNums {
      margin: 0; padding: 10px 8px 10px 10px; text-align: right;
      color: #46586c; background: rgba(2,6,12,.45);
      border-right: 1px solid var(--border-subtle);
      font-family: var(--font-mono); font-size: .78rem; line-height: 1.62;
      user-select: none; overflow: hidden; min-width: 40px;
    }
    #sandboxEditor {
      flex: 1; min-width: 0; width: 100%;
      background: transparent; border: none; outline: none; resize: none;
      padding: 10px 12px; color: #e3ecf7;
      font-family: var(--font-mono); font-size: .8rem; line-height: 1.62;
      white-space: pre; overflow: auto; tab-size: 4;
    }
    #sandboxEditor::placeholder { color: #5b6d82; }
    .out-tabs { display: inline-flex; gap: 3px; margin-left: auto; }
    .out-tab {
      border: none; background: transparent; cursor: pointer;
      color: var(--text-muted); font-family: inherit;
      font-size: .66rem; font-weight: 700; letter-spacing: .04em;
      padding: 3px 8px; border-radius: 7px; text-transform: none;
    }
    .out-tab:hover { color: var(--text); }
    .out-tab.active { background: rgba(34,211,238,.15); color: #a5f3fc; }
    .run-status { font-family: var(--font-mono); font-size: .64rem; letter-spacing: 0; text-transform: none; }
    .run-status.idle { color: var(--text-muted); }
    .run-status.running { color: #7dd3fc; }
    .run-status.ok { color: #6ee7b7; }
    .run-status.err { color: #fca5a5; }
    #sandboxConsole {
      flex: 1; min-height: 120px; margin: 0; padding: 10px 12px; overflow: auto;
      background: transparent; border: none;
      font-family: var(--font-mono); font-size: .78rem; line-height: 1.6;
      color: #cfe3f5; white-space: pre-wrap; word-break: break-word;
    }
    #sandboxConsole .con-dim { color: #7b8ea3; }
    #sandboxConsole .con-ok { color: #6ee7b7; }
    #sandboxConsole .con-err { color: #fca5a5; }
    #sandboxFrame {
      flex: 1; width: 100%; min-height: 140px; border: none;
      background: #fff; display: none;
    }
    .term-row {
      display: flex; align-items: center; gap: 8px;
      padding: 7px 10px; border-top: 1px solid var(--border-subtle);
      background: rgba(2,6,12,.5);
    }
    .term-row .prompt {
      font-family: var(--font-mono); font-size: .74rem; color: #34d399; white-space: nowrap;
    }
    .term-row input {
      flex: 1; min-width: 0; background: transparent; border: none; outline: none;
      color: #e3ecf7; font-family: var(--font-mono); font-size: .78rem;
    }
    .sandbox-templates {
      display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
      padding-top: 2px;
    }
    .sandbox-templates .tpl-label {
      font-size: .66rem; text-transform: uppercase; letter-spacing: .1em;
      color: var(--text-muted); font-weight: 700; margin-right: 2px;
    }
    .tpl-chip {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 5px 9px; border-radius: 8px; cursor: pointer; min-height: 28px;
      border: 1px solid var(--border-subtle); background: rgba(148,163,184,.07);
      color: #c7d5e4; font-family: inherit; font-size: .72rem; font-weight: 600;
      transition: background .15s ease, border-color .15s ease, transform .15s ease;
    }
    .tpl-chip:hover { background: rgba(148,163,184,.15); border-color: var(--border); transform: translateY(-1px); }

    /* ---------- share modal ---------- */
    .share-body { padding: 12px 18px; display: flex; }
    #shareText {
      width: 100%; min-height: 220px; max-height: 46vh; resize: vertical;
      background: rgba(2,6,12,.65); color: #cfe3f5;
      border: 1px solid var(--border-subtle); border-radius: 10px;
      padding: 10px 12px; font-family: var(--font-mono); font-size: .74rem; line-height: 1.55;
    }
    .models-foot .foot-actions { display: flex; gap: 6px; flex-wrap: wrap; }

    /* ---------- v3.1 bits ---------- */
    .sidebar-header {
      display: flex; align-items: center; justify-content: space-between;
      gap: 8px; padding: 12px 12px 6px;
    }
    .sidebar-brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .sidebar-brand .orb { font-size: 1rem; color: #34d399; }
    .sidebar-brand .brand-text {
      display: flex; flex-direction: column; min-width: 0;
      font-size: .78rem; font-weight: 800; letter-spacing: .1em; color: #e6f1ff;
    }
    .sidebar-brand .brand-text small {
      font-size: .58rem; font-weight: 600; letter-spacing: .14em;
      text-transform: uppercase; color: #64798f;
    }
    .sidebar-section-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px 4px; }
    .sidebar-section-title {
      font-size: .62rem; font-weight: 800; letter-spacing: .16em;
      text-transform: uppercase; color: #64798f;
    }
    .sidebar-foot {
      display: flex; align-items: center; gap: 6px;
      padding: 8px 12px 10px; margin-top: auto;
      font-size: .6rem; color: #5c7085; border-top: 1px solid var(--border-subtle);
    }
    .sidebar-foot .dot {
      width: 6px; height: 6px; border-radius: 50%; background: #22c55e;
      box-shadow: 0 0 0 3px rgba(34,197,94,.14); flex: none;
    }
    .history-item.archived { opacity: .78; }
    .tpl-hint {
      font-size: .62rem; font-weight: 800; letter-spacing: .14em;
      text-transform: uppercase; color: #64798f; margin-right: 2px;
    }
    .sandbox-templates { overflow-x: auto; scrollbar-width: thin; }
    #tab-sandbox { overflow-y: auto; }
    #tab-sandbox .sandbox-shell { min-height: min(720px, calc(100vh - 140px)); }
    @media (max-width: 820px) {
      .sandbox-shell { height: auto; }
      .sandbox-body { grid-template-columns: 1fr; }
      .sandbox-pane { min-height: 240px; }
      #sandboxEditor, #sandboxLineNums { font-size: .74rem; }
    }

    /* ---------- v3.2 sidebar layout guard ----------
       The sidebar is a column flex container. Base-theme rules (`.btn-new-chat`,
       `.history-list` both `flex: 1`) made every section fight for height, so the
       tool buttons kept their min-height and painted over the New chat row and the
       chat delete buttons. Only the chat list may grow; everything else is fixed. */
    .sidebar { overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; }
    .sidebar > * { flex: 0 0 auto; }
    .sidebar > .history-list { flex: 1 1 auto; min-height: 54px; max-height: 38vh; }
    .sidebar > .tool-nav,
    .sidebar > .system-strip,
    .sidebar > .archive-block,
    .sidebar > .sidebar-foot { position: relative; z-index: 1; }

    .sidebar .btn-new-chat.compact {
      flex: 0 0 auto; width: 100%; height: 32px;
      margin: 0; align-self: stretch; justify-content: center;
    }
    .sidebar .sidebar-search { margin: 0; }
    .sidebar .sidebar-section-header { padding: 0 2px; }
    .sidebar .archive-block { margin: 0; }
    .sidebar .tool-nav { margin: 0; padding-top: 8px; gap: 2px; }
    .sidebar .tool-nav .sidebar-section-title { padding: 0 8px 2px; }
    .sidebar .tool-nav-btn {
      flex: 0 0 auto; min-height: 30px; padding: 5px 8px;
      font-size: .775rem; gap: 8px;
    }
    .sidebar .tool-nav-btn .label {
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .sidebar .system-strip { margin: 0; }
    .sidebar .sidebar-foot { padding: 8px 2px 0; margin-top: 0; }
    .sidebar .history-item { position: relative; flex: 0 0 auto; }
    .sidebar .history-actions { flex: 0 0 auto; }

    @media (max-height: 720px) {
      .sidebar { gap: 7px; padding: 10px; }
      .sidebar .tool-nav-btn { min-height: 27px; padding: 4px 8px; font-size: .74rem; }
      .sidebar .tool-nav .sidebar-section-title { display: none; }
      .sidebar > .history-list { max-height: 26vh; }
      .sidebar .system-strip { padding: 7px 9px; gap: 4px; }
    }

    /* ---------- sidebar tool list (ChatGPT-style nav) ---------- */
    .side-tools { display: flex; flex-direction: column; gap: 1px; padding: 0 6px; flex: none; }
    .side-tool {
      display: flex; align-items: center; gap: 10px; width: 100%;
      padding: 7px 9px; border-radius: 9px; cursor: pointer; text-align: left;
      background: transparent; border: 1px solid transparent;
      color: #c2d1df; font-family: inherit; font-size: .8rem; font-weight: 600;
      transition: background .15s ease, color .15s ease, border-color .15s ease;
    }
    .side-tool:hover { background: rgba(148,163,184,.12); color: #fff; }
    .side-tool.active {
      background: rgba(16,185,129,.15); border-color: rgba(16,185,129,.38); color: #d8fff3; font-weight: 800;
    }
    .side-tool .st-icon { font-size: .92rem; line-height: 1; width: 16px; text-align: center; }

    /* ---------- sidebar bottom actions ---------- */
    .sidebar-actions {
      display: flex; flex-direction: column; gap: 2px;
      margin: 6px 6px 0; padding-top: 6px; border-top: 1px solid var(--border-subtle);
    }
    .sidebar-action {
      display: flex; align-items: center; gap: 9px; width: 100%;
      padding: 8px 9px; border-radius: 9px; cursor: pointer;
      background: transparent; border: 1px solid transparent;
      color: #c3d2e0; font-family: inherit; font-size: .8rem; font-weight: 700;
      text-align: left; transition: background .15s ease, color .15s ease;
    }
    .sidebar-action:hover { background: rgba(148,163,184,.12); color: #fff; }
    .sidebar-action .sa-icon { font-size: .95rem; line-height: 1; }
    .sidebar-action .sa-hint {
      margin-left: auto; font-size: .62rem; font-weight: 600; color: #7b8b9c;
      font-family: var(--font-mono);
    }

    /* ---------- settings ---------- */
    .settings-panel { max-width: 860px; }
    .settings-layout { display: grid; grid-template-columns: 168px 1fr; gap: 14px; min-height: 0; flex: 1; overflow: hidden; }
    .settings-nav { display: flex; flex-direction: column; gap: 4px; }
    .settings-tab {
      text-align: left; padding: 8px 10px; border-radius: 9px; cursor: pointer;
      background: transparent; border: 1px solid transparent; color: #a7b6c6;
      font-family: inherit; font-size: .78rem; font-weight: 700;
    }
    .settings-tab:hover { background: rgba(148,163,184,.1); color: #fff; }
    .settings-tab.active { background: rgba(16,185,129,.16); border-color: rgba(16,185,129,.4); color: #d5fff2; }
    .settings-body { min-width: 0; overflow-y: auto; padding-right: 4px; }
    .settings-pane { display: none; }
    .settings-pane.active { display: block; }
    .set-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; margin-bottom: 12px; }
    .set-card {
      display: flex; flex-direction: column; gap: 3px; padding: 9px 11px;
      background: rgba(148,163,184,.07); border: 1px solid var(--border-subtle); border-radius: 10px;
    }
    .set-card-label { font-size: .64rem; letter-spacing: .08em; text-transform: uppercase; color: #8fa0b3; font-weight: 800; }
    .set-card-value { font-size: 1.02rem; font-weight: 800; color: #e8f6ff; font-variant-numeric: tabular-nums; }
    .set-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 10px 0; }
    .set-note { font-size: .72rem; color: #8b9aab; }
    .set-checks { display: flex; flex-direction: column; gap: 3px; }
    .set-check {
      display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 7px;
      background: rgba(148,163,184,.05); font-size: .74rem; color: #c6d3e1;
    }
    .set-check .mark { width: 16px; text-align: center; font-weight: 900; }
    .set-check.pass .mark { color: #34d399; }
    .set-check.fail .mark { color: #f87171; }
    .set-check .prompt { color: #7f8fa1; font-family: var(--font-mono); font-size: .66rem; margin-left: auto; text-align: right; }
    .set-rows { display: flex; flex-direction: column; gap: 6px; }
    .set-row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 9px; border-radius: 8px; background: rgba(148,163,184,.05); font-size: .76rem; }
    .set-row span:first-child { color: #93a3b5; }
    .set-row span:last-child { color: #e6f1fa; font-weight: 700; text-align: right; font-family: var(--font-mono); font-size: .72rem; word-break: break-all; }
    @media (max-width: 700px) {
      .settings-layout { grid-template-columns: 1fr; }
      .settings-nav { flex-direction: row; overflow-x: auto; }
      .settings-tab { white-space: nowrap; }
    }

    /* ---------- agent pane (IDE) ---------- */
    .agent-pane {
      flex: 1; display: flex; flex-direction: column; gap: 7px;
      padding: 8px; overflow: hidden; background: #101418;
    }
    .agent-modes {
      display: flex; gap: 2px; padding: 3px; flex: none;
      background: #161c22; border: 1px solid #2d2d2d; border-radius: 9px;
    }
    .agent-mode {
      flex: 1; padding: 5px 6px; border: none; border-radius: 7px; cursor: pointer;
      background: transparent; color: #9aa8b8; font-family: inherit;
      font-size: .69rem; font-weight: 700; white-space: nowrap;
      transition: background .15s ease, color .15s ease;
    }
    .agent-mode:hover { color: #fff; }
    .agent-mode.active {
      background: linear-gradient(135deg, rgba(16,185,129,.9), rgba(13,148,136,.86));
      color: #04120c;
    }
    .agent-chips { display: flex; flex-wrap: wrap; gap: 4px; flex: none; }
    .agent-chip {
      padding: 3px 7px; border-radius: 7px; cursor: pointer; min-height: 24px;
      border: 1px solid #333c46; background: #171e25; color: #c5d3e2;
      font-family: inherit; font-size: .67rem; font-weight: 600;
    }
    .agent-chip:hover { border-color: #4a5a69; color: #fff; }
    .agent-transcript {
      flex: 1; min-height: 0; overflow-y: auto;
      background: #0b0f13; border: 1px solid #2d2d2d; border-radius: 9px;
      padding: 8px; font-size: .76rem; color: #dbe6f0;
    }
    .agent-empty { color: #7d8b9b; line-height: 1.55; font-size: .73rem; }
    .agent-plan {
      font-size: .68rem; color: #8fa2b5; padding: 4px 0 6px;
      border-bottom: 1px dashed #263038; margin-bottom: 6px;
    }
    .agent-step { margin-bottom: 9px; }
    .agent-step-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .agent-step-title { font-weight: 700; color: #e7f0f8; }
    .agent-step-icon { opacity: .9; }
    .agent-status {
      margin-left: auto; font-size: .6rem; font-weight: 800; letter-spacing: .06em;
      text-transform: uppercase; padding: 1px 6px; border-radius: 99px;
      background: rgba(148,163,184,.16); color: #9fb0c2;
    }
    .agent-status.applied { background: rgba(16,185,129,.18); color: #6ee7b7; }
    .agent-status.staged { background: rgba(251,191,36,.18); color: #fcd34d; }
    .agent-step-path {
      font-family: var(--font-mono); font-size: .64rem; color: #6f8298;
      margin: 2px 0 4px; word-break: break-all;
    }
    .agent-step-body { line-height: 1.55; color: #cbd7e3; }
    .agent-step-body h3 { font-size: .82rem; margin: 2px 0 6px; color: #eaf4ff; }
    .agent-step-body ul { margin: 4px 0 4px 16px; }
    .agent-step-body li { margin: 2px 0; }
    .agent-step-body code { background: rgba(148,163,184,.14); padding: 1px 4px; border-radius: 4px; font-size: .92em; }
    .agent-step-body pre { background: #0f151b; border: 1px solid #263038; border-radius: 7px; padding: 6px 8px; overflow-x: auto; }
    .agent-step.error .agent-step-title { color: #fca5a5; }
    .agent-diff {
      margin: 5px 0 0; padding: 7px 9px; max-height: 190px; overflow: auto;
      background: #0d1216; border: 1px solid #263038; border-radius: 7px;
      font-family: var(--font-mono); font-size: .68rem; line-height: 1.45;
      color: #c3d3e2; white-space: pre;
    }
    .agent-review {
      flex: none; display: none; flex-direction: column; gap: 6px;
      border: 1px solid rgba(251,191,36,.4); border-radius: 9px;
      background: rgba(251,191,36,.07); padding: 7px 8px;
    }
    .agent-review-head { font-size: .68rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: #fcd34d; }
    .agent-review-head span { font-weight: 600; letter-spacing: 0; text-transform: none; color: #c9b483; }
    .agent-review-actions { display: flex; gap: 6px; }
    .agent-apply, .agent-discard {
      flex: 1; padding: 6px 8px; border-radius: 7px; cursor: pointer;
      font-family: inherit; font-size: .72rem; font-weight: 800; border: 1px solid transparent;
    }
    .agent-apply { background: #065f46; color: #ecfdf5; border-color: #10b981; }
    .agent-discard { background: #1b2229; color: #cbd5e1; border-color: #374151; }
    .agent-apply:hover { filter: brightness(1.12); }
    .agent-discard:hover { border-color: #6b7280; }
    .agent-compose { display: flex; gap: 6px; flex: none; align-items: stretch; }
    .agent-compose textarea {
      flex: 1; resize: none; min-height: 40px; max-height: 96px;
      background: #0f151b; color: #e6f0fa; border: 1px solid #2d3945; border-radius: 8px;
      padding: 7px 9px; font-family: inherit; font-size: .76rem; line-height: 1.45;
    }
    .agent-compose textarea:focus { outline: none; border-color: rgba(16,185,129,.6); }
    .agent-send {
      width: 42px; border-radius: 8px; cursor: pointer;
      background: linear-gradient(135deg, #10b981, #0d9488); color: #04120c;
      border: none; font-size: 1rem; font-weight: 900;
    }
    .agent-send:disabled { opacity: .5; cursor: progress; }

    @media (max-width: 900px) {
      .agent-mode { font-size: .64rem; padding: 5px 3px; }
      .agent-pane { padding: 6px; }
    }

    /* ---------- mobile ---------- */
    @media (max-width: 900px) {
      .sandbox-body { grid-template-columns: 1fr; }
      .sandbox-pane { min-height: 240px; }
      .sandbox-shell { padding: 10px; }
    }
    @media (max-width: 820px) {
      .topbar { padding: 7px 10px; gap: 6px; }
      .topbar .brand-name { display: none; }
      .chip-btn { padding: 6px 8px; font-size: .72rem; }
      .chip-btn .t-label { display: none; }
      .topbar .brand-name { display: none; }
      .sidebar { width: min(86vw, 320px); }
      .chat-scroll { padding: 12px 10px 6px; }
      .input-wrapper { padding: 8px 10px max(12px, env(safe-area-inset-bottom)); }
      .composer { padding: 9px 10px 7px; border-radius: 16px; }
      .composer textarea { font-size: .9rem; }
      .composer-hint { font-size: .64rem; }
      .suggestion-grid { grid-template-columns: 1fr; }
      .tool-nav-btn { min-height: 38px; }
      .btn-new-chat.compact { height: 34px; }
      .models-panel { max-height: 92vh; }
      .models-head { flex-wrap: wrap; }
      .models-foot { flex-wrap: wrap; gap: 8px; }
    }
    @media (max-width: 480px) {
      .top-chip { display: none !important; }
      .model-pill span:not(.brand-mark) { font-size: .74rem; }
      .send-btn { width: 36px; height: 36px; }
      .pane-head .pane-meta { display: none; }
    }
  </style>
</head>
<body>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-brand">
        <span class="orb">✦</span>
        <span class="brand-text">ARGON-OMEGA<small>offline studio</small></span>
      </div>
      <button class="icon-btn" onclick="toggleSidebar()" title="Close sidebar (Ctrl+B)">☰</button>
    </div>

    <button class="btn-new-chat compact" onclick="newChat()" title="New chat (Ctrl+Shift+O)">
      <span aria-hidden="true">＋</span><span>New chat</span>
    </button>

    <label class="sidebar-search" for="chatSearchInput">
      <span aria-hidden="true">🔎</span>
      <input id="chatSearchInput" type="search" placeholder="Search chats…" autocomplete="off" oninput="filterHistory(this.value)" />
    </label>

    <div class="sidebar-section-header">
      <span class="sidebar-section-title">Tools</span>
    </div>
    <nav class="side-tools" aria-label="Tools">
      <button class="side-tool active" data-tab="chat" onclick="switchTab('chat')">
        <span class="st-icon" aria-hidden="true">💬</span><span>Chat</span>
      </button>
      <button class="side-tool" data-tab="ide" onclick="switchTab('ide'); ideInit();">
        <span class="st-icon" aria-hidden="true">⌨️</span><span>Code IDE</span>
      </button>
      <button class="side-tool" data-tab="gallery" onclick="switchTab('gallery'); loadGallery();">
        <span class="st-icon" aria-hidden="true">🎨</span><span>Gallery</span>
      </button>
      <button class="side-tool" data-tab="editor" onclick="switchTab('editor'); loadEditorPicker();">
        <span class="st-icon" aria-hidden="true">🖌️</span><span>Image editor</span>
      </button>
      <button class="side-tool" data-tab="security" onclick="switchTab('security')">
        <span class="st-icon" aria-hidden="true">🛡️</span><span>Security</span>
      </button>
      <button class="side-tool" data-tab="benchmark" onclick="switchTab('benchmark'); loadBenchmarkModels();">
        <span class="st-icon" aria-hidden="true">⚡</span><span>Benchmark</span>
      </button>
      <button class="side-tool" data-tab="video" onclick="switchTab('video'); loadVideoStudio();">
        <span class="st-icon" aria-hidden="true">🎬</span><span>Video studio</span>
      </button>
    </nav>

    <div class="sidebar-section-header">
      <span class="sidebar-section-title">Recent chats</span>
    </div>
    <div class="history-list" id="historyList"></div>

    <div class="archive-block" id="archiveBlock" style="display:none;">
      <button class="archive-toggle" onclick="toggleArchiveList()" aria-expanded="false" id="archiveToggle">
        <span aria-hidden="true">📦</span><span>Archived</span>
        <span class="archive-count" id="archiveCount">0</span>
        <span style="margin-left:auto;" id="archiveCaret">▾</span>
      </button>
      <div class="archive-list" id="archiveList"></div>
    </div>


    <div class="sidebar-actions">
      <button class="sidebar-action" onclick="openSettings()" title="Settings &amp; benchmark (Ctrl+,)">
        <span class="sa-icon" aria-hidden="true">⚙️</span><span>Settings</span>
        <span class="sa-hint">Ctrl+,</span>
      </button>
      <button class="sidebar-action" onclick="openModels()" title="Manage local models">
        <span class="sa-icon" aria-hidden="true">🧠</span><span>Models</span>
        <span class="sa-hint">local</span>
      </button>
    </div>

    <div class="system-strip" onclick="openModels()" title="Manage models &amp; storage">
      <div class="ss-row"><span>SSD ROM</span><strong id="romSize">0.00 MB</strong></div>
      <div class="progress-bar"><div class="progress-fill" id="romProgress"></div></div>
      <div class="ss-row">
        <span class="engine"><span class="engine-dot" id="engineDot"></span><span id="engineState">checking…</span></span>
        <span id="totalRows">Brains: 0</span>
      </div>
      <div class="ss-row"><span id="engineDetail" style="font-size:.62rem;">—</span></div>
    </div>

    <div class="sidebar-foot">
      <span class="dot"></span>
      <span id="sidebarFootText">100% offline · nothing leaves this machine</span>
    </div>
  </aside>

  <main class="main">
    <header class="topbar">
      <button class="icon-btn" id="openSidebarBtn" onclick="toggleSidebar()" title="Open sidebar (Ctrl+B)" style="display:none;">☰</button>
      <div class="model-pill" onclick="switchTab('chat')" title="Back to chat">
        <span class="brand-mark" aria-hidden="true">✦</span>
        <span class="brand-name">ARGON-OMEGA</span>
        <span class="tag">OFFLINE</span>
      </div>
      <span class="top-chip" id="demoChip" style="display:none;" title="No GGUF installed — replies are simulated locally">🧪 Demo</span>
      <span class="topbar-spacer"></span>
      <button class="chip-btn" onclick="openSettings()" title="Settings &amp; benchmark (Ctrl+,)">⚙️ <span class="t-label">Settings</span></button>
      <button class="chip-btn accent" onclick="openModels()" title="Models &amp; storage">🧠 <span class="t-label">Models</span></button>
      <button class="chip-btn" id="offlineModeBtn" onclick="toggleOfflineMode()" title="Offline mode">✈️ <span class="t-label">Offline</span></button>
    </header>

    <!-- CHAT TAB -->
    <div id="tab-chat" class="tab-view active">
      <div class="chat-scroll" id="chatScroll">
        <div class="chat-container" id="chatBox">
          <div class="welcome-screen" id="welcomeScreen">
            <div class="welcome-orb">✦</div>
            <div class="welcome-title">What can I help with?</div>
            <div class="welcome-sub">100% offline — no account, no cloud. Runs on local compute, a curated knowledge base and 44 code recipes until you install weights.</div>
            <div class="welcome-badge" id="welcomeBadge">⚡ Local engine ready</div>
            <div class="suggestion-grid">
              <button class="suggestion-chip" data-icon="🧠" onclick="useSuggestion('Explain how mmap works in llama-server')">
                <span><strong>Explain mmap</strong>
                <span>How local model memory mapping works</span></span>
              </button>
              <button class="suggestion-chip" data-icon="🐍" onclick="useSuggestion('Write a Python script to scan a directory for large files')">
                <span><strong>Write a Python script</strong>
                <span>Scan a directory for large files</span></span>
              </button>
              <button class="suggestion-chip" data-icon="💾" onclick="useSuggestion('Remember that my favorite language is Python')">
                <span><strong>Save to memory</strong>
                <span>Store a personal fact in SSD ROM</span></span>
              </button>
              <button class="suggestion-chip" data-icon="🛡️" onclick="useSuggestion('What are common SQL injection vulnerabilities and how to fix them?')">
                <span><strong>Security advice</strong>
                <span>SQL injection patterns &amp; fixes</span></span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <button type="button" id="scrollFab" class="scroll-fab" onclick="jumpToBottom()" title="Scroll to latest message">↓</button>

      <div class="input-wrapper">
        <form class="composer" id="composer" onsubmit="sendMessage(event)">
          <div class="composer-row">
            <textarea id="promptInput" rows="1" placeholder="Message ARGON-OMEGA — ask for code, an explanation, or say “remember that …”" autocomplete="off"></textarea>
            <button type="submit" id="sendBtn" class="send-btn" title="Send (Enter)">↑</button>
          </div>
          <div class="composer-tools">
            <button type="button" id="imageModeBtn" class="tool-btn" onclick="toggleImageMode()" title="Toggle image generation mode">🎨 Image</button>
            <button type="button" id="quantumToggleBtn" class="tool-btn active" onclick="toggleQuantumMode()" title="Quantum reasoning enabled">⚛️ Quantum ON</button>
            <span class="spacer"></span>
            <select id="brainSelect" class="model-select" onchange="selectBrain()" title="Model / brain used for replies">
              <option value="auto">⚡ Auto brain</option>
              <option value="qwen2.5-coder-7b-instruct-q4_k_m.gguf">💻 Qwen Coder 7B</option>
              <option value="qwen2.5-coder-32b-instruct-IQ3_XS.gguf">🏗️ Qwen Coder 32B</option>
              <option value="Phi-4-mini-instruct-Q4_K_M.gguf">🧮 Phi-4 mini</option>
              <option value="phi-4-Q4_K_M.gguf">🧠 Phi-4 14B</option>
              <option value="gemma-3-4b-it-Q4_K_M.gguf">🌐 Gemma 3 4B</option>
              <option value="gemma-3-1b-it-Q4_K_M.gguf">⚡ Gemma 3 1B</option>
            </select>
            <select id="thinkingLevelSelect" title="Thinking level: Low = fast, Mid = balanced, High = deep">
              <option value="low">⚡ Low</option>
              <option value="mid" selected>⚖️ Mid</option>
              <option value="high">🧠 High</option>
            </select>
            <span id="charCount" style="font-size:.68rem;color:#6f8299;font-variant-numeric:tabular-nums;">0</span>
          </div>
        </form>
        <div class="composer-hint">
          <span class="kbd-hint"><kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> newline · <kbd>Ctrl</kbd>+<kbd>K</kbd> focus · <kbd>Esc</kbd> stop</span>
          <span id="composerStatus">100% offline · local SSD ROM</span>
        </div>
      </div>
    </div>

    <!-- SSD IMAGE GALLERY TAB -->
    <div id="tab-gallery" class="tab-view">
      <div class="gallery-container">
        <div class="gallery-toolbar" style="flex-wrap:wrap;gap:8px;">
          <input id="galleryPrompt" type="text" placeholder="Describe the brand-new image you want to generate..." style="flex:1;min-width:220px;" />
          <select id="galleryStyle" style="max-width:180px;">
            <option value="Realistic Photo">Realistic Photo</option>
            <option value="Cyberpunk Neon">Cyberpunk Neon</option>
            <option value="Cinematic 4K">Cinematic 4K</option>
            <option value="Anime Art">Anime Art</option>
            <option value="3D Unreal Render">3D Unreal Render</option>
            <option value="Digital Oil Painting">Digital Oil Painting</option>
          </select>
          <button class="btn-firehose" id="genStudioBtn" style="width:auto;" onclick="generateBrandNewImage()">⚡ Generate Brand-New Image</button>
        </div>
        <div id="fluxRomBanner" style="margin-bottom:10px;padding:8px 12px;border-radius:8px;background:rgba(15,23,42,0.75);border:1px solid var(--border);font-size:0.78rem;color:#94a3b8;">Checking FLUX.1-schnell Pure ROM SSD status...</div>
        <div id="studioResult" style="margin-bottom:12px;"></div>
        <div id="galleryGrid" class="gallery-grid"></div>
      </div>
    </div>

    <!-- IMAGE EDITOR TAB -->
    <div id="tab-editor" class="tab-view">
      <div class="editor-container">
        <div class="editor-bar">
          <input type="file" id="editorUpload" accept="image/*" onchange="uploadEditorImage(this.files[0])" style="max-width:220px;" />
          <select id="editorPicker" onchange="selectEditorImage(this.value)" style="max-width:260px;">
            <option value="">-- Pick from SSD Gallery --</option>
          </select>
          <button class="btn-subtle" onclick="undoEdit()">↩ Undo</button>
          <button class="btn-subtle" onclick="redoEdit()">↪ Redo</button>
          <button class="btn-subtle" onclick="useEditedAsSource()">🔁 Edit Result Again</button>
          <a id="editorDownloadBtn" class="btn-subtle" href="#" download style="text-decoration:none;display:none;">⬇ Download</a>
        </div>

        <div class="editor-bar">
          <input id="editorCommand" type="text" placeholder="Type edit command (e.g. 'change background to a sunset beach', 'make it anime', 'upscale to 4K')..." style="flex:1;" />
          <button class="btn-firehose" style="width:auto;" onclick="runImageEdit()">✨ Apply AI Edit</button>
        </div>

        <div class="quick-edit-row">
          <button class="btn-subtle" onclick="runImageEdit('make it black and white')">B&amp;W</button>
          <button class="btn-subtle" onclick="runImageEdit('sepia filter')">Sepia</button>
          <button class="btn-subtle" onclick="runImageEdit('add neon glow filter')">Neon Glow</button>
          <button class="btn-subtle" onclick="runImageEdit('blur')">Blur</button>
          <button class="btn-subtle" onclick="runImageEdit('sharpen')">Sharpen</button>
          <button class="btn-subtle" onclick="runImageEdit('crop to square')">Crop Square</button>
          <button class="btn-subtle" onclick="runImageEdit('rotate 90')">Rotate 90°</button>
          <button class="btn-subtle" onclick="runImageEdit('mirror')">Mirror</button>
          <button class="btn-subtle" onclick="runImageEdit(&quot;add text 'ARGON AI' in neon blue at the bottom&quot;)">Add Text</button>
          <button class="btn-subtle" onclick="runImageEdit('upscale to 4K')">Upscale 4x</button>
        </div>

        <div id="editorStatus" class="sub-metric">Upload an image or pick one from ssd_images/ to start editing.</div>

        <div class="split-wrap" id="splitWrap" style="display:none;">
          <img id="editorBeforeImg" src="" alt="Original" />
          <img id="editorAfterImg" class="split-after" src="" alt="Edited" />
        </div>
        <input id="splitSlider" class="split-slider" type="range" min="0" max="100" value="50" oninput="updateSplit(this.value)" style="display:none;" />
      </div>
    </div>

    <!-- SANDBOX & TERMINAL STUDIO TAB -->
    <div id="tab-sandbox" class="tab-view">
      <div class="sandbox-shell">
        <div class="sandbox-head">
          <div class="sandbox-modes" role="tablist" aria-label="Sandbox mode">
            <button class="sandbox-mode active" id="sandboxModeCode" onclick="sandboxSetMode('code')" role="tab" aria-selected="true">🧪 Code</button>
            <button class="sandbox-mode" id="sandboxModeTerm" onclick="sandboxSetMode('terminal')" role="tab" aria-selected="false">💻 Terminal</button>
          </div>
          <div class="sandbox-actions">
            <button class="tpl-chip" onclick="loadSandboxTemplate('python')" title="Load a Python starter">⚡ Python</button>
            <button class="tpl-chip" onclick="loadSandboxTemplate('snake')" title="Load a playable game">🐍 Snake</button>
            <button class="tpl-chip" onclick="loadSandboxTemplate('terminal')" title="Switch to system terminal">💻 Shell</button>
            <button class="tpl-chip" onclick="clearSandbox()" title="Clear editor &amp; output">🧹 Clear</button>
            <button class="btn-firehose" id="sandboxRunBtn" onclick="runSandbox()" title="Run (Ctrl+Enter)">▶ Run <span class="kbd-hint" style="font-size:.66rem;opacity:.7;">⌘↵</span></button>
          </div>
        </div>

        <div class="sandbox-body">
          <section class="sandbox-pane">
            <div class="pane-head">
              <span class="file" id="sandboxFileName">main.py</span>
              <span class="pane-meta" id="sandboxEditorMeta">python · utf-8</span>
            </div>
            <div class="editor-wrap">
              <pre id="sandboxLineNums">1</pre>
              <textarea id="sandboxEditor" spellcheck="false" autocomplete="off" autocapitalize="off"
                oninput="sandboxUpdateLineNums()" onscroll="document.getElementById('sandboxLineNums').scrollTop=this.scrollTop;"
                placeholder="# Python, HTML5/JS or a shell command — then Run (Ctrl+Enter)"></textarea>
            </div>
          </section>

          <section class="sandbox-pane">
            <div class="pane-head">
              <span class="file">Output</span>
              <span class="run-status idle" id="sandboxStatus">idle</span>
              <div class="out-tabs">
                <button class="out-tab active" id="outTabConsole" onclick="sandboxSetOutput('console')">Console</button>
                <button class="out-tab" id="outTabPreview" onclick="sandboxSetOutput('preview')">Preview</button>
              </div>
            </div>
            <pre id="sandboxConsole">Ready. Write code above and press Run — Python runs sandboxed, HTML renders live in Preview.</pre>
            <iframe id="sandboxFrame" sandbox="allow-scripts" title="Live preview"></iframe>
            <div class="term-row">
              <span class="prompt" id="terminalPromptLabel">$</span>
              <input id="terminalCmdInput" type="text" autocomplete="off" spellcheck="false"
                placeholder="Run a read-only shell command…"
                onkeydown="handleTerminalKey(event)" />
            </div>
          </section>
        </div>

        <div class="sandbox-templates" id="sandboxQuickCmds"></div>
      </div>
    </div>

    <!-- VS CODE + ANTIGRAVITY IDE TAB -->
    <div id="tab-ide" class="tab-view" style="height:100%;overflow:hidden;">
      <div style="display:grid;grid-template-columns:230px 1fr 310px;grid-template-rows:1fr 210px;height:100%;width:100%;background:#1e1e1e;color:#d4d4d4;">
        <!-- Left: Full-Device File Explorer -->
        <div style="grid-row:1/3;background:#181818;border-right:1px solid #2d2d2d;display:flex;flex-direction:column;overflow:hidden;">
          <div style="padding:8px 10px;border-bottom:1px solid #2d2d2d;display:flex;justify-content:space-between;align-items:center;font-size:0.75rem;font-weight:700;color:#9cdcfe;">
            <span>📁 EXPLORER</span>
            <div style="display:flex;gap:4px;">
              <button class="btn-subtle" onclick="ideCreateFile()" title="New File" style="padding:2px 6px;font-size:0.72rem;">+📄</button>
              <button class="btn-subtle" onclick="ideCreateFolder()" title="New Folder" style="padding:2px 6px;font-size:0.72rem;">+📁</button>
              <button class="btn-subtle" onclick="ideLoadDir()" title="Refresh" style="padding:2px 6px;font-size:0.72rem;">🔄</button>
            </div>
          </div>
          <div style="padding:6px;border-bottom:1px solid #2d2d2d;display:flex;gap:4px;">
            <input id="idePathInput" type="text" style="flex:1;font-size:0.74rem;padding:4px 6px;background:#252526;border:1px solid #3c3c3c;color:#e2e8f0;" onkeydown="if(event.key==='Enter')ideLoadDir(this.value);" />
            <button class="btn-subtle" onclick="ideGoUp()" title="Parent Folder" style="padding:3px 6px;font-size:0.72rem;">⬆️</button>
          </div>
          <div id="ideFileList" style="flex:1;overflow-y:auto;padding:4px;font-size:0.8rem;"></div>
        </div>

        <!-- Center Top: Multi-Tab Code Editor -->
        <div style="display:flex;flex-direction:column;overflow:hidden;border-right:1px solid #2d2d2d;">
          <div style="display:flex;align-items:center;justify-content:space-between;background:#252526;border-bottom:1px solid #2d2d2d;padding-right:8px;">
            <div id="ideTabs" style="display:flex;overflow-x:auto;flex:1;"></div>
            <div style="display:flex;gap:6px;">
              <button class="btn-subtle" onclick="ideSaveActive()" style="padding:3px 9px;font-size:0.74rem;">💾 Save (Ctrl+S)</button>
              <button class="btn-subtle" onclick="ideRunActiveFile()" style="padding:3px 9px;font-size:0.74rem;background:#065f46;color:#ecfdf5;border-color:#10b981;">▶️ Run File</button>
            </div>
          </div>
          <div style="flex:1;display:flex;overflow:hidden;position:relative;">
            <pre id="ideLineNums" style="margin:0;padding:10px 8px;background:#1e1e1e;color:#858585;font-family:ui-monospace,Consolas,monospace;font-size:0.84rem;line-height:1.5;text-align:right;user-select:none;border-right:1px solid #2d2d2d;min-width:42px;overflow:hidden;">1</pre>
            <textarea id="ideEditor" spellcheck="false" style="flex:1;background:#1e1e1e;color:#d4d4d4;border:none;outline:none;padding:10px 12px;font-family:ui-monospace,Consolas,monospace;font-size:0.84rem;line-height:1.5;resize:none;white-space:pre;overflow:auto;" oninput="ideOnEdit()" onkeyup="ideUpdateCursor()" onclick="ideUpdateCursor()" onscroll="document.getElementById('ideLineNums').scrollTop=this.scrollTop;"></textarea>
          </div>
          <div id="ideStatusBar" style="padding:3px 10px;background:#007acc;color:#ffffff;font-size:0.72rem;display:flex;justify-content:space-between;">
            <span id="ideActivePath">No file open</span>
            <span id="ideCursorPos">Ln 1, Col 1</span>
          </div>
        </div>

        <!-- Right: Antigravity AI Agent + Live Preview -->
        <div style="grid-row:1/3;background:#181818;display:flex;flex-direction:column;overflow:hidden;">
          <div style="padding:6px 8px;background:#252526;border-bottom:1px solid #2d2d2d;display:flex;gap:6px;">
            <button class="btn-subtle" id="ideRightAiBtn" onclick="ideSwitchRight('ai')" style="flex:1;font-size:0.75rem;background:#094771;color:#fff;">🤖 Agent</button>
            <button class="btn-subtle" id="ideRightPrevBtn" onclick="ideSwitchRight('preview')" style="flex:1;font-size:0.75rem;">🌐 Live Preview</button>
          </div>
          <div id="ideAiPane" class="agent-pane">
            <div class="agent-modes" role="tablist" aria-label="Agent mode">
              <button class="agent-mode active" data-mode="auto" role="tab" aria-selected="true"
                      onclick="agentSetMode('auto')" title="Plan, edit and run automatically">🤖 Auto</button>
              <button class="agent-mode" data-mode="turbo" role="tab" aria-selected="false"
                      onclick="agentSetMode('turbo')" title="Apply immediately with minimal narration">⚡ Turbo</button>
              <button class="agent-mode" data-mode="review" role="tab" aria-selected="false"
                      onclick="agentSetMode('review')" title="Propose a diff and wait for approval">🔍 Request review</button>
            </div>
            <div class="agent-chips">
              <button class="agent-chip" onclick="agentQuick('review this file')" title="Static review of the open file">🔎 Review</button>
              <button class="agent-chip" onclick="agentQuick('add tests')" title="Write a unittest file for this module">🧪 Tests</button>
              <button class="agent-chip" onclick="agentQuick('add docstrings')" title="Insert missing docstrings">📝 Docstrings</button>
              <button class="agent-chip" onclick="agentQuick('clean up formatting')" title="Normalise whitespace and line endings">🧹 Clean</button>
              <button class="agent-chip" onclick="agentQuick('run it')" title="Execute the open file">▶ Run</button>
            </div>
            <div id="agentTranscript" class="agent-transcript">
              <div class="agent-empty">
                <strong>Agent ready.</strong><br>
                It reads the open file, performs a real operation and shows you the diff.
                No model weights are installed, so it works from parsers and the recipe library — never guesses.
              </div>
            </div>
            <div id="agentReview" class="agent-review" style="display:none;">
              <div class="agent-review-head">Proposed changes <span id="agentReviewMeta"></span></div>
              <pre id="agentDiff" class="agent-diff"></pre>
              <div class="agent-review-actions">
                <button class="agent-apply" onclick="agentApprove()">✅ Apply</button>
                <button class="agent-discard" onclick="agentReject()">✕ Reject</button>
              </div>
            </div>
            <div class="agent-compose">
              <textarea id="agentPrompt" rows="2" spellcheck="false"
                        placeholder="Ask the agent… e.g. “create utils.py with a retry decorator”"
                        onkeydown="agentKey(event)"></textarea>
              <button id="agentSend" class="agent-send" onclick="agentRun()" title="Send (Enter)">➤</button>
            </div>
          </div>
          <div id="idePreviewPane" style="display:none;flex:1;flex-direction:column;overflow:hidden;padding:8px;">
            <iframe id="idePreviewFrame" sandbox="allow-scripts" style="flex:1;width:100%;border:1px solid #2d2d2d;border-radius:8px;background:#ffffff;"></iframe>
          </div>
        </div>

        <!-- Bottom: Integrated Full-System Terminal -->
        <div style="background:#0c0c0c;border-top:1px solid #2d2d2d;border-right:1px solid #2d2d2d;display:flex;flex-direction:column;overflow:hidden;">
          <div style="padding:4px 10px;background:#181818;border-bottom:1px solid #2d2d2d;display:flex;justify-content:space-between;align-items:center;font-size:0.74rem;">
            <span style="color:#34d399;font-weight:700;">💻 TERMINAL (Full PC Access)
              <button class="btn-subtle" onclick="switchTab('sandbox')" title="Open the sandbox &amp; terminal studio"
                      style="margin-left:8px;padding:1px 7px;font-size:0.68rem;">🧪 Sandbox</button>
            </span>
            <span id="ideTermCwd" style="color:#94a3b8;font-family:ui-monospace,Consolas,monospace;"></span>
          </div>
          <pre id="ideTermOut" style="flex:1;margin:0;padding:8px 10px;overflow-y:auto;font-family:ui-monospace,Consolas,monospace;font-size:0.78rem;color:#d4d4d4;white-space:pre-wrap;"></pre>
          <div style="display:flex;align-items:center;padding:4px 8px;border-top:1px solid #2d2d2d;background:#111;">
            <span style="color:#34d399;font-family:ui-monospace,Consolas,monospace;font-size:0.8rem;margin-right:6px;">PS&gt;</span>
            <input id="ideTermInput" type="text" placeholder="Type any command (e.g. dir, cd .., python script.py)..." style="flex:1;background:transparent;border:none;color:#e2e8f0;font-family:ui-monospace,Consolas,monospace;font-size:0.8rem;outline:none;" onkeydown="ideTermKey(event)" />
          </div>
        </div>
      </div>
    </div>

    <!-- SSD VIDEO AI STUDIO TAB -->
    <div id="tab-video" class="tab-view">
      <div class="gallery-container">
        <div class="lab-panel" style="padding:14px;">
          <div class="card-row">
            <span class="card-label">🎬 SSD Video AI Studio (30 GB SSD ROM Budget)</span>
            <span id="videoQuotaText" class="sub-metric">Used: 0.00 GB / 30.0 GB</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" id="videoQuotaBar"></div></div>
          <div id="videoModelsList" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px;margin-top:8px;"></div>
        </div>

        <div class="gallery-toolbar" style="flex-wrap:wrap;gap:8px;">
          <input id="videoPrompt" type="text" placeholder="Describe video (e.g. 'cyberpunk neon rain city, camera pan')..." style="flex:1;min-width:220px;" />
          <select id="videoEngine" style="max-width:210px;">
            <option value="auto">⚡ Auto (Best Available)</option>
            <option value="1.3b">⚡ Wan2.1-1.3B (Fast Turbo)</option>
            <option value="14b">👑 Wan2.1-14B (Cinema Master)</option>
            <option value="i2v">🎬 60FPS I2V Motion Synth</option>
          </select>
          <select id="videoRes" style="max-width:130px;">
            <option value="480x320">480x320</option>
            <option value="640x360">640x360</option>
          </select>
          <button class="btn-firehose" style="width:auto;" onclick="runVideoGen()">🎬 Generate Video</button>
        </div>

        <div id="videoStatus" class="sub-metric">Ready (99.9% Pure SSD ROM Mode).</div>
        <div id="videoPlayerWrap" style="display:none;max-width:640px;">
          <video id="videoPlayer" controls autoplay loop style="width:100%;border-radius:12px;border:1px solid var(--border);background:#000;"></video>
          <img id="videoWebpPlayer" style="display:none;width:100%;border-radius:12px;border:1px solid var(--border);" />
        </div>
        <div id="videoGrid" class="gallery-grid"></div>
      </div>
    </div>

    <!-- AI BENCHMARK TAB -->
    <div id="tab-benchmark" class="tab-view">
      <div class="lab-container" style="max-width:1100px;">
        <div class="lab-panel" style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
          <span class="card-label" style="margin:0;">⚡ AI Benchmark (Fast / Balanced / Accurate)</span>
          <button class="btn-subtle" onclick="runBenchmark('selected')" style="width:auto;padding:8px 14px;">Benchmark Selected Brain</button>
          <button class="btn-firehose" onclick="runBenchmark('all')" style="width:auto;padding:8px 16px;">⚡ Benchmark All Installed Brains</button>
        </div>
        <div id="benchmarkGrid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px; margin-top:12px;">
          <div class="sub-metric">Click a benchmark button to measure live TTFT, Tokens/sec, ms/token, and Coding/Logic Accuracy.</div>
        </div>
      </div>
    </div>

    <!-- SECURITY & BUG LAB TAB -->
    <div id="tab-security" class="tab-view">
      <div class="lab-container">
        <div class="lab-panel">
          <div class="card-label">1. Local Device Security Auditor (100% Offline)</div>
          <button class="btn-firehose" onclick="runLaptopAudit()">🛡️ Audit My Laptop Security</button>
          <div id="auditOutput" class="output-pre">Click "Audit My Laptop Security" to scan listening ports, firewall, admin privileges, and processes.</div>
        </div>

        <div class="lab-panel">
          <div class="card-label">2. Offline CVE &amp; Bug Bounty Vault</div>
          <div style="display:flex; gap:8px;">
            <input id="cveQuery" type="text" placeholder="Search CVE / SQLi / XSS / Buffer Overflow..." style="flex:1;" />
            <button class="btn-subtle" onclick="searchCVE()">Search</button>
          </div>
          <button class="btn-subtle" onclick="downloadCVE()">📥 Download CVE &amp; Security Datasets</button>
          <div id="cveOutput" class="output-pre">Search the local SSD FTS5 Vault for vulnerability patterns &amp; fixes.</div>
        </div>

        <div class="lab-panel full">
          <div class="card-label">3. Offline Code Vulnerability &amp; Bug Scanner (SAST)</div>
          <textarea id="codeInput" class="code-editor" placeholder="Paste Python, JS, C/C++, or SQL code here to scan for vulnerabilities..."></textarea>
          <div style="display:flex; gap:10px;">
            <button class="btn-firehose" style="width:auto;" onclick="scanCode()">🔍 Scan Code for Bugs &amp; Vulnerabilities</button>
            <button class="btn-subtle" onclick="patchCode()">🔧 Auto-Patch Code</button>
          </div>
          <div id="sastOutput" class="output-pre">SAST Severity Report &amp; Auto-Patch output will appear here.</div>
        </div>
      </div>
    </div>
  </main>

  <div id="sidebarOverlay" class="sidebar-overlay" onclick="closeSidebarOnMobile()"></div>

  <div id="shareModal" class="models-modal" onclick="if(event.target===this)closeShare()">
    <div class="models-panel" role="dialog" aria-modal="true" aria-label="Share chat">
      <header class="models-head">
        <div>
          <h2>🔗 Share chat</h2>
          <p id="shareSub">Nothing is uploaded. Copy the transcript, download Markdown, or copy a link that reopens this chat on this machine.</p>
        </div>
        <button class="icon-btn" onclick="closeShare()" title="Close">✕</button>
      </header>
      <div class="share-body">
        <textarea id="shareText" readonly spellcheck="false"></textarea>
      </div>
      <footer class="models-foot">
        <span id="shareFootNote">Local-only export · 100% offline</span>
        <div class="foot-actions">
          <button class="btn-subtle" onclick="copyShare()">📋 Copy</button>
          <button class="btn-subtle" onclick="downloadShare()">⬇ Markdown</button>
          <button class="btn-subtle" onclick="copyShareLink()">🔗 Copy link</button>
        </div>
      </footer>
    </div>
  </div>

  <div id="settingsModal" class="models-modal" onclick="if(event.target===this)closeSettings()">
    <div class="models-panel settings-panel" role="dialog" aria-modal="true" aria-label="Settings">
      <header class="models-head">
        <div>
          <h2>⚙️ Settings</h2>
          <p>Everything runs on this machine — no account, no upload, no telemetry.</p>
        </div>
        <button class="icon-btn" onclick="closeSettings()" title="Close (Esc)">✕</button>
      </header>
      <div class="settings-layout">
        <nav class="settings-nav" aria-label="Settings sections">
          <button class="settings-tab active" data-pane="benchmark" onclick="settingsTab('benchmark')">⚡ Benchmark</button>
          <button class="settings-tab" data-pane="engine" onclick="settingsTab('engine')">🧠 Engine</button>
          <button class="settings-tab" data-pane="workspace" onclick="settingsTab('workspace')">🗂 Workspace</button>
        </nav>
        <div class="settings-body">
          <section id="setPane-benchmark" class="settings-pane active">
            <div id="setBenchCards" class="set-cards">
              <div class="set-card"><span class="set-card-label">Accuracy</span><span class="set-card-value" id="setAcc">—</span></div>
              <div class="set-card"><span class="set-card-label">Latency</span><span class="set-card-value" id="setLat">—</span></div>
              <div class="set-card"><span class="set-card-label">Knowledge</span><span class="set-card-value" id="setTopics">—</span></div>
              <div class="set-card"><span class="set-card-label">Recipes</span><span class="set-card-value" id="setRecipes">—</span></div>
            </div>
            <div class="set-actions">
              <button class="btn-subtle" id="setBenchRun" onclick="settingsRunBenchmark()">▶ Run benchmark</button>
              <span class="set-note" id="setBenchStatus">18 deterministic checks · offline</span>
            </div>
            <div id="setChecks" class="set-checks"><div class="set-note">Loading checks…</div></div>
          </section>
          <section id="setPane-engine" class="settings-pane">
            <div id="setEngine" class="set-rows"><div class="set-note">Loading…</div></div>
            <div class="set-actions">
              <button class="btn-subtle" onclick="closeSettings(); openModels();">🧠 Manage models</button>
              <button class="btn-subtle" onclick="closeSettings(); newChat();">✦ New chat</button>
            </div>
          </section>
          <section id="setPane-workspace" class="settings-pane">
            <div id="setWorkspace" class="set-rows"><div class="set-note">Loading…</div></div>
          </section>
        </div>
      </div>
    </div>
  </div>
  <div id="modelsModal" class="models-modal" onclick="if(event.target===this)closeModels()">
    <div class="models-panel" role="dialog" aria-modal="true" aria-label="Models">
      <header class="models-head">
        <div>
          <h2>🧠 Brains &amp; models</h2>
          <p id="modelsSub">Local GGUF files the studio can run. Nothing is uploaded anywhere.</p>
        </div>
        <button class="btn-subtle" onclick="downloadRecommendedModels()" style="white-space:nowrap;">⬇ Recommended (~5.5 GB)</button>
        <button class="icon-btn" onclick="closeModels()" title="Close">✕</button>
      </header>
      <div id="modelsList" class="models-list">
        <div class="models-empty">Loading model catalog…</div>
      </div>
      <footer class="models-foot">
        <span id="modelsFootNote">Tip: <code>python download_models.py --recommended</code> fetches the ~5.5 GB starter set from a terminal.</span>
        <button class="btn-subtle" onclick="refreshModels()">↻ Refresh</button>
      </footer>
    </div>
  </div>
  <div id="toastHost" class="toast-host" aria-live="polite"></div>

  <div id="lightboxModal" class="lightbox-modal" onclick="closeFullscreen()">
    <img id="lightboxImg" src="" alt="Fullscreen preview" />
  </div>

  <script>
    let harvesterRunning = false;
    let imageMode = false;
    let currentBootId = null;
    let chats = JSON.parse(localStorage.getItem('argon_chats') || '[]');
    let currentChatId = null;
    const pendingChats = new Set();

    const promptInput = document.getElementById('promptInput');
    promptInput.addEventListener('input', () => {
      promptInput.style.height = 'auto';
      promptInput.style.height = Math.min(promptInput.scrollHeight, 180) + 'px';
    });
    promptInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(e);
      }
    });

    function toggleSidebar() {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      const overlay = document.getElementById('sidebarOverlay');
      if (!sb) return;
      const collapsed = sb.classList.toggle('collapsed');
      if (openBtn) openBtn.style.display = collapsed ? 'flex' : 'none';
      const overlayOn = !collapsed && window.innerWidth <= 820;
      if (overlay) overlay.classList.toggle('show', overlayOn);
      document.body.classList.toggle('sidebar-open', overlayOn);
    }

    function switchTab(name, btn) {
      document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tool-nav-btn, .tool-strip-btn, .side-tool').forEach(el => el.classList.remove('active'));
      const view = document.getElementById('tab-' + name);
      if (view) view.classList.add('active');
      const target = btn || document.querySelector(`.tool-nav-btn[data-tab="${name}"]`);
      if (target && target.classList) target.classList.add('active');
      const stripBtn = document.querySelector(`.tool-strip-btn[data-tab="${name}"], .side-tool[data-tab="${name}"]`);
      if (stripBtn) {
        stripBtn.classList.add('active');
        try { stripBtn.scrollIntoView({ inline: 'nearest', block: 'nearest' }); } catch (e) {}
      }
      if (name === 'chat') {
        updateScrollFab();
        const input = document.getElementById('promptInput');
        if (input && window.innerWidth > 820) input.focus();
      }
      if (name === 'sandbox') setTimeout(sandboxUpdateLineNums, 0);
      if (window.innerWidth <= 820) closeSidebarOnMobile();
      try { localStorage.setItem('argon_last_tab', name); } catch (e) {}
    }

    function historyAction(label, title, handler, kind) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'hist-act' + (kind ? ' ' + kind : '');
      b.title = title;
      b.setAttribute('aria-label', title);
      b.textContent = label;
      b.onclick = (e) => { e.stopPropagation(); handler(e); };
      return b;
    }

    function buildHistoryItem(chat, archived) {
      const item = document.createElement('div');
      item.className = 'history-item' + (chat.id === currentChatId ? ' active' : '') + (archived ? ' archived' : '');
      item.tabIndex = 0;
      item.onclick = () => loadChat(chat.id);
      item.onkeydown = (e) => { if (e.key === 'Enter') loadChat(chat.id); };

      const title = document.createElement('span');
      title.className = 'history-item-title';
      title.textContent = (pendingChats.has(chat.id) ? '⏳ ' : '') + (chat.title || 'New chat');
      title.title = chat.title || 'New chat';
      item.appendChild(title);

      const actions = document.createElement('span');
      actions.className = 'history-actions';
      if (archived) {
        actions.appendChild(historyAction('↩', 'Restore chat', () => restoreChat(chat.id)));
        actions.appendChild(historyAction('✕', 'Delete permanently', () => deleteChat(chat.id), 'danger'));
      } else {
        actions.appendChild(historyAction('🔗', 'Share / export', () => shareChat(chat.id)));
        actions.appendChild(historyAction('📦', 'Archive chat', () => archiveChat(chat.id)));
        actions.appendChild(historyAction('✕', 'Delete chat', () => deleteChat(chat.id), 'danger'));
      }
      item.appendChild(actions);
      return item;
    }

    function renderHistory() {
      const list = document.getElementById('historyList');
      if (!list) return;
      list.innerHTML = '';
      const active = chats.filter(c => !c.archived);
      const archived = chats.filter(c => c.archived);
      if (!active.length) {
        const empty = document.createElement('div');
        empty.className = 'history-empty';
        empty.textContent = chats.length ? 'Everything is archived — open Archived below.' : 'No chats yet — say hello to start.';
        list.appendChild(empty);
      }
      active.slice().reverse().forEach(c => list.appendChild(buildHistoryItem(c, false)));

      const block = document.getElementById('archiveBlock');
      const countEl = document.getElementById('archiveCount');
      const archList = document.getElementById('archiveList');
      if (block) block.style.display = archived.length ? '' : 'none';
      if (countEl) countEl.textContent = String(archived.length);
      if (archList) {
        archList.innerHTML = '';
        archived.slice().reverse().forEach(c => archList.appendChild(buildHistoryItem(c, true)));
      }
      const q = document.getElementById('chatSearchInput');
      if (q && q.value) filterHistory(q.value);
    }

    function toggleArchiveList(force) {
      const list = document.getElementById('archiveList');
      const toggle = document.getElementById('archiveToggle');
      const caret = document.getElementById('archiveCaret');
      if (!list) return;
      const open = typeof force === 'boolean' ? force : list.style.display === 'none';
      list.style.display = open ? 'block' : 'none';
      if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (caret) caret.textContent = open ? '▴' : '▾';
    }

    function archiveChat(id) {
      const chat = chats.find(c => c.id === id);
      if (!chat) return;
      chat.archived = true;
      localStorage.setItem('argon_chats', JSON.stringify(chats));
      if (currentChatId === id) newChat();
      toggleArchiveList(true);
      renderHistory();
      toast('Chat archived', 'ok', 1800);
    }

    function restoreChat(id) {
      const chat = chats.find(c => c.id === id);
      if (!chat) return;
      chat.archived = false;
      localStorage.setItem('argon_chats', JSON.stringify(chats));
      renderHistory();
      toast('Chat restored to recent', 'ok', 1800);
    }

    let shareChatId = null;

    function chatToMarkdown(chat) {
      const when = new Date(Number(chat.id) || Date.now()).toLocaleString();
      const lines = [`# ${chat.title || 'ARGON-OMEGA chat'}`, '', `_${when} · exported from ARGON-OMEGA (100% offline)_`, ''];
      (chat.messages || []).forEach(m => {
        lines.push(m.role === 'user' ? '**👤 You**' : '**✦ ARGON-OMEGA**');
        lines.push('');
        lines.push(m.text || '');
        if (m.imageUrl) lines.push('', `![generated image](${m.imageUrl})`);
        lines.push('');
      });
      return lines.join('\n').trim() + '\n';
    }

    function shareChat(id) {
      const chat = chats.find(c => c.id === id) || chats.find(c => c.id === currentChatId);
      if (!chat) { toast('Start a chat first, then share it', 'info'); return; }
      shareChatId = chat.id;
      const modal = document.getElementById('shareModal');
      const ta = document.getElementById('shareText');
      if (!modal || !ta) return;
      ta.value = chatToMarkdown(chat);
      const sub = document.getElementById('shareSub');
      if (sub) {
        sub.textContent = `“${chat.title || 'Untitled chat'}” · ${(chat.messages || []).length} message(s) · nothing is uploaded.`;
      }
      modal.classList.add('open');
      modal.style.display = 'flex';
    }

    function closeShare() {
      const modal = document.getElementById('shareModal');
      if (modal) { modal.classList.remove('open'); modal.style.display = 'none'; }
    }

    function copyShare() {
      const ta = document.getElementById('shareText');
      if (ta) copyToClipboard(ta.value, 'Transcript copied');
    }

    function downloadShare() {
      const chat = chats.find(c => c.id === shareChatId);
      if (!chat) return;
      const slug = (chat.title || 'argon-chat').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'argon-chat';
      const blob = new Blob([chatToMarkdown(chat)], { type: 'text/markdown;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = slug + '.md';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      toast('Markdown exported', 'ok');
    }

    function copyShareLink() {
      if (!shareChatId) return;
      const url = location.origin + location.pathname + '#chat-' + encodeURIComponent(shareChatId);
      copyToClipboard(url, 'Local link copied — opens this chat offline');
    }

    function openChatFromHash() {
      const m = String(location.hash || '').match(/^#chat-(.+)$/);
      if (!m) return false;
      let id = m[1];
      try { id = decodeURIComponent(id); } catch (e) {}
      const chat = chats.find(c => c.id === id);
      if (!chat) { toast('That shared chat is not on this machine anymore', 'info'); return false; }
      if (chat.archived) chat.archived = false;
      loadChat(id);
      return true;
    }

    function deleteChat(id, e) {
      if (e && e.stopPropagation) e.stopPropagation();
      const chat = chats.find(c => c.id === id);
      if (!chat) return;
      if (!chat.archived && chat.messages && chat.messages.length > 1 &&
          !confirm(`Delete "${chat.title || 'this chat'}" permanently?`)) return;
      chats = chats.filter(c => c.id !== id);
      localStorage.setItem('argon_chats', JSON.stringify(chats));
      if (shareChatId === id) { shareChatId = null; closeShare(); }
      if (currentChatId === id) {
        newChat();
      } else {
        renderHistory();
      }
    }

    function clearAllChats() {
      fetch('/api/chat/clear', { method: 'POST' }).catch(() => {});
      if (chats.length === 0) return;
      chats = [];
      localStorage.removeItem('argon_chats');
      newChat();
    }

    async function clearServerHistory() {
      await fetch('/api/chat/clear', { method: 'POST' });
      newChat();
    }

    function newChat() {
      currentChatId = null;
      const box = document.getElementById('chatBox');
      box.innerHTML = welcomeMarkup();
      renderHistory();
      const sc = document.getElementById('chatScroll');
      if (sc) sc.scrollTop = 0;
      promptInput.focus();
    }

    function loadChat(id) {
      const chat = chats.find(c => c.id === id);
      if (!chat) return;
      currentChatId = id;
      const box = document.getElementById('chatBox');
      box.innerHTML = '';
      if (chat.messages.length === 0) {
        newChat();
        return;
      }
      chat.messages.forEach((m, idx) => {
        if (m.role === 'user') appendUserMessage(m.text, idx);
        else appendAiMessage(m.text, m.badge, idx, m.imageUrl || null);
      });
      if (pendingChats.has(id)) {
        appendTypingIndicator();
      }



    renderHistory();
    }

    function saveMessageToHistory(role, text, badge = '', imageUrl = null, targetChatId = null) {
      let cid = targetChatId || currentChatId;
      if (!cid) {
        cid = Date.now().toString();
        currentChatId = cid;
        chats.push({
          id: cid,
          title: text.slice(0, 36) + (text.length > 36 ? '...' : ''),
          messages: []
        });
      }
      const chat = chats.find(c => c.id === cid);
      if (!chat) return -1;
      chat.messages.push({ role, text, badge, imageUrl });
      if (chats.length > 25) chats = chats.slice(-25);
      localStorage.setItem('argon_chats', JSON.stringify(chats));



    renderHistory();
      return chat.messages.length - 1;
    }

    function deletePrompt(idx) {
      const chat = chats.find(c => c.id === currentChatId);
      if (!chat || idx < 0 || idx >= chat.messages.length) return;
      chat.messages.splice(idx, 1);
      if (chat.messages.length === 0) {
        chats = chats.filter(c => c.id !== currentChatId);
        localStorage.setItem('argon_chats', JSON.stringify(chats));
        newChat();
      } else {
        localStorage.setItem('argon_chats', JSON.stringify(chats));
        loadChat(currentChatId);
      }
    }

    function branchChat(idx) {
      const chat = chats.find(c => c.id === currentChatId);
      if (!chat || idx < 0 || idx >= chat.messages.length) return;
      const newId = Date.now().toString();
      const branchedMessages = chat.messages.slice(0, idx + 1).map(m => ({ ...m }));
      const baseTitle = (chat.title || 'Chat').replace(/^⑂\s*/, '');
      chats.push({
        id: newId,
        title: ('⑂ ' + baseTitle).slice(0, 38),
        messages: branchedMessages
      });
      if (chats.length > 25) chats = chats.slice(-25);
      localStorage.setItem('argon_chats', JSON.stringify(chats));
      loadChat(newId);
    }

    function useSuggestion(text) {
      promptInput.value = text;
      sendMessage(new Event('submit'));
    }

    function renderRichMarkdown(raw) {
      const fencedBlocks = [];
      let text = String(raw || '').replace(/```(\w*)\n?([\s\S]*?)```/g, function(_, lang, code) {
        const marker = `__ARGON_RICH_CODE_${fencedBlocks.length}__`;
        fencedBlocks.push({ marker, code });
        return marker;
      });
      text = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      text = text.replace(/\\\[([\s\S]*?)\\\]/g, '<div class="math-block">$1</div>');
      text = text.replace(/\\\(([\s\S]*?)\\\)/g, '<span class="math-inline">$1</span>');
      text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function(_, alt, url) {
        return '<div style="margin:12px 0;background:rgba(15,23,42,0.75);border:1px solid rgba(56,189,248,0.3);border-radius:12px;padding:10px;display:inline-block;max-width:100%;">' +
               '<img src="' + url + '" alt="' + alt + '" style="max-width:460px;width:100%;border-radius:8px;display:block;box-shadow:0 6px 20px rgba(0,0,0,0.45);" />' +
               '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;font-size:0.85em;color:#94a3b8;">' +
               '<span>🖼️ ' + (alt || 'Generated Image') + '</span>' +
               '<a href="' + url + '" download style="color:#38bdf8;text-decoration:none;font-weight:700;padding:4px 10px;background:rgba(56,189,248,0.15);border-radius:6px;">⬇️ Download PNG</a>' +
               '</div></div>';
      });

      const lines = text.split('\n');
      const out = [];
      let inUl = false;
      let inOl = false;
      let inTable = false;
      let tableHeadDone = false;

      function closeLists() {
        if (inUl) { out.push('</ul>'); inUl = false; }
        if (inOl) { out.push('</ol>'); inOl = false; }
      }
      function closeTable() {
        if (inTable) { out.push('</tbody></table>'); inTable = false; tableHeadDone = false; }
      }
      function inlineFmt(s) {
        return s
          .replace(/`([^`]+)`/g, '<code>$1</code>')
          .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
          .replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
      }

      for (let i = 0; i < lines.length; i++) {
        const ln = lines[i];
        const trimmed = ln.trim();

        // Markdown tables
        if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
          closeLists();
          const cells = trimmed.slice(1, -1).split('|').map(c => c.trim());
          if (cells.every(c => /^:?-{2,}:?$/.test(c))) {
            continue;
          }
          if (!inTable) {
            out.push('<table><thead><tr>' + cells.map(c => `<th>${inlineFmt(c)}</th>`).join('') + '</tr></thead><tbody>');
            inTable = true;
            tableHeadDone = true;
          } else {
            out.push('<tr>' + cells.map(c => `<td>${inlineFmt(c)}</td>`).join('') + '</tr>');
          }
          continue;
        } else {
          closeTable();
        }

        // Headings
        if (/^###\s+/.test(trimmed)) { closeLists(); out.push(`<h3>${inlineFmt(trimmed.slice(4))}</h3>`); continue; }
        if (/^##\s+/.test(trimmed)) { closeLists(); out.push(`<h2>${inlineFmt(trimmed.slice(3))}</h2>`); continue; }
        if (/^#\s+/.test(trimmed)) { closeLists(); out.push(`<h1>${inlineFmt(trimmed.slice(2))}</h1>`); continue; }

        // Bullet & numbered lists
        if (/^[-*]\s+/.test(trimmed)) {
          if (inOl) { out.push('</ol>'); inOl = false; }
          if (!inUl) { out.push('<ul>'); inUl = true; }
          out.push(`<li>${inlineFmt(trimmed.replace(/^[-*]\s+/, ''))}</li>`);
          continue;
        }
        if (/^\d+\.\s+/.test(trimmed)) {
          if (inUl) { out.push('</ul>'); inUl = false; }
          if (!inOl) { out.push('<ol>'); inOl = true; }
          out.push(`<li>${inlineFmt(trimmed.replace(/^\d+\.\s+/, ''))}</li>`);
          continue;
        }

        closeLists();
        if (trimmed === '') {
          out.push('<br>');
        } else {
          out.push(inlineFmt(ln) + '<br>');
        }
      }
      closeLists();
      closeTable();
      let rendered = `<div class="md-content">${out.join('')}</div>`;
      fencedBlocks.forEach(block => {
        const code = block.code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        rendered = rendered.replace(block.marker, `<pre><code>${code}</code></pre>`);
      });
      return rendered;
    }

    async function runSandboxBlock(btn, lang) {
      const wrap = btn.closest('.sandbox-code-wrap');
      const code = wrap.querySelector('pre code').textContent;
      const out = wrap.querySelector('.sandbox-block-out');
      if (lang === 'html') {
        out.innerHTML = '';
        const iframe = document.createElement('iframe');
        iframe.setAttribute('sandbox', 'allow-scripts');
        iframe.style.cssText = 'width:100%;height:420px;border:1px solid #38bdf8;border-radius:8px;background:#090d16;margin-top:8px;';
        iframe.srcdoc = code;
        out.appendChild(iframe);
        return;
      }
      out.innerHTML = '<div style="font-size:0.8rem;color:#38bdf8;margin-top:6px;">⏳ Running in Kernel Audit Sandbox...</div>';
      try {
        const res = await fetch('/api/sandbox_run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code })
        });
        const data = await res.json();
        const txt = data.ok ? (data.stdout || '(No output)') : (data.stderr || data.stdout || 'Error');
        out.innerHTML = `<pre style="margin-top:6px;border-color:${data.ok ? '#10b981' : '#ef4444'};">${txt.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>`;
      } catch (e) {
        out.innerHTML = '<div style="color:#ef4444;font-size:0.8rem;">Sandbox error.</div>';
      }
    }

    function highlightVSCode(code) {
      const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const tokenRe = /(\/\*[\s\S]*?\*\/|\/\/.*$|#.*$|<!--[\s\S]*?-->)|(".*?"|'.*?'|`.*?`)|(&lt;\/?[a-zA-Z!][a-zA-Z0-9-]*|&gt;)|(\b(?:if|else|elif|return|while|for|import|from|try|except|catch|break|continue|with|as|in|not|and|or)\b)|(\b(?:function|const|let|var|def|class|async|await|new|true|false|True|False|None|null)\b)|(\b(?:document|window|Math|console|board|style|textContent)\b)|(\.[a-zA-Z_-][a-zA-Z0-9_-]*|#[a-zA-Z_-][a-zA-Z0-9_-]*)|(\b[a-zA-Z_][a-zA-Z0-9_]*(?=\s*\())|(\b[a-zA-Z-]+(?=\s*[:=]))|(\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|ms|s)?\b)/gm;
      return String(code).split('\n').map((line, i) => {
        const escaped = esc(line);
        const h = escaped.replace(tokenRe, (m, com, str, tag, kw, decl, obj, sel, fn, attr, num) => {
          if (com) return `<span class="tok-com">${com}</span>`;
          if (str) return `<span class="tok-str">${str}</span>`;
          if (tag) return `<span class="tok-tag">${tag}</span>`;
          if (kw) return `<span class="tok-kw">${kw}</span>`;
          if (decl) return `<span class="tok-decl">${decl}</span>`;
          if (obj) return `<span class="tok-obj">${obj}</span>`;
          if (sel) return `<span class="tok-sel">${sel}</span>`;
          if (fn) return `<span class="tok-fn">${fn}</span>`;
          if (attr) return `<span class="tok-attr">${attr}</span>`;
          if (num) return `<span class="tok-num">${num}</span>`;
          return m;
        });
        return `<div class="code-line"><span class="code-line-num">${i + 1}</span><span>${h || ' '}</span></div>`;
      }).join('');
    }

    function formatText(text) {
      const blocks = [];
      const masked = (text || '').replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const idx = blocks.length;
        blocks.push({ lang: (lang || '').toLowerCase(), code });
        return `__ARGON_CODE_BLOCK_${idx}__`;
      });
      let html = renderRichMarkdown(masked);
      blocks.forEach((b, idx) => {
        const rawCode = b.code;
        const lang = b.lang || (rawCode.includes('<!DOCTYPE') || rawCode.includes('<html') || rawCode.includes('<canvas') ? 'html' : 'python');
        const icon = lang.includes('html') ? '🌐 HTML5/JS' : (lang === 'python' || lang === 'py' ? '🐍 PYTHON' : `💻 ${lang.toUpperCase()}`);
        const card = document.createElement('div');
        card.className = 'code-card';
        card.dataset.lang = lang;
        card.dataset.raw = rawCode;
        card.innerHTML = `
          <div class="code-card-header">
            <span><strong>${icon}</strong> <span class="code-status" style="margin-left:8px;color:#38bdf8;">⚡ Auto-Running...</span></span>
            <span style="display:flex;gap:6px;">
              <button class="copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-card').dataset.raw); this.textContent='✓ Copied'; setTimeout(()=>this.textContent='📋 Copy',1200);">📋 Copy</button>
              <button class="copy-btn" onclick="runSingleCodeCard(this.closest('.code-card'))">🔄 Re-Run</button>
            </span>
          </div>
          <div class="code-card-body">${highlightVSCode(rawCode)}</div>
          <div class="inline-run-box" style="display:none;"></div>`;
        html = html.replace(`__ARGON_CODE_BLOCK_${idx}__`, card.outerHTML);
      });
      return html;
    }

    async function runSingleCodeCard(card) {
      if (!card) return;
      const lang = card.dataset.lang || 'python';
      const raw = card.dataset.raw || '';
      const statusEl = card.querySelector('.code-status');
      const box = card.querySelector('.inline-run-box');
      if (!raw.length || !box) return;
      const ed = document.getElementById('sandboxEditor');
      if (ed) ed.value = raw;
      box.style.display = 'block';
      if (lang.includes('html') || raw.includes('<!DOCTYPE') || raw.includes('<html') || raw.includes('<canvas')) {
        box.innerHTML = '';
        const ifr = document.createElement('iframe');
        ifr.className = 'inline-run-frame';
        ifr.sandbox = 'allow-scripts';
        ifr.srcdoc = raw;
        box.appendChild(ifr);
        if (statusEl) statusEl.textContent = '✅ Executed';
        return;
      }
      if (lang === 'javascript' || lang === 'js') {
        box.innerHTML = '';
        const ifr = document.createElement('iframe');
        ifr.className = 'inline-run-frame';
        ifr.sandbox = 'allow-scripts';
        ifr.srcdoc = `<!doctype html><html><body><script>${raw.replace(/<\/script/gi, '<\\/script')}<\/script></body></html>`;
        box.appendChild(ifr);
        if (statusEl) statusEl.textContent = '✅ Executed';
        return;
      }
      if (lang === 'python' || lang === 'py') {
        if (statusEl) statusEl.textContent = '⚡ Auto-Running...';
        try {
          const res = await fetch('/api/sandbox/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: raw })
          });
          const data = await res.json();
          const out = (data.stdout || '') + (data.stderr ? '\n[STDERR]\n' + data.stderr : '');
          box.textContent = out || '(Finished with no output)';
          if (statusEl) statusEl.textContent = data.returncode === 0 ? '✅ Executed' : '⚠️ Error';
        } catch (e) {
          box.textContent = 'Sandbox execution error.';
          if (statusEl) statusEl.textContent = '⚠️ Error';
        }
      } else {
        box.style.display = 'none';
        if (statusEl) statusEl.textContent = '✅ Ready';
      }
    }

    function autoRunChatCodeBlocks(container) {
      if (!container) return;
      container.querySelectorAll('.code-card').forEach(card => runSingleCodeCard(card));
    }

    function appendUserMessage(text, idx = null) {
      const ws = document.getElementById('welcomeScreen');
      if (ws) ws.remove();
      const box = document.getElementById('chatBox');
      const row = document.createElement('div');
      row.className = 'msg-row user';

      const wrap = document.createElement('div');
      wrap.className = 'msg-user-wrap';

      const bubble = document.createElement('div');
      bubble.className = 'msg-bubble-user';
      bubble.textContent = text;
      wrap.appendChild(bubble);

      if (idx !== null) {
        const actions = document.createElement('div');
        actions.className = 'msg-user-actions';

        const branchBtn = document.createElement('button');
        branchBtn.className = 'msg-action-btn';
        branchBtn.textContent = '⑂ Branch';
        branchBtn.title = 'Branch chat from this prompt';
        branchBtn.onclick = () => branchChat(idx);

        const delBtn = document.createElement('button');
        delBtn.className = 'msg-action-btn del';
        delBtn.textContent = '🗑 Delete';
        delBtn.title = 'Delete this prompt';
        delBtn.onclick = () => deletePrompt(idx);

        actions.appendChild(branchBtn);
        actions.appendChild(delBtn);
        wrap.appendChild(actions);
      }

      row.appendChild(wrap);
      box.appendChild(row);
      scrollToBottom();
    }

    function appendAiMessage(text, badgeText = '', idx = null, imageUrl = null) {
      const box = document.getElementById('chatBox');
      const row = document.createElement('div');
      row.className = 'msg-row ai';

      const avatar = document.createElement('div');
      avatar.className = 'ai-avatar';
      avatar.textContent = '✦';

      const body = document.createElement('div');
      body.className = 'msg-ai-body';

      const content = document.createElement('div');
      content.className = 'msg-ai-content';
      content.innerHTML = formatText(text);
      body.appendChild(content);
      autoRunChatCodeBlocks(content);

      if (imageUrl) {
        const card = document.createElement('div');
        card.className = 'chat-image-card';
        const img = document.createElement('img');
        img.src = imageUrl;
        img.alt = text;
        img.loading = 'lazy';
        img.decoding = 'async';
        img.onclick = () => openFullscreen(imageUrl);
        card.appendChild(img);

        const acts = document.createElement('div');
        acts.className = 'chat-image-actions';

        const dl = document.createElement('a');
        dl.className = 'btn-subtle';
        dl.href = imageUrl;
        dl.download = imageUrl.split('/').pop();
        dl.style.textDecoration = 'none';
        dl.textContent = '⬇ Download';

        const fs = document.createElement('button');
        fs.className = 'btn-subtle';
        fs.textContent = '🔍 Fullscreen';
        fs.onclick = () => openFullscreen(imageUrl);

        acts.appendChild(dl);
        acts.appendChild(fs);
        card.appendChild(acts);
        body.appendChild(card);
      }

      const footer = document.createElement('div');
      footer.className = 'msg-footer';
      if (badgeText) {
        const badge = document.createElement('span');
        badge.textContent = badgeText;
        footer.appendChild(badge);
      }
      const copyBtn = document.createElement('button');
      copyBtn.className = 'copy-btn';
      copyBtn.textContent = '📋 Copy';
      copyBtn.title = 'Copy this reply';
      copyBtn.onclick = () => {
        copyToClipboard(text, 'Reply copied');
        copyBtn.textContent = '✓ Copied';
        setTimeout(() => copyBtn.textContent = '📋 Copy', 1400);
      };
      footer.appendChild(copyBtn);

      const retryBtn = document.createElement('button');
      retryBtn.className = 'msg-action-btn';
      retryBtn.textContent = '↻ Regenerate';
      retryBtn.title = 'Ask again for a fresh answer';
      retryBtn.onclick = () => regenerateAnswer();
      footer.appendChild(retryBtn);

      if (idx !== null) {
        const branchBtn = document.createElement('button');
        branchBtn.className = 'msg-action-btn';
        branchBtn.textContent = '⑂ Branch';
        branchBtn.title = 'Branch chat from here';
        branchBtn.onclick = () => branchChat(idx);

        const delBtn = document.createElement('button');
        delBtn.className = 'msg-action-btn del';
        delBtn.textContent = '🗑 Delete';
        delBtn.title = 'Delete this message';
        delBtn.onclick = () => deletePrompt(idx);

        footer.appendChild(branchBtn);
        footer.appendChild(delBtn);
      }

      body.appendChild(footer);

      row.appendChild(avatar);
      row.appendChild(body);
      box.appendChild(row);
      scrollToBottom();
    }

    function appendTypingIndicator() {
      const box = document.getElementById('chatBox');
      const row = document.createElement('div');
      row.className = 'msg-row ai';
      row.id = 'typingRow';
      row.innerHTML = `
        <div class="ai-avatar">✦</div>
        <div class="msg-ai-body">
          <div class="typing-dots"><span></span><span></span><span></span></div>
        </div>`;
      box.appendChild(row);
      scrollToBottom();
    }

    function scrollToBottom(force = true) {
      const sc = document.getElementById('chatScroll');
      if (!sc) return;
      if (!force && !isNearBottom()) { updateScrollFab(); return; }
      sc.scrollTop = sc.scrollHeight;
      updateScrollFab();
    }

    async function fetchTelemetry() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (currentBootId && data.boot_id && data.boot_id !== currentBootId) {
          location.reload();
          return;
        }
        currentBootId = data.boot_id;
        const s = data.storage;

        const romSizeEl = document.getElementById('romSize');
        if (romSizeEl) {
          const fGb = s.folder_size_gb ?? s.db_size_gb;
          const fMb = s.folder_size_mb ?? s.db_size_mb;
          romSizeEl.textContent = fGb >= 1 ? `${fGb.toFixed(2)} GB` : `${fMb.toFixed(2)} MB`;
        }
        const romProgEl = document.getElementById('romProgress');
        if (romProgEl) {
          const fGb = s.folder_size_gb ?? s.db_size_gb;
          romProgEl.style.width = `${Math.min(100, (fGb / s.max_rom_gb) * 100)}%`;
        }
        const totalRowsEl = document.getElementById('totalRows');
        if (totalRowsEl) {
          const memRows = Number(s.memory_rows || 0);
          const brainRows = Number(s.total_rows || 0);
          totalRowsEl.innerHTML = memRows
            ? `<span id="memoryRows">${memRows.toLocaleString()}</span> mem · ${brainRows.toLocaleString()} idx`
            : `${brainRows.toLocaleString()} rows indexed`;
          totalRowsEl.title = `Memory rows: ${s.memory_rows} · Indexed brain rows: ${s.total_rows} · ROM limit ${s.max_rom_gb} GB`;
        }

        if (typeof data.offline_mode === 'boolean') {
          window._offlineMode = data.offline_mode;
          const oBtn = document.getElementById('offlineModeBtn');
          if (oBtn) {
            oBtn.innerHTML = data.offline_mode ? '✈️ <span class="t-label">Offline</span>' : '🌐 <span class="t-label">Online</span>';
            oBtn.title = data.offline_mode
              ? 'Offline mode is ON — no network calls are made'
              : 'Offline mode is OFF — click to block all network access';
            oBtn.classList.toggle('on', !!data.offline_mode);
          }
        }
        if (data.flux_status) {
          const fb = document.getElementById('fluxRomBanner');
          if (fb) {
            const f = data.flux_status;
            fb.innerHTML = f.ready
              ? `🟢 <b>FLUX.1-schnell Pure ROM READY</b> (<code>${f.diffusion_model}</code> + <code>${f.t5xxl}</code> + <code>${f.clip_l}</code> + <code>${f.vae}</code>)`
              : `🟡 <b>FLUX.1-schnell Pure ROM:</b> Missing files in <code>models/flux/</code> (using <code>sd_turbo_q8_0.gguf</code>). <button class="btn" style="margin-left:8px;padding:4px 10px;font-size:11px;background:#6366f1;" onclick="downloadFluxRom()">⬇️ Download flux1-schnell-Q4_K_S.gguf to SSD ROM</button> <span id="fluxDlProg" style="margin-left:8px;font-size:11px;color:#93c5fd;"></span>`;
          }
        }
        applyEngineStatus(data);

        if (data.brains) {
          const bSel = document.getElementById('brainSelect');
          if (bSel) {
            const prevVal = bSel.value;
            bSel.innerHTML = '';
            if (data.brains.available_models && data.brains.available_models.length) {
              data.brains.available_models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = m.name;
                opt.disabled = Boolean(m.disabled);
                bSel.appendChild(opt);
              });
            } else {
              bSel.innerHTML = '<option value="auto">⚡ Auto (Takes Best Model Automatically)</option>';
              (data.brains.cartridges || []).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.filename;
                const label = c.display_name || c.filename;
                opt.textContent = `${c.active ? '🟢 ' : ''}${label} (${c.size_mb} MB)`;
                bSel.appendChild(opt);
              });
            }
            bSel.value = data.brains.selected_mode || prevVal || 'auto';
          }
          if (typeof data.brains.quantum_mode === 'boolean') {
            quantumModeEnabled = data.brains.quantum_mode;
            const qBtn = document.getElementById('quantumToggleBtn');
            if (qBtn) qBtn.textContent = quantumModeEnabled ? '⚛️ Quantum Mode: ON' : '⚛️ Quantum Mode: OFF';
          }
        }
        if (data.matrix) {
          const mEl = document.getElementById('matrixCounter');
          if (mEl) {
            const tok = Number(data.matrix.stored_tokens || 0).toLocaleString();
            const cap = Number(data.matrix.max_capacity || 100000000).toLocaleString();
            mEl.textContent = `🌌 Infini-Matrix: ${tok} / ${cap} Tokens on SSD (2MB RAM | 0.3ms Search)`;
          }
        }
      } catch (e) {}
    }

    async function feedFilesToMatrix(event) {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;
      const btn = document.getElementById('feedMatrixBtn');
      const prevText = btn.textContent;
      btn.textContent = '⏳ Ingesting to SSD Matrix...';
      try {
        for (const file of files) {
          const text = await file.text();
          await fetch('/api/matrix/ingest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, source_tag: file.name })
          });
        }
      } finally {
        btn.textContent = prevText;
        event.target.value = '';
      }
    }

    let quantumModeEnabled = true;
    let swarmModeEnabled = true;

    function toggleSwarmMode() {
      swarmModeEnabled = !swarmModeEnabled;
      const sBtn = document.getElementById('swarmToggleBtn');
      if (sBtn) sBtn.textContent = swarmModeEnabled ? '⚡ Fast Sub-Agent Swarm: ON' : '⚡ Fast Sub-Agent Swarm: OFF';
    }

    async function toggleQuantumMode() {
      quantumModeEnabled = !quantumModeEnabled;
      const qBtn = document.getElementById('quantumToggleBtn');
      if (qBtn) {
        qBtn.classList.toggle('active', quantumModeEnabled);
        qBtn.textContent = quantumModeEnabled ? '⚛️ Quantum ON' : '⚛️ Quantum';
        qBtn.title = quantumModeEnabled ? 'Quantum reasoning enabled' : 'Quantum reasoning disabled';
      }
      try {
        await fetch('/api/quantum/toggle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: quantumModeEnabled })
        });
        toast(quantumModeEnabled ? 'Quantum reasoning enabled' : 'Quantum reasoning disabled', 'info', 1600);
      } catch (_) {
        toast('Could not update quantum mode', 'err');
      }
    }

    async function toggleOfflineMode() {
      const nextState = !(window._offlineMode !== false);
      await fetch('/api/offline_mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ offline: nextState })
      });
      fetchTelemetry();
    }

    async function selectBrain() {
      const mode = document.getElementById('brainSelect').value;
      await fetch('/api/models/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
      });
    }

    async function toggleHarvester() {
      await fetch(harvesterRunning ? '/api/harvester/pause' : '/api/harvester/start', { method: 'POST' });
    }

    async function stopServer() {
      try {
        await fetch('/api/shutdown', { method: 'POST' });
      } catch (e) {}
      document.body.innerHTML = '<div style="margin:auto;color:#e2e8f0;font-family:sans-serif;text-align:center;"><h1>ARGON-OMEGA Stopped</h1><p>You can close this tab now.</p></div>';
    }

    function toggleImageMode() {
      imageMode = !imageMode;
      const btn = document.getElementById('imageModeBtn');
      btn.classList.toggle('active', imageMode);
      promptInput.placeholder = imageMode
        ? 'Describe an image to generate (Flux AI / Offline SVG)...'
        : 'Message ARGON-OMEGA...';
      promptInput.focus();
    }

    function hasCodingIntent(prompt) {
      const lower = String(prompt || '').toLowerCase();
      return /\b(code|program|python|html|css|js|javascript|game|tic-tac-toe|board|player|win|tie|function|loop|grid|button)\b/.test(lower)
        || lower.includes('draw/tie')
        || lower.includes('process 1');
    }

    function openFullscreen(url) {
      const modal = document.getElementById('lightboxModal');
      document.getElementById('lightboxImg').src = url;
      modal.classList.add('open');
    }

    function closeFullscreen() {
      document.getElementById('lightboxModal').classList.remove('open');
    }

    async function generateBrandNewImage() {
      const prompt = document.getElementById('galleryPrompt').value.trim();
      const style = document.getElementById('galleryStyle').value;
      const out = document.getElementById('studioResult');
      const btn = document.getElementById('genStudioBtn');
      if (!prompt) return;
      btn.disabled = true;
      out.innerHTML = '<div style="padding:12px;background:rgba(15,23,42,0.75);border-radius:10px;color:#38bdf8;">🎨 Synthesizing brand-new neural image (Unique Seed)...</div>';
      try {
        const res = await fetch('/api/generate_image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, style, width: 768, height: 768 })
        });
        const data = await res.json();
        if (!data.ok) {
          out.innerHTML = `<div style="color:#ef4444;">Error: ${data.error}</div>`;
        } else {
          out.innerHTML = `
            <div style="background:rgba(15,23,42,0.85);border:1px solid rgba(56,189,248,0.35);border-radius:12px;padding:12px;max-width:520px;">
              <img src="${data.url}" style="width:100%;border-radius:8px;display:block;" />
              <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;font-size:0.82rem;color:#94a3b8;">
                <span>Seed #${data.seed} • ${data.elapsed_ms}ms • ${data.engine}</span>
                <a href="${data.url}" download style="color:#38bdf8;text-decoration:none;font-weight:700;">⬇️ Download PNG</a>
              </div>
            </div>`;
          loadGallery();
        }
      } finally {
        btn.disabled = false;
      }
    }

    let upscale2kEnabled = true;

    function toggleUpscale2k() {
      upscale2kEnabled = !upscale2kEnabled;
      const btn = document.getElementById('upscale2kBtn');
      if (btn) btn.textContent = upscale2kEnabled ? '🔍 SSD Tiled 2K Ultra-HD Upscale: ON' : '🔍 SSD Tiled 2K Ultra-HD Upscale: OFF';
    }

    async function loadImageVault() {
      const el = document.getElementById('imageVaultList');
      if (!el) return;
      try {
        const res = await fetch('/api/images/vault');
        const data = await res.json();
        el.innerHTML = '';
        (data.vault || []).forEach(m => {
          const pill = document.createElement('div');
          pill.style.cssText = 'display:flex;align-items:center;gap:6px;background:rgba(15,23,42,0.7);border:1px solid rgba(255,255,255,0.12);padding:5px 10px;border-radius:8px;';
          pill.innerHTML = m.installed
            ? `<span>🟢 <strong>${m.name}</strong> (${m.size_mb} MB)</span>`
            : `<span>⚪ <strong>${m.name}</strong> (${m.role})</span><button class="btn-subtle" style="width:auto;padding:2px 8px;font-size:0.72rem;" onclick="downloadVaultModel('${m.id}', this)">⬇ Download</button>`;
          el.appendChild(pill);
        });
      } catch (e) {}
    }

    async function downloadVaultModel(id, btn) {
      if (btn) btn.textContent = '⏳ Downloading...';
      await fetch('/api/images/vault/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id })
      });
      loadImageVault();
    }

    async function loadGallery() {
      loadImageVault();
      const grid = document.getElementById('galleryGrid');
      if (!grid) return;
      grid.innerHTML = '<div class="sub-metric">Loading SSD images...</div>';
      const res = await fetch('/api/images');
      const data = await res.json();
      grid.innerHTML = '';
      if (!data.images || data.images.length === 0) {
        grid.innerHTML = '<div class="sub-metric">No images saved in ssd_images/ yet. Generate one above or in Chat!</div>';
        return;
      }
      data.images.forEach(item => {
        const card = document.createElement('div');
        card.className = 'gallery-card';
        card.innerHTML = `
          <img src="${item.url}" alt="${item.prompt}" loading="lazy" decoding="async" onclick="openFullscreen('${item.url}')" />
          <div class="gallery-prompt" title="${item.prompt}">${item.prompt}</div>
          <div class="sub-metric">${item.mode}</div>
          <div class="chat-image-actions">
            <a class="btn-subtle" href="${item.url}" download="${item.filename}" style="text-decoration:none;">⬇ Download</a>
            <button class="btn-subtle" onclick="openFullscreen('${item.url}')">🔍 Fullscreen</button>
            <button class="btn-subtle" onclick="animateImageToVideo('${item.filename}', '${item.prompt.replace(/'/g, '')}')">🎬 Animate</button>
          </div>`;
        grid.appendChild(card);
      });
    }

    async function generateInGallery(forceOffline) {
      const input = document.getElementById('galleryPrompt');
      const prompt = input.value.trim();
      if (!prompt) return;
      const grid = document.getElementById('galleryGrid');
      grid.insertAdjacentHTML('afterbegin', '<div id="galLoading" class="sub-metric">Generating image...</div>');
      await fetch('/api/image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, force_offline: forceOffline })
      });
      input.value = '';
      loadGallery();
    }

    let activeStream = null;          // AbortController for the in-flight reply
    let lastUserPrompt = '';
    let isSending = false;

    function setComposerBusy(busy) {
      isSending = busy;
      const btn = document.getElementById('sendBtn');
      if (!btn) return;
      btn.classList.toggle('stopping', busy);
      btn.textContent = busy ? '■' : '↑';
      btn.title = busy ? 'Stop generating (Esc)' : 'Send (Enter)';
      btn.dataset.mode = busy ? 'stop' : 'send';
    }

    function stopStreaming() {
      if (activeStream) {
        try { activeStream.abort(); } catch (_) {}
        activeStream = null;
        toast('Generation stopped', 'info');
      }
      setComposerBusy(false);
      const typing = document.getElementById('typingRow');
      if (typing) typing.remove();
    }

    function composeStreamingRow() {
      const box = document.getElementById('chatBox');
      const row = document.createElement('div');
      row.className = 'msg-row ai';
      row.id = 'streamRow';
      const avatar = document.createElement('div');
      avatar.className = 'ai-avatar';
      avatar.textContent = '✦';
      const body = document.createElement('div');
      body.className = 'msg-ai-body';
      const textDiv = document.createElement('div');
      textDiv.className = 'msg-ai-text';
      textDiv.innerHTML = '<span class="typing-cursor">▌</span>';
      const badgeDiv = document.createElement('div');
      badgeDiv.className = 'msg-badge';
      badgeDiv.textContent = '⚡ Streaming live…';
      body.appendChild(textDiv);
      body.appendChild(badgeDiv);
      row.appendChild(avatar);
      row.appendChild(body);
      box.appendChild(row);
      scrollToBottom();
      return { row, textDiv, badgeDiv };
    }

    async function sendMessage(e) {
      if (e && e.preventDefault) e.preventDefault();
      if (isSending) { toast('Still generating — press Esc to stop.', 'info'); return; }
      const text = promptInput.value.trim();
      if (!text) { promptInput.focus(); return; }

      lastUserPrompt = text;
      promptInput.value = '';
      updateComposerMeta();
      promptInput.style.height = 'auto';

      const ws = document.getElementById('welcomeScreen');
      if (ws) ws.remove();

      const userIdx = saveMessageToHistory('user', text);
      const originChatId = currentChatId;
      pendingChats.add(originChatId);
      renderHistory();
      appendUserMessage(text, userIdx);
      appendTypingIndicator();
      setComposerBusy(true);

      const lang = 'auto';  /* language UI removed — the engine auto-detects */

      try {
        /* ---------------- image generation mode ---------------- */
        if (imageMode && !hasCodingIntent(text)) {
          const res = await fetch('/api/image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text, language: lang, quantum_mode: quantumModeEnabled })
          });
          const data = await readJsonSafe(res);
          const replyText = data.reply ?? data.answer ?? (data.error ? `⚠️ ${data.error}` : 'No response received.');
          const badge = data.badge || `⚡ ${data.latency_ms ?? 0}ms • ${data.source || 'local'} • 100% Offline`;
          const imgUrl = data.image_url || null;
          const aiIdx = saveMessageToHistory('ai', replyText, badge, imgUrl, originChatId);
          if (currentChatId === originChatId) {
            const typing = document.getElementById('typingRow');
            if (typing) typing.remove();
            appendAiMessage(replyText, badge, aiIdx, imgUrl);
          }
          if (imgUrl) loadGallery();
          return;
        }

        /* ---------------- normal streaming chat ---------------- */
        const typing = document.getElementById('typingRow');
        if (typing) typing.remove();

        const { row: streamRow, textDiv, badgeDiv } = composeStreamingRow();
        const controller = new AbortController();
        activeStream = controller;

        const selectedModel = document.getElementById('brainSelect') ? document.getElementById('brainSelect').value : 'auto';
        const thinkingLevel = document.getElementById('thinkingLevelSelect') ? document.getElementById('thinkingLevelSelect').value : 'mid';
        const res = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({
            prompt: text,
            language: lang,
            model: selectedModel,
            thinking_level: thinkingLevel,
            quantum_mode: quantumModeEnabled,
            swarm_enabled: swarmModeEnabled
          })
        });

        if (!res.ok || !res.body) {
          const detail = await readJsonSafe(res);
          const why = detail.error || `HTTP ${res.status}`;
          streamRow.remove();
          toast(`Server rejected the request (${why})`, 'err');
          const msg = `⚠️ The local server refused this request: **${why}**. ` +
            (res.status === 403 ? 'The host/origin guard blocked it — reload the page or see the console for the allowed hosts.' : 'Check the server log for details.');
          const idx = saveMessageToHistory('ai', msg, 'Error', null, originChatId);
          if (currentChatId === originChatId) appendAiMessage(msg, 'Error', idx);
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let fullText = '';
        let finalBadge = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            let ev;
            try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
            if (ev.type === 'token') {
              fullText += ev.token || '';
              textDiv.innerHTML = formatText(fullText) + '<span class="typing-cursor">▌</span>';
              if (isNearBottom()) scrollToBottom();
            } else if (ev.type === 'done') {
              fullText = ev.reply || fullText;
              finalBadge = ev.badge || '';
            } else if (ev.type === 'error') {
              finalBadge = 'Error';
              fullText += `\n\n⚠️ ${ev.error || 'Generation failed.'}`;
            }
          }
        }

        streamRow.remove();
        const reply = fullText.trim() || 'No response received.';
        const aiIdx = saveMessageToHistory('ai', reply, finalBadge, null, originChatId);
        if (currentChatId === originChatId) appendAiMessage(reply, finalBadge, aiIdx);
      } catch (err) {
        const typing = document.getElementById('typingRow');
        if (typing) typing.remove();
        const streamRow = document.getElementById('streamRow');

        if (err && err.name === 'AbortError') {
          if (streamRow) streamRow.remove();
          saveMessageToHistory('ai', '⏹ Generation stopped by you.', 'Stopped', null, originChatId);
          const chat = chats.find(c => c.id === originChatId);
          if (chat && currentChatId === originChatId) {
            const box = document.getElementById('chatBox');
            const note = document.createElement('div');
            note.className = 'msg-row ai';
            note.innerHTML = '<div class="ai-avatar">✦</div><div class="msg-ai-body"><div class="msg-ai-content" style="color:#93a4b8;">⏹ Generation stopped.</div></div>';
            box.appendChild(note);
            scrollToBottom();
          }
        } else {
          if (streamRow) streamRow.remove();
          const msg = `⚠️ Could not reach the local ARGON-OMEGA server. ${err && err.message ? '(' + err.message + ')' : ''}`;
          toast('Connection to the local server failed', 'err');
          const idx = saveMessageToHistory('ai', msg, 'Error', null, originChatId);
          if (currentChatId === originChatId) appendAiMessage(msg, 'Error', idx);
        }
      } finally {
        activeStream = null;
        pendingChats.delete(originChatId);
        renderHistory();
        setComposerBusy(false);
        if (window.innerWidth <= 768) closeSidebarOnMobile();
      }
    }

    async function readJsonSafe(res) {
      try {
        const textBody = await res.text();
        return textBody ? JSON.parse(textBody) : {};
      } catch (_) {
        return {};
      }
    }

    async function regenerateAnswer() {
      if (isSending) return;
      let prompt = lastUserPrompt;
      if (!prompt) {
        const chat = chats.find(c => c.id === currentChatId);
        const lastUser = chat && [...chat.messages].reverse().find(m => m.role === 'user');
        prompt = lastUser ? lastUser.text : '';
      }
      if (!prompt) { toast('Nothing to regenerate yet', 'info'); return; }
      promptInput.value = prompt;
      updateComposerMeta();
      await sendMessage();
    }


    function loadSandboxTemplate(kind) {
      const ed = document.getElementById('sandboxEditor');
      if (!ed) return;
      if (kind === 'snake') {
        ed.value = `<!DOCTYPE html>
<html>
<body style="margin:0;background:#0f172a;color:#38bdf8;font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;">
  <div style="margin-bottom:6px;font-weight:bold;">🐍 Snake Game (Use Arrow Keys) — Score: <span id="sc">0</span></div>
  <canvas id="c" width="280" height="280" style="border:2px solid #38bdf8;background:#020617;"></canvas>
  <script>
    const ctx = document.getElementById('c').getContext('2d');
    let snake = [{x:140,y:140}], dx=14, dy=0, food={x:70,y:70}, score=0;
    document.addEventListener('keydown', e => {
      if(e.key==='ArrowUp'&&dy===0){dx=0;dy=-14;}
      if(e.key==='ArrowDown'&&dy===0){dx=0;dy=14;}
      if(e.key==='ArrowLeft'&&dx===0){dx=-14;dy=0;}
      if(e.key==='ArrowRight'&&dx===0){dx=14;dy=0;}
    });
    setInterval(() => {
      const head = {x:(snake[0].x+dx+280)%280, y:(snake[0].y+dy+280)%280};
      snake.unshift(head);
      if(head.x===food.x && head.y===food.y){
        score++; document.getElementById('sc').textContent=score;
        food={x:Math.floor(Math.random()*20)*14, y:Math.floor(Math.random()*20)*14};
      } else snake.pop();
      ctx.fillStyle='#020617'; ctx.fillRect(0,0,280,280);
      ctx.fillStyle='#f43f5e'; ctx.fillRect(food.x,food.y,13,13);
      ctx.fillStyle='#22d3ee'; snake.forEach(s=>ctx.fillRect(s.x,s.y,13,13));
    }, 110);
  <\/script>
</body>
</html>`;
      } else if (kind === 'python') {
        ed.value = `def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    mid = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + mid + quicksort(right)

data = [42, 7, 19, 3, 99, 15, 1, 64]
print("Sorted:", quicksort(data))
primes = [n for n in range(2, 50) if all(n % d != 0 for d in range(2, int(n**0.5) + 1))]
print("Primes < 50:", primes)`;
      } else if (kind === 'terminal') {
        sandboxSetMode('terminal');
        ed.value = '!whoami';
      } else {
        sandboxSetMode('code');
      }
      if (kind === 'snake') {
        sandboxSetMode('code');
        const file = document.getElementById('sandboxFileName');
        if (file) file.textContent = 'snake.html';
        sandboxSetOutput('preview');
      }
      sandboxUpdateLineNums();
      sandboxStatus('idle', 'idle');
    }

    const terminalHistory = [];
    let terminalHistIdx = -1;

    function handleTerminalKey(e) {
      const inp = document.getElementById('terminalCmdInput');
      if (!inp) return;
      if (e.key === 'Enter') {
        e.preventDefault();
        const cmd = inp.value.trim();
        if (!cmd) return;
        terminalHistory.push(cmd);
        terminalHistIdx = terminalHistory.length;
        inp.value = '';
        runTerminalCommand(cmd);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (terminalHistIdx > 0) {
          terminalHistIdx--;
          inp.value = terminalHistory[terminalHistIdx];
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (terminalHistIdx < terminalHistory.length - 1) {
          terminalHistIdx++;
          inp.value = terminalHistory[terminalHistIdx];
        } else {
          terminalHistIdx = terminalHistory.length;
          inp.value = '';
        }
      }
    }

    async function runTerminalCommand(cmd) {
      const out = document.getElementById('sandboxConsole');
      const promptEl = document.getElementById('terminalPromptLabel');
      const low = cmd.toLowerCase();
      if (low === 'cls' || low === 'clear') {
        out.textContent = '';
        return;
      }
      const curPrompt = promptEl ? promptEl.textContent : 'PS>';
      out.textContent += `\n${curPrompt} ${cmd}\n`;
      out.scrollTop = out.scrollHeight;
      const res = await fetch('/api/sandbox/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: cmd, mode: 'terminal' })
      });
      const data = await res.json();
      if (data.cwd && promptEl) {
        promptEl.textContent = `PS ${data.cwd}>`;
      }
      if (data.clear) {
        out.textContent = '';
        return;
      }
      const textOut = (data.stdout || '') + (data.stderr ? '\n[STDERR]\n' + data.stderr : '');
      if (textOut.trim()) {
        out.textContent += textOut + '\n';
      }
      out.scrollTop = out.scrollHeight;
    }

    async function sandboxExec(code, mode) {
      const res = await fetch('/api/sandbox/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, mode })
      });
      return await readJsonSafe(res);
    }

    async function runSandbox() {
      const ed = document.getElementById('sandboxEditor');
      const out = document.getElementById('sandboxConsole');
      const frame = document.getElementById('sandboxFrame');
      if (!ed || !out) return;
      const code = ed.value.trim();
      if (!code) { toast('Editor is empty — load a template or write some code', 'info'); return; }
      const btn = document.getElementById('sandboxRunBtn');
      if (btn) btn.disabled = true;
      const looksHtml = /^\s*<!doctype/i.test(code) || /^\s*<html/i.test(code) || (code.includes('<script') && code.includes('<\/script>'));
      const t0 = performance.now();
      try {
        if (looksHtml && sandboxMode !== 'terminal') {
          if (frame) frame.srcdoc = code;
          sandboxSetOutput('preview');
          out.textContent = '▶ HTML rendered in the Preview tab. Edit the code and Run again to refresh.';
          sandboxStatus('rendered · preview', 'ok');
          return;
        }
        sandboxSetOutput('console');
        sandboxStatus('running…', 'running');
        out.textContent = '▶ Running…';
        if (sandboxMode === 'terminal') {
          const lines = code.split('\n').map(l => l.trim()).filter(Boolean);
          const chunks = [];
          let bad = false;
          for (const line of lines) {
            const data = await sandboxExec(line.startsWith('!') ? line : '!' + line, 'terminal');
            if (data.cwd && document.getElementById('terminalPromptLabel')) {
              document.getElementById('terminalPromptLabel').textContent = `PS ${data.cwd}>`;
            }
            const chunk = [data.stdout, data.stderr].filter(Boolean).join('\n');
            if (chunk) chunks.push(`$ ${line.replace(/^!/, '')}\n${chunk}`);
            if (data.returncode && data.returncode !== 0) bad = true;
          }
          const ms = Math.round(performance.now() - t0);
          out.textContent = chunks.join('\n\n') || '(no output)';
          sandboxStatus(`${bad ? 'exit≠0' : 'exit 0'} · ${ms}ms`, bad ? 'err' : 'ok');
          return;
        }
        const data = await sandboxExec(code, 'auto');
        const ms = Math.round(performance.now() - t0);
        const body = [data.stdout, data.stderr ? '[stderr]\n' + data.stderr : ''].filter(Boolean).join('\n');
        out.textContent = body || '(no output)';
        const bad = Boolean(data.stderr) || (typeof data.returncode === 'number' && data.returncode !== 0) || data.ok === false;
        sandboxStatus(`${bad ? 'error' : 'exit 0'} · ${ms}ms`, bad ? 'err' : 'ok');
      } catch (err) {
        out.textContent = 'Request failed: ' + err.message;
        sandboxStatus('failed', 'err');
        toast('Sandbox request failed', 'err');
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function loadBenchmarkModels() {
      const grid = document.getElementById('benchmarkGrid');
      if (!grid) return;
      try {
        const res = await fetch('/api/benchmark/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'list' })
        });
        const data = await res.json();
        if (data.models && data.models.length) {
          grid.innerHTML = data.models.map(m => `
            <div class="lab-panel" style="padding:14px;">
              <div style="font-weight:700;color:#38bdf8;">${m.tier} — ${m.filename}</div>
              <div class="sub-metric">SSD Size: ${m.size}</div>
            </div>`).join('');
        }
      } catch (e) {}
    }

    async function runBenchmark(mode) {
      const grid = document.getElementById('benchmarkGrid');
      if (!grid) return;
      grid.innerHTML = '<div class="sub-metric">⏳ Running live Speed &amp; Accuracy benchmark...</div>';
      const sel = document.getElementById('brainSelect') ? document.getElementById('brainSelect').value : 'all';
      const target = mode === 'selected' && sel !== 'auto' ? sel : 'all';
      const res = await fetch('/api/benchmark/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target })
      });
      const data = await res.json();
      grid.innerHTML = (data.results || []).map(r => `
        <div class="lab-panel" style="padding:14px;">
          <div style="font-weight:800;color:#38bdf8;margin-bottom:6px;">${r.tier} — ${r.filename}</div>
          <div class="sub-metric">SSD Size: ${r.size}</div>
          <div style="margin:6px 0;font-size:0.85rem;">⚡ TTFT: <strong>${r.ttft_ms}ms</strong> • 🚀 ${
            r.demo ? 'Speed: <strong>instant</strong> (template, not tokens)' :
            `Speed: <strong>${r.tok_per_sec} tok/s</strong> • ⏱️ ${r.ms_per_token} ms/tok`}</div>
          <div style="font-size:0.82rem;margin-bottom:4px;">Accuracy: <strong>${r.accuracy_score}%</strong>${
            r.checks ? ` <span class="sub-metric">(${r.checks} checks passed)</span>` : ''}</div>
          <div class="progress-bar"><div class="progress-fill" style="width:${r.accuracy_score}%;"></div></div>
        </div>`).join('') || '<div class="sub-metric">No results.</div>';
    }

    async function runLaptopAudit() {
      const out = document.getElementById('auditOutput');
      out.textContent = 'Running offline laptop security audit...';
      try {
        const res = await fetch('/api/security/audit', { method: 'POST' });
        const data = await readJsonSafe(res);
        const analysis = data.ai_analysis || data.report || 'No analysis returned.';
        out.textContent = `— ANALYSIS (${data.model || 'static'}) —\n${analysis}\n\n` +
          `— RAW DIAGNOSTICS —\n${data.raw_report || ''}`;
        toast('Device audit finished', 'ok');
      } catch (err) {
        out.textContent = `Audit failed: ${err.message}`;
        toast('Audit failed', 'err');
      }
    }

    async function scanCode() {
      const code = document.getElementById('codeInput').value;
      const out = document.getElementById('sastOutput');
      if (!code.trim()) { toast('Paste some code first', 'info'); return; }
      out.textContent = 'Scanning code offline...';
      try {
        const res = await fetch('/api/security/sast', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code })
        });
        const data = await readJsonSafe(res);
        const findings = data.findings || [];
        if (!findings.length) {
          out.textContent = '✅ No issues matched the offline ruleset.\n\n' +
            (data.neural_review || data.static_summary || '');
          toast('Scan finished — no findings', 'ok');
          return;
        }
        const order = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
        const sorted = [...findings].sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
        out.textContent =
          `⚠️ ${findings.length} finding(s) — worst first\n\n` +
          sorted.map(f =>
            `[${f.severity}] line ${f.line} · ${f.type}\n    ${f.code}\n    fix: ${f.fix}`
          ).join('\n\n');
        const critical = findings.filter(f => f.severity === 'CRITICAL').length;
        toast(`${findings.length} finding(s)${critical ? ` · ${critical} critical` : ''}`, critical ? 'err' : 'info');
      } catch (err) {
        out.textContent = `Scan failed: ${err.message}`;
        toast('SAST scan failed', 'err');
      }
    }

    async function patchCode() {
      const code = document.getElementById('codeInput').value;
      const out = document.getElementById('sastOutput');
      out.textContent = 'Auto-patching vulnerabilities...';
      const res = await fetch('/api/security/patch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });
      const data = await readJsonSafe(res);
      const patched = data.patched_code || '';
      if (!patched) {
        out.textContent = 'No patch produced. A local GGUF model is required for AI auto-patching;\n' +
          'the rules engine still reports findings in the scan above.';
        toast('Auto-patch needs a local model', 'info');
        return;
      }
      out.textContent =
        `--- AUTO-PATCH (${data.model || 'local model'}) ---\n\n${patched}`;
      document.getElementById('codeInput').value = patched;
      toast('Patched code written into the editor', 'ok');
    }

    async function searchCVE() {
      const query = document.getElementById('cveQuery').value.trim();
      const out = document.getElementById('cveOutput');
      if (!query) return;
      out.textContent = 'Searching offline CVE Vault...';
      const res = await fetch('/api/security/cve_search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      });
      const data = await readJsonSafe(res);
      const hits = data.results || [];
      if (!hits.length) {
        out.textContent = 'No vault matches. Try “sql injection”, “xss”, “cve-2021”, or download the datasets first.';
        return;
      }
      out.textContent = hits.map((hit, i) => {
        if (typeof hit === 'string') return `${i + 1}. ${hit}`;
        const title = hit.question || hit.title || hit.cve || hit.id || 'match';
        const body = hit.answer || hit.description || hit.summary || '';
        return `${i + 1}. ${title}\n   ${String(body).slice(0, 300)}`;
      }).join('\n\n');
    }

    async function downloadCVE() {
      const out = document.getElementById('cveOutput');
      out.textContent = 'Downloading & indexing CVE/Security datasets into SSD ROM...';
      const res = await fetch('/api/security/cve_download', { method: 'POST' });
      const data = await res.json();
      out.textContent = `Downloaded & indexed ${data.inserted} security/CVE records into SSD ROM.`;
    }



    let editorSourceUrl = '';
    let editorStack = [];
    let editorRedoStack = [];

    async function loadEditorPicker() {
      const picker = document.getElementById('editorPicker');
      if (!picker) return;
      const res = await fetch('/api/images');
      const data = await res.json();
      picker.innerHTML = '<option value="">-- Pick from SSD Gallery --</option>';
      (data.images || []).forEach(img => {
        if (!img.filename.endsWith('.svg')) {
          const opt = document.createElement('option');
          opt.value = img.url;
          opt.textContent = `${img.prompt.slice(0, 32)} (${img.filename})`;
          picker.appendChild(opt);
        }
      });
      if (!editorSourceUrl && picker.options.length > 1) {
        picker.selectedIndex = 1;
        selectEditorImage(picker.value);
      }
    }

    function selectEditorImage(url) {
      if (!url) return;
      editorSourceUrl = url;
      editorStack = [url];
      editorRedoStack = [];
      showEditorPreview(url, url);
    }

    function showEditorPreview(beforeUrl, afterUrl) {
      document.getElementById('splitWrap').style.display = 'block';
      document.getElementById('splitSlider').style.display = 'block';
      document.getElementById('editorBeforeImg').src = beforeUrl;
      document.getElementById('editorAfterImg').src = afterUrl;
      const dl = document.getElementById('editorDownloadBtn');
      dl.href = afterUrl;
      dl.download = afterUrl.split('/').pop();
      dl.style.display = 'inline-block';
      updateSplit(document.getElementById('splitSlider').value);
    }

    function updateSplit(val) {
      document.getElementById('editorAfterImg').style.clipPath = `inset(0 0 0 ${val}%)`;
    }

    function uploadEditorImage(file) {
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async () => {
        const res = await fetch('/api/image/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data_url: reader.result, filename: file.name })
        });
        const data = await res.json();
        selectEditorImage(data.url);
        document.getElementById('editorStatus').textContent = `Loaded: ${data.filename}`;
        loadEditorPicker();
      };
      reader.readAsDataURL(file);
    }

    async function runImageEdit(presetCmd = null) {
      const cmdInput = document.getElementById('editorCommand');
      const command = presetCmd || cmdInput.value.trim();
      if (!editorSourceUrl) {
        document.getElementById('editorStatus').textContent = 'Please upload or select an image first.';
        return;
      }
      if (!command) return;
      document.getElementById('editorStatus').textContent = `Applying "${command}"...`;
      const res = await fetch('/api/image/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_url: editorSourceUrl, command })
      });
      const data = await res.json();
      if (data.error) {
        document.getElementById('editorStatus').textContent = `Error: ${data.error}`;
        return;
      }
      editorStack.push(data.url);
      editorRedoStack = [];
      showEditorPreview(editorSourceUrl, data.url);
      document.getElementById('editorStatus').textContent = `✅ ${data.mode} in ${data.latency_ms}ms (${data.filename})`;
    }

    function undoEdit() {
      if (editorStack.length <= 1) return;
      editorRedoStack.push(editorStack.pop());
      const cur = editorStack[editorStack.length - 1];
      showEditorPreview(editorSourceUrl, cur);
    }

    function redoEdit() {
      if (editorRedoStack.length === 0) return;
      const next = editorRedoStack.pop();
      editorStack.push(next);
      showEditorPreview(editorSourceUrl, next);
    }

    function useEditedAsSource() {
      if (editorStack.length === 0) return;
      editorSourceUrl = editorStack[editorStack.length - 1];
      showEditorPreview(editorSourceUrl, editorSourceUrl);
      document.getElementById('editorStatus').textContent = 'Chaining edits onto current result.';
    }

    function _decodeCodeB64(b64) {
      const raw = decodeURIComponent(escape(atob(b64)));
      return raw.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    }

    function copyCodeBlock(btn, b64) {
      navigator.clipboard.writeText(_decodeCodeB64(b64));
      btn.textContent = '✓ Copied';
      setTimeout(() => btn.textContent = '📋 Copy', 1500);
    }

    async function runCodeBlock(btn, b64, label = 'PYTHON') {
      const code = _decodeCodeB64(b64);
      const wrap = btn.closest('.code-block-wrap');
      const outEl = wrap.querySelector('.sandbox-out');
      outEl.style.display = 'block';
      if (label === 'TERMINAL') {
        outEl.style.color = '#94a3b8';
        outEl.textContent = '⏳ Running in Read-Only Terminal...';
        const lines = code.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#') && !l.startsWith('REM'));
        const outputs = [];
        for (const cmdLine of lines) {
          const res = await fetch('/api/sandbox_run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'terminal', code: cmdLine })
          });
          const data = await res.json();
          outputs.push(`$ ${cmdLine}\n${data.stdout || data.stderr || '(OK)'}`);
        }
        outEl.style.color = '#34d399';
        outEl.textContent = outputs.join('\n\n');
        return;
      }
      const trimmed = code.trim().toLowerCase();
      if (trimmed.startsWith('<!doctype html') || trimmed.startsWith('<html') || trimmed.includes('<canvas')) {
        outEl.innerHTML = '';
        const iframe = document.createElement('iframe');
        iframe.setAttribute('sandbox', 'allow-scripts');
        iframe.style.cssText = 'width:100%;height:420px;border:1px solid #38bdf8;border-radius:8px;background:#090d16;';
        iframe.srcdoc = code;
        outEl.appendChild(iframe);
        return;
      }
      outEl.style.color = '#94a3b8';
      outEl.textContent = '⏳ Running in isolated Read-Only sandbox...';
      try {
        const res = await fetch('/api/sandbox_run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'python', code })
        });
        const data = await res.json();
        outEl.style.color = data.ok ? '#34d399' : '#f87171';
        const body = [data.stdout, data.stderr].filter(Boolean).join('\n') || '(No output)';
        outEl.textContent = `[${data.ok ? 'OK' : 'ERROR'} | ${data.elapsed_ms}ms]\n${body}`;
      } catch (e) {
        outEl.style.color = '#f87171';
        outEl.textContent = 'Sandbox request failed: ' + e;
      }
    }

    /* ---------------- sandbox studio ---------------- */
    let sandboxMode = 'code';
    let sandboxOutputMode = 'console';

    function sandboxStatus(text, kind) {
      const el = document.getElementById('sandboxStatus');
      if (!el) return;
      el.textContent = text;
      el.className = 'run-status ' + (kind || 'idle');
    }

    function sandboxUpdateLineNums() {
      const ed = document.getElementById('sandboxEditor');
      const nums = document.getElementById('sandboxLineNums');
      if (!ed || !nums) return;
      const n = Math.max(1, ed.value.split('\n').length);
      let out = '';
      for (let i = 1; i <= n; i++) out += i + '\n';
      nums.textContent = out.trimEnd();
      nums.scrollTop = ed.scrollTop;
    }

    function sandboxSetOutput(which) {
      sandboxOutputMode = which;
      const consoleEl = document.getElementById('sandboxConsole');
      const frame = document.getElementById('sandboxFrame');
      const tabC = document.getElementById('outTabConsole');
      const tabP = document.getElementById('outTabPreview');
      if (tabC) tabC.classList.toggle('active', which === 'console');
      if (tabP) tabP.classList.toggle('active', which === 'preview');
      if (consoleEl) consoleEl.style.display = which === 'console' ? 'block' : 'none';
      if (frame) frame.style.display = which === 'preview' ? 'block' : 'none';
    }

    function sandboxSetMode(mode) {
      sandboxMode = mode === 'terminal' ? 'terminal' : 'code';
      document.getElementById('sandboxModeCode')?.classList.toggle('active', sandboxMode === 'code');
      document.getElementById('sandboxModeTerm')?.classList.toggle('active', sandboxMode === 'terminal');
      document.getElementById('sandboxModeCode')?.setAttribute('aria-selected', sandboxMode === 'code' ? 'true' : 'false');
      document.getElementById('sandboxModeTerm')?.setAttribute('aria-selected', sandboxMode === 'terminal' ? 'true' : 'false');
      const ed = document.getElementById('sandboxEditor');
      const file = document.getElementById('sandboxFileName');
      const meta = document.getElementById('sandboxEditorMeta');
      if (!ed) return;
      if (sandboxMode === 'terminal') {
        if (!ed.value.trim().startsWith('!')) ed.value = '!whoami';
        if (file) file.textContent = 'shell';
        if (meta) meta.textContent = 'read-only commands';
        ed.placeholder = '!command — one read-only command per line (ls, pwd, python --version)';
        toast('Shell mode — each line runs read-only in the sandbox', 'info', 2400);
      } else {
        ed.value = ed.value.replace(/^!/gm, '');
        if (file) file.textContent = 'main.py';
        if (meta) meta.textContent = 'python · utf-8';
        ed.placeholder = '# Python or HTML5/JS — then Run (Ctrl+Enter)';
      }
      sandboxUpdateLineNums();
      ed.focus();
    }

    function clearSandbox() {
      const ed = document.getElementById('sandboxEditor');
      const out = document.getElementById('sandboxConsole');
      const frame = document.getElementById('sandboxFrame');
      if (ed) ed.value = '';
      if (out) out.textContent = 'Cleared. Write code above and press Run.';
      if (frame) frame.srcdoc = '';
      sandboxSetOutput('console');
      sandboxStatus('idle', 'idle');
      sandboxUpdateLineNums();
    }

    function sandboxFillCommand(cmd, autoRun) {
      const input = document.getElementById('terminalCmdInput');
      if (input) { input.value = cmd; input.focus(); }
      sandboxSetOutput('console');
      if (autoRun) runTerminalCommand(cmd);
    }

    function renderSandboxQuickCmds(platform) {
      const host = document.getElementById('sandboxQuickCmds');
      if (!host) return;
      const win = String(platform || navigator.platform || '').toLowerCase().includes('win');
      const cmds = win
        ? [['$ whoami', 'whoami'], ['$ dir', 'dir'], ['$ ipconfig', 'ipconfig'], ['$ tasklist', 'tasklist']]
        : [['$ whoami', 'whoami'], ['$ ls -la', 'ls -la'], ['$ ip addr', 'ip addr'], ['$ ps aux', 'ps aux']];
      host.innerHTML = '<span class="tpl-hint">Quick shell</span>' + cmds
        .map(([label, cmd]) => `<button type="button" class="tpl-chip" onclick="sandboxFillCommand('${cmd.replace(/'/g, "\\'")}', true)">${label}</button>`)
        .join('') +
        `<button type="button" class="tpl-chip" onclick="sandboxFillCommand('!python3 -c \\'print(2**10)\\'' , true)">🐍 Python</button>`;
    }

    function ideRenderTabs() {
      const bar = document.getElementById('ideTabs');
      bar.innerHTML = '';
      ideOpenTabs.forEach(t => {
        const tab = document.createElement('div');
        const active = t.path === ideActivePath;
        tab.style.cssText = `padding:6px 10px;font-size:0.78rem;cursor:pointer;display:flex;align-items:center;gap:6px;border-right:1px solid #2d2d2d;background:${active ? '#1e1e1e' : '#2d2d2d'};color:${active ? '#fff' : '#9cdcfe'};`;
        tab.onclick = () => { ideActivePath = t.path; ideRenderTabs(); };
        tab.innerHTML = `<span>${t.dirty ? '● ' : ''}${t.name}</span><span onclick="event.stopPropagation();ideCloseTab('${t.path.replace(/\\/g, '\\\\')}')" style="color:#858585;">×</span>`;
        bar.appendChild(tab);
      });
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const ed = document.getElementById('ideEditor');
      ed.value = cur ? cur.content : '';
      document.getElementById('ideActivePath').textContent = cur ? cur.path : 'No file open';
      ideUpdateLineNums();
      if (cur && cur.name.toLowerCase().endsWith('.html')) {
        document.getElementById('idePreviewFrame').srcdoc = cur.content;
      }
    }

    function ideCloseTab(path) {
      ideOpenTabs = ideOpenTabs.filter(t => t.path !== path);
      if (ideActivePath === path) ideActivePath = ideOpenTabs.length ? ideOpenTabs[0].path : null;
      ideRenderTabs();
    }

    function ideOnEdit() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (cur) {
        cur.content = document.getElementById('ideEditor').value;
        cur.dirty = true;
        ideRenderTabs();
      }
      ideUpdateLineNums();
    }

    function ideUpdateLineNums() {
      const lines = document.getElementById('ideEditor').value.split('\n').length;
      document.getElementById('ideLineNums').textContent = Array.from({length: lines}, (_, i) => i + 1).join('\n');
    }

    function ideUpdateCursor() {
      const ed = document.getElementById('ideEditor');
      const upTo = ed.value.substring(0, ed.selectionStart).split('\n');
      document.getElementById('ideCursorPos').textContent = `Ln ${upTo.length}, Col ${upTo[upTo.length - 1].length + 1}`;
    }

    async function ideSaveActive() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur) return;
      const res = await fetch('/api/ide/files', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action:'save', path: cur.path, content: cur.content }) });
      const data = await res.json();
      if (data.ok) { cur.dirty = false; ideRenderTabs(); }
    }

    function ideTermKey(e) {
      const inp = document.getElementById('ideTermInput');
      if (e.key === 'Enter') {
        const cmd = inp.value.trim();
        if (!cmd) return;
        ideTermHistory.push(cmd);
        ideTermHistIdx = ideTermHistory.length;
        inp.value = '';
        ideRunTerminal(cmd);
      } else if (e.key === 'ArrowUp' && ideTermHistIdx > 0) {
        ideTermHistIdx--;
        inp.value = ideTermHistory[ideTermHistIdx];
      } else if (e.key === 'ArrowDown' && ideTermHistIdx < ideTermHistory.length - 1) {
        ideTermHistIdx++;
        inp.value = ideTermHistory[ideTermHistIdx];
      }
    }

    async function ideRunTerminal(cmd = null, runFile = null) {
      const out = document.getElementById('ideTermOut');
      const res = await fetch('/api/ide/terminal', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ cmd, run_file: runFile }) });
      const data = await res.json();
      document.getElementById('ideTermCwd').textContent = data.cwd || '';
      if (data.stderr) ideLastError = data.stderr;
      const text = [cmd ? `PS> ${cmd}` : `▶ Running ${runFile}`, data.stdout, data.stderr].filter(Boolean).join('\n');
      out.textContent += (out.textContent ? '\n' : '') + text + '\n';
      out.scrollTop = out.scrollHeight;
    }

    async function ideRunActiveFile() {
      if (!ideActivePath) return;
      await ideSaveActive();
      ideRunTerminal(null, ideActivePath);
    }

    function ideSwitchRight(which) {
      document.getElementById('ideAiPane').style.display = which === 'ai' ? 'flex' : 'none';
      document.getElementById('idePreviewPane').style.display = which === 'preview' ? 'flex' : 'none';
      document.getElementById('ideRightAiBtn').style.background = which === 'ai' ? '#094771' : '';
      document.getElementById('ideRightPrevBtn').style.background = which === 'preview' ? '#094771' : '';
    }


    /* ---------------- settings (benchmark / engine / workspace) ---------------- */
    let settingsLoadedAt = 0;

    function openSettings(pane = 'benchmark') {
      const modal = document.getElementById('settingsModal');
      if (!modal) return;
      modal.classList.add('open');
      document.body.classList.add('modal-open');
      settingsTab(pane);
      loadSettings(true);
      setTimeout(() => document.getElementById('settingsBtn')?.focus(), 40);
    }

    function closeSettings() {
      const modal = document.getElementById('settingsModal');
      if (!modal) return;
      modal.classList.remove('open');
      document.body.classList.remove('modal-open');
    }

    function settingsTab(pane) {
      document.querySelectorAll('.settings-tab').forEach(btn => {
        const on = btn.dataset.pane === pane;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      document.querySelectorAll('.settings-pane').forEach(sec => {
        sec.classList.toggle('active', sec.id === 'setPane-' + pane);
      });
    }

    function setRow(label, value) {
      return `<div class="set-row"><span>${label}</span><span>${value}</span></div>`;
    }

    async function loadSettings(force = false) {
      const now = Date.now();
      if (!force && now - settingsLoadedAt < 1500) return;
      settingsLoadedAt = now;
      try {
        const status = await (await fetch('/api/status')).json();
        const brain = status.demo_brain || status.brains || {};
        const storage = status.storage || {};
        const harvester = status.harvester || {};

        document.getElementById('setTopics').textContent = brain.knowledge_topics ?? '—';
        document.getElementById('setRecipes').textContent = brain.code_recipes ?? '—';

        document.getElementById('setEngine').innerHTML = [
          setRow('Engine', brain.name || 'Argon Demo Brain'),
          setRow('Version', brain.version || '—'),
          setRow('Weights installed', brain.active ? 'yes' : 'none (demo brain)'),
          setRow('Knowledge topics', brain.knowledge_topics ?? '—'),
          setRow('Code recipes', brain.code_recipes ?? '—'),
          setRow('Memory facts', brain.memory_facts ?? 0),
          setRow('Last measured latency', (brain.latency_ms ? brain.latency_ms.toFixed(2) + ' ms' : 'idle')),
        ].join('');
        document.getElementById('setEngine').insertAdjacentHTML('beforeend',
          `<p class="set-note" style="margin-top:8px;">${brain.note || 'Answers come from local computation, a curated knowledge base and code recipes.'}</p>`);

        document.getElementById('setWorkspace').innerHTML = [
          setRow('ROM database', storage.db_path || 'argon_rom.db'),
          setRow('Database size', (storage.db_size_mb ?? 0) + ' MB'),
          setRow('Rows stored', (storage.total_rows ?? 0).toLocaleString()),
          setRow('Free disk', (storage.free_disk_gb ?? 0) + ' GB'),
          setRow('Storage guard', storage.safe ? 'ok' : 'paused (low disk)'),
          setRow('Harvester', harvester.status || 'idle'),
        ].join('');
      } catch (err) {
        document.getElementById('setEngine').innerHTML = `<div class="set-note">Could not read settings: ${err.message}</div>`;
      }
      await settingsRunBenchmark(true);
    }

    async function settingsRunBenchmark(quiet = false) {
      const btn = document.getElementById('setBenchRun');
      const note = document.getElementById('setBenchStatus');
      if (btn) { btn.disabled = true; btn.textContent = '⏳ Running…'; }
      try {
        const res = await fetch('/api/benchmark/run', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target: 'all' })
        });
        const data = await readJsonSafe(res);
        const row = (data.results || [])[0] || {};
        document.getElementById('setAcc').textContent = `${row.accuracy_score ?? 0}% (${row.checks || '0/0'})`;
        document.getElementById('setLat').textContent = `${row.ttft_ms ?? 0} ms`;
        const checks = row.check_detail || [];
        const host = document.getElementById('setChecks');
        if (checks.length) {
          host.innerHTML = checks.map(c =>
            `<div class="set-check ${c.ok ? 'pass' : 'fail'}"><span class="mark">${c.ok ? '✓' : '✕'}</span>` +
            `<span>${c.name}</span><span class="prompt">${c.prompt}</span></div>`).join('');
        } else {
          host.innerHTML = '<div class="set-note">This engine reports a score only — install a GGUF for per-check detail.</div>';
        }
        if (note) {
          const passed = checks.filter(c => c.ok).length || row.checks_passed || 0;
          const total = checks.length || row.checks_total || 0;
          note.textContent = quiet
            ? `${passed}/${total} checks passed · measured just now`
            : `Re-ran ${total} checks · ${row.ttft_ms} ms per turn`;
        }
        toast(`Benchmark: ${row.checks || ''} checks passed`, 'ok', 2000);
      } catch (err) {
        if (note) note.textContent = 'Benchmark failed: ' + err.message;
        toast('Benchmark failed', 'err');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '▶ Run benchmark'; }
      }
    }

    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === ',') { e.preventDefault(); openSettings(); }
      if (e.key === 'Escape') closeSettings();
    });

    /* ---------------- coding agent: Auto / Turbo / Request review ---------------- */
    let agentMode = 'auto';
    let agentToken = null;
    let agentBusy = false;

    const AGENT_MODE_HINT = {
      auto: 'Auto — the agent plans, edits and runs, then shows you the diff.',
      turbo: 'Turbo — applies immediately with minimal narration.',
      review: 'Request review — proposes the diff and waits for your approval.'
    };

    function agentSetMode(mode) {
      agentMode = mode;
      document.querySelectorAll('.agent-mode').forEach(btn => {
        const on = btn.dataset.mode === mode;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      toast(AGENT_MODE_HINT[mode] || mode, 'info', 2200);
    }

    function agentKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        agentRun();
      }
    }

    function agentQuick(prompt) {
      const el = document.getElementById('agentPrompt');
      if (el) el.value = prompt;
      agentRun();
    }

    function agentActiveTab() {
      return ideOpenTabs.find(t => t.path === ideActivePath) || null;
    }

    function agentClearEmpty() {
      const empty = document.querySelector('#agentTranscript .agent-empty');
      if (empty) empty.remove();
    }

    function agentAppend(html, className) {
      agentClearEmpty();
      const host = document.getElementById('agentTranscript');
      const wrap = document.createElement('div');
      if (className) wrap.className = className;
      wrap.innerHTML = html;
      host.appendChild(wrap);
      host.scrollTop = host.scrollHeight;
      return wrap;
    }

    function agentStepRow(step) {
      const icon = { edit: '✏️', note: '📄', run: '▶', read: '👁', error: '⚠️' }[step.kind] || '•';
      const head = document.createElement('div');
      head.className = 'agent-step-head';
      const iconEl = document.createElement('span');
      iconEl.className = 'agent-step-icon';
      iconEl.textContent = icon;
      const title = document.createElement('span');
      title.className = 'agent-step-title';
      title.textContent = step.title || step.kind || 'step';
      head.appendChild(iconEl);
      head.appendChild(title);
      if (step.status) {
        const chip = document.createElement('span');
        chip.className = 'agent-status ' + step.status;
        chip.textContent = step.status;
        head.appendChild(chip);
      }
      const row = document.createElement('div');
      row.className = 'agent-step' + (step.kind === 'error' ? ' error' : '');
      row.appendChild(head);
      if (step.path) {
        const path = document.createElement('div');
        path.className = 'agent-step-path';
        path.textContent = step.path;
        row.appendChild(path);
      }
      if (step.notes) {
        const body = document.createElement('div');
        body.className = 'agent-step-body';
        body.innerHTML = renderRichMarkdown(step.notes);
        row.appendChild(body);
      }
      if (step.diff) {
        const pre = document.createElement('pre');
        pre.className = 'agent-diff';
        pre.textContent = step.diff;
        row.appendChild(pre);
      }
      agentClearEmpty();
      const host = document.getElementById('agentTranscript');
      host.appendChild(row);
      host.scrollTop = host.scrollHeight;
      return row;
    }

    async function agentReloadFile(path) {
      const tab = ideOpenTabs.find(t => t.path === path);
      if (!tab) return;
      try {
        const res = await fetch('/api/ide/files', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'read', path })
        });
        const data = await readJsonSafe(res);
        if (typeof data.content === 'string') {
          tab.content = data.content;
          tab.dirty = false;
        }
        ideRenderTabs();
      } catch (e) {}
    }

    async function agentRun() {
      if (agentBusy) return;
      const input = document.getElementById('agentPrompt');
      const prompt = (input && input.value || '').trim();
      if (!prompt) { toast('Tell the agent what to do first', 'info'); return; }

      agentBusy = true;
      agentToken = null;
      const sendBtn = document.getElementById('agentSend');
      if (sendBtn) sendBtn.disabled = true;
      const reviewCard = document.getElementById('agentReview');
      if (reviewCard) reviewCard.style.display = 'none';

      const cur = agentActiveTab();
      agentAppend(`<div class="agent-step-head"><span class="agent-step-icon">🧠</span><span class="agent-step-title">Working in ${agentMode} mode…</span></div>`, 'agent-step');

      try {
        const res = await fetch('/api/ide/agent', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt,
            mode: agentMode,
            path: cur ? cur.path : null,
            cwd: ideCwd || null,
            content: cur ? cur.content : null
          })
        });
        const data = await readJsonSafe(res);
        const host = document.getElementById('agentTranscript');
        if (host && host.lastElementChild && /Working in/.test(host.lastElementChild.textContent)) {
          host.removeChild(host.lastElementChild);
        }
        if (!data.ok) {
          agentStepRow({ kind: 'error', title: data.error || 'The agent could not complete that.' });
          toast(data.error || 'Agent failed', 'err');
          return;
        }
        if (Array.isArray(data.plan) && data.plan.length) {
          agentAppend(`<div class="agent-plan">${data.plan.map((p, i) => `${i + 1}. ${p}`).join('  ·  ')}</div>`);
        }
        (data.steps || []).forEach(agentStepRow);
        (data.touched || []).forEach(p => agentReloadFile(p));

        if (data.awaiting_review && data.staged_id) {
          agentToken = data.staged_id;
          const diffHost = document.getElementById('agentDiff');
          diffHost.textContent = (data.steps || []).map(x => x.diff).filter(Boolean).join('\n\n') || '(no textual diff)';
          document.getElementById('agentReviewMeta').textContent = `· ${data.ms} ms`;
          reviewCard.style.display = 'flex';
          toast('Review the diff, then apply or reject', 'info');
        } else if (data.staged_id && (data.touched || []).length) {
          agentToken = data.staged_id;
          const undo = document.createElement('button');
          undo.className = 'agent-chip';
          undo.textContent = '↩ Undo this run';
          undo.onclick = agentUndo;
          const host = document.getElementById('agentTranscript');
          const row = document.createElement('div');
          row.className = 'agent-plan';
          row.appendChild(undo);
          host.appendChild(row);
          host.scrollTop = host.scrollHeight;
          toast('Agent applied the changes', 'ok');
        } else if ((data.steps || []).some(step => step.kind === 'note')) {
          toast('Agent finished', 'ok');
        }
        if (input) input.value = '';
      } catch (err) {
        agentStepRow({ kind: 'error', title: 'Agent request failed: ' + err.message });
        toast('Agent request failed', 'err');
      } finally {
        agentBusy = false;
        if (sendBtn) sendBtn.disabled = false;
      }
    }

    async function agentApprove() {
      if (!agentToken) return;
      const res = await fetch('/api/ide/agent/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: agentToken })
      });
      const data = await readJsonSafe(res);
      document.getElementById('agentReview').style.display = 'none';
      if (!data.ok) { toast(data.error || 'Could not apply', 'err'); return; }
      (data.touched || []).forEach(p => agentReloadFile(p));
      agentStepRow({ kind: 'note', title: 'Applied', notes: `Wrote ${(data.touched || []).length} file(s): ` +
        (data.touched || []).map(p => '`' + p.split('/').pop() + '`').join(', ') });
      toast('Changes applied', 'ok');
    }

    async function agentReject() {
      if (agentToken) {
        await fetch('/api/ide/agent/discard', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: agentToken })
        }).catch(() => {});
      }
      agentToken = null;
      document.getElementById('agentReview').style.display = 'none';
      agentStepRow({ kind: 'note', title: 'Rejected', notes: 'No files were written.' });
      toast('Changes discarded', 'info');
    }

    async function agentUndo() {
      if (!agentToken) { toast('Nothing to undo in this session', 'info'); return; }
      const res = await fetch('/api/ide/agent/revert', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: agentToken })
      });
      const data = await readJsonSafe(res);
      if (!data.ok) { toast(data.error || 'Undo failed', 'err'); return; }
      (data.reverted || []).forEach(p => agentReloadFile(p));
      agentStepRow({ kind: 'note', title: 'Reverted', notes: 'Restored the files to their previous contents.' });
      agentToken = null;
      toast('Reverted', 'ok');
    }

    // === VS CODE + ANTIGRAVITY IDE LOGIC ===
    let ideInitialized = false;
    let ideCwd = '';
    let ideParent = '';
    let ideOpenTabs = []; // { path, name, content, dirty }
    let ideActivePath = null;
    let ideTermHistory = [];
    let ideTermHistIdx = -1;
    let ideLastError = '';
    let ideLastAiCode = '';

    async function ideInit() {
      if (ideInitialized) return;
      ideInitialized = true;
      await ideLoadDir('');
      const ed = document.getElementById('ideEditor');
      ed.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
          e.preventDefault();
          ideSaveActive();
        } else if (e.key === 'Tab') {
          e.preventDefault();
          const s = ed.selectionStart, end = ed.selectionEnd;
          ed.value = ed.value.substring(0, s) + '    ' + ed.value.substring(end);
          ed.selectionStart = ed.selectionEnd = s + 4;
          ideOnEdit();
        }
      });
    }

    async function ideLoadDir(path = null) {
      const target = path !== null ? path : (document.getElementById('idePathInput').value.trim() || ideCwd);
      const res = await fetch('/api/ide/files?path=' + encodeURIComponent(target));
      const data = await res.json();
      ideCwd = data.cwd || target;
      ideParent = data.parent || ideCwd;
      document.getElementById('idePathInput').value = ideCwd;
      document.getElementById('ideTermCwd').textContent = ideCwd;
      const list = document.getElementById('ideFileList');
      list.innerHTML = '';
      (data.items || []).forEach(it => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:4px 6px;border-radius:5px;cursor:pointer;';
        row.onmouseenter = () => row.style.background = '#2a2d2e';
        row.onmouseleave = () => row.style.background = 'transparent';
        const label = document.createElement('span');
        label.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        label.textContent = (it.is_dir ? '📁 ' : '📄 ') + it.name;
        label.onclick = () => it.is_dir ? ideLoadDir(it.path) : ideOpenFile(it.path, it.name);
        const del = document.createElement('button');
        del.textContent = '🗑';
        del.title = 'Delete';
        del.style.cssText = 'background:transparent;border:none;color:#858585;cursor:pointer;font-size:0.72rem;';
        del.onclick = async (e) => {
          e.stopPropagation();
          await fetch('/api/ide/files', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action:'delete', path: it.path }) });
          ideLoadDir(ideCwd);
        };
        row.appendChild(label);
        row.appendChild(del);
        list.appendChild(row);
      });
    }

    function ideGoUp() { if (ideParent) ideLoadDir(ideParent); }

    async function ideCreateFile() {
      const name = prompt('New file name:');
      if (!name) return;
      const sep = ideCwd.endsWith('\\') ? '' : '\\';
      await fetch('/api/ide/files', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action:'create_file', path: ideCwd + sep + name }) });
      ideLoadDir(ideCwd);
    }

    async function ideCreateFolder() {
      const name = prompt('New folder name:');
      if (!name) return;
      const sep = ideCwd.endsWith('\\') ? '' : '\\';
      await fetch('/api/ide/files', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action:'create_folder', path: ideCwd + sep + name }) });
      ideLoadDir(ideCwd);
    }

    async function ideOpenFile(path, name) {
      const existing = ideOpenTabs.find(t => t.path === path);
      if (!existing) {
        const res = await fetch('/api/ide/files', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action:'read', path }) });
        const data = await res.json();
        if (data.error) { toast(data.error, 'err'); return; }
        ideOpenTabs.push({ path, name, content: data.content, dirty: false });
      }
      ideActivePath = path;
      ideRenderTabs();
    }

    async function loadVideoStudio() {
      const res = await fetch('/api/video/status');
      const data = await res.json();
      document.getElementById('videoQuotaText').textContent =
        `Models: ${data.models_gb} GB • Videos: ${data.videos_gb} GB • Free Budget: ${data.free_budget_gb} GB / ${data.budget_gb} GB`;
      document.getElementById('videoQuotaBar').style.width = `${Math.min(100, (data.used_gb / data.budget_gb) * 100)}%`;

      const mList = document.getElementById('videoModelsList');
      mList.innerHTML = '';
      (data.models || []).forEach(m => {
        const badge = m.installed
          ? `<span style="color:#34d399;font-size:0.74rem;">✅ Installed (${m.size_gb} GB)</span>`
          : (m.downloading
              ? `<span style="color:#38bdf8;font-size:0.74rem;">⏳ ${m.progress}% (${m.speed_mb_s} MB/s)</span>`
              : `<button class="btn-subtle" onclick="downloadVideoModel('${m.filename}')" style="font-size:0.72rem;padding:3px 8px;">📥 Download (${m.expected_gb} GB)</button>`);
        mList.insertAdjacentHTML('beforeend', `<div class="card" style="padding:8px;"><div style="font-size:0.78rem;font-weight:600;">${m.label}</div><div>${badge}</div></div>`);
      });

      const vGrid = document.getElementById('videoGrid');
      vGrid.innerHTML = '';
      (data.videos || []).forEach(v => {
        const isMp4 = v.filename.endsWith('.mp4');
        const media = isMp4
          ? `<video src="${v.url}" controls loop style="width:100%;border-radius:8px;"></video>`
          : `<img src="${v.url}" style="width:100%;border-radius:8px;" />`;
        vGrid.insertAdjacentHTML('beforeend', `<div class="gallery-card">${media}<div class="gallery-prompt">${v.filename} (${v.size_mb} MB)</div></div>`);
      });
    }

    async function downloadVideoModel(filename) {
      await fetch('/api/video/download', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ filename })
      });
      loadVideoStudio();
      setTimeout(loadVideoStudio, 2000);
    }

    async function runVideoGen(sourceImage = null, presetPrompt = null) {
      const prompt = presetPrompt || document.getElementById('videoPrompt').value.trim();
      if (!prompt) return;
      const engine = sourceImage ? 'i2v' : document.getElementById('videoEngine').value;
      const [w, h] = document.getElementById('videoRes').value.split('x').map(Number);
      document.getElementById('videoStatus').textContent = '⏳ Generating video in Pure SSD ROM Mode...';
      const res = await fetch('/api/video/generate', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ prompt, engine, width: w, height: h, source_image: sourceImage })
      });
      const data = await res.json();
      document.getElementById('videoStatus').textContent = `✅ ${data.engine} in ${data.latency_ms}ms (${data.filename})`;
      const wrap = document.getElementById('videoPlayerWrap');
      const vid = document.getElementById('videoPlayer');
      const webp = document.getElementById('videoWebpPlayer');
      wrap.style.display = 'block';
      if (data.filename.endsWith('.mp4')) {
        vid.style.display = 'block'; webp.style.display = 'none'; vid.src = data.url;
      } else {
        webp.style.display = 'block'; vid.style.display = 'none'; webp.src = data.url;
      }
      loadVideoStudio();
    }

    function animateImageToVideo(filename, promptText) {
      switchTab('video');
      runVideoGen(filename, promptText || 'cinematic motion');
    }

    renderHistory();
    sandboxUpdateLineNums();
    sandboxSetOutput('console');
    if (window.innerWidth <= 820) {
      document.getElementById('sidebar')?.classList.add('collapsed');
      const openBtn = document.getElementById('openSidebarBtn');
      if (openBtn) openBtn.style.display = 'flex';
    }
    try {
      const lastTab = localStorage.getItem('argon_last_tab');
      if (lastTab && lastTab !== 'chat' && document.getElementById('tab-' + lastTab)) switchTab(lastTab);
    } catch (e) {}
    openChatFromHash();
    setInterval(fetchTelemetry, 2000);
    initUiExtras();
  async function downloadFluxRom() {
    const el = document.getElementById('fluxDlProg');
    if (el) el.textContent = 'Starting download...';
    await fetch('/api/flux/download', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const timer = setInterval(async () => {
      try {
        const res = await fetch('/api/flux/status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const d = await res.json();
        const dl = d.download || {};
        if (el) {
          el.textContent = dl.error
            ? 'Error: ' + dl.error + ' (Switch top bar to 🌐 ONLINE to download)'
            : `${dl.current_file || ''} (${dl.file_index || 0}/${dl.total_files || 4}) - ${dl.progress_pct || 0}% (${dl.downloaded_mb || 0} MB)`;
        }
        if (!dl.downloading) {
          clearInterval(timer);
          if (dl.ready) fetchTelemetry();
        }
      } catch (e) { clearInterval(timer); }
    }, 1500);
  }

    /* ==================================================================
       ARGON v2 UX helpers: toasts, search, shortcuts, engine status
       ================================================================== */
    const WELCOME_HTML = `
      <div class="welcome-screen" id="welcomeScreen">
        <div class="welcome-orb">✦</div>
        <div class="welcome-title">What can I help with?</div>
        <div class="welcome-sub">100% offline — no account, no cloud. Runs on local compute, a curated knowledge base and 44 code recipes until you install weights.</div>
        <div class="welcome-badge" id="welcomeBadge">⚡ Local engine ready</div>
        <div class="suggestion-grid">
          <button class="suggestion-chip" data-icon="🧠" onclick="useSuggestion('Explain how mmap works in llama-server')">
            <span><strong>Explain mmap</strong><span>How local model memory mapping works</span></span>
          </button>
          <button class="suggestion-chip" data-icon="🐍" onclick="useSuggestion('Write a Python script to scan a directory for large files')">
            <span><strong>Write a Python script</strong><span>Scan a directory for large files</span></span>
          </button>
          <button class="suggestion-chip" data-icon="💾" onclick="useSuggestion('Remember that my favorite language is Python')">
            <span><strong>Save to memory</strong><span>Store a personal fact in SSD ROM</span></span>
          </button>
          <button class="suggestion-chip" data-icon="🛡️" onclick="useSuggestion('What are common SQL injection vulnerabilities and how to fix them?')">
            <span><strong>Security advice</strong><span>SQL injection patterns &amp; fixes</span></span>
          </button>
        </div>
      </div>`;
    function welcomeMarkup() { return WELCOME_HTML; }
    function toast(message, kind = 'info', ms = 3200) {
      const host = document.getElementById('toastHost');
      if (!host) return;
      const el = document.createElement('div');
      el.className = `toast ${kind}`;
      const icon = kind === 'ok' ? '✅' : kind === 'err' ? '⚠️' : 'ℹ️';
      el.innerHTML = `<span class="ico">${icon}</span><span></span>`;
      el.lastElementChild.textContent = message;
      host.appendChild(el);
      setTimeout(() => {
        el.classList.add('leaving');
        setTimeout(() => el.remove(), 220);
      }, ms);
    }
    async function copyToClipboard(text, label = 'Copied to clipboard') {
      try {
        await navigator.clipboard.writeText(text);
        toast(label, 'ok', 1600);
      } catch (_) {
        toast('Clipboard blocked by the browser', 'err');
      }
    }
    function filterHistory(query) {
      const q = String(query || '').trim().toLowerCase();
      const list = document.getElementById('historyList');
      if (!list) return;
      const match = (item) => {
        const title = (item.querySelector('.history-item-title')?.textContent || '').toLowerCase();
        const hit = !q || title.includes(q);
        item.style.display = hit ? '' : 'none';
        return hit;
      };
      const visible = [...list.querySelectorAll('.history-item')].filter(match).length;
      const archList = document.getElementById('archiveList');
      let archVisible = 0;
      if (archList) archVisible = [...archList.querySelectorAll('.history-item')].filter(match).length;
      const block = document.getElementById('archiveBlock');
      if (block) {
        const hasArch = document.getElementById('archiveCount')?.textContent !== '0';
        block.style.display = hasArch ? '' : 'none';
      }
      let empty = list.querySelector('.history-empty');
      if (!visible && q) {
        if (!empty) {
          empty = document.createElement('div');
          empty.className = 'history-empty';
          list.appendChild(empty);
        }
        empty.textContent = `No chats match “${query}”.`;
      } else if (empty) {
        empty.remove();
      }
      if (q && archVisible && archList) toggleArchiveList(true);
    }
    function updateComposerMeta() {
      const count = document.getElementById('charCount');
      if (count) {
        const n = promptInput.value.length;
        count.textContent = n ? `${n}` : '0';
        count.style.color = n > 4000 ? '#fca5a5' : '#6f8299';
      }
    }
    function isNearBottom() {
      const sc = document.getElementById('chatScroll');
      if (!sc) return true;
      return sc.scrollHeight - sc.scrollTop - sc.clientHeight < 140;
    }
    function updateScrollFab() {
      const fab = document.getElementById('scrollFab');
      if (!fab) return;
      fab.classList.toggle('show', !isNearBottom());
    }
    function jumpToBottom() {
      const sc = document.getElementById('chatScroll');
      if (sc) sc.scrollTo({ top: sc.scrollHeight, behavior: 'smooth' });
    }
    function openModelHelp() { openModels(); }

    /* ---------------- models panel ---------------- */
    let modelsPoll = null;

    function openModels() {
      const modal = document.getElementById('modelsModal');
      if (!modal) return;
      modal.classList.add('open');
      refreshModels();
      if (modelsPoll) clearInterval(modelsPoll);
      modelsPoll = setInterval(refreshModels, 2500);
    }

    function closeModels() {
      const modal = document.getElementById('modelsModal');
      if (modal) modal.classList.remove('open');
      if (modelsPoll) { clearInterval(modelsPoll); modelsPoll = null; }
    }

    async function refreshModels() {
      const list = document.getElementById('modelsList');
      if (!list) return;
      try {
        const res = await fetch('/api/models/download/status');
        const data = await readJsonSafe(res);
        const models = data.models || {};
        const names = Object.keys(models);
        if (!names.length) {
          list.innerHTML = '<div class="models-empty">The server returned no model catalog entries.</div>';
          return;
        }
        const running = Boolean(data.running);
        list.innerHTML = names.map(name => {
          const st = models[name] || {};
          const status = st.status || 'missing';
          const pct = st.total_mb > 0 ? Math.min(100, Math.round((st.downloaded_mb / st.total_mb) * 100)) : 0;
          const cls = status === 'ready' ? 'ready' : (status === 'downloading' || status === 'resolving') ? 'downloading' : 'missing';
          const label = status === 'ready' ? '✅ installed'
            : status === 'downloading' ? `⬇ downloading ${pct}%`
            : status === 'resolving' ? '⚙ resolving…' : 'not installed';
          const size = st.total_mb ? `${(st.total_mb / 1024).toFixed(2)} GB` : 'size unknown';
          const busy = running || status === 'downloading' || status === 'resolving';
          return `
            <div class="model-row">
              <div style="min-width:0;">
                <div class="m-name">${name}</div>
                <div class="m-meta">${size}${st.error ? ' · ⚠️ ' + st.error : ''}</div>
              </div>
              <div class="m-state">
                <span class="state-pill ${cls}">${label}</span>
                <button onclick="downloadModel('${name}', this)" ${status === 'ready' || busy ? 'disabled' : ''}>
                  ${status === 'ready' ? 'installed' : '⬇ Download'}
                </button>
              </div>
            </div>`;
        }).join('');
        const foot = document.getElementById('modelsFootNote');
        if (foot && running) {
          foot.textContent = 'Download in progress — this panel refreshes every couple of seconds.';
        }
      } catch (err) {
        list.innerHTML = `<div class="models-empty">Could not read the model catalog (${err.message || 'network error'}).</div>`;
      }
    }

    async function downloadModel(name, btn) {
      if (btn) { btn.disabled = true; btn.textContent = 'starting…'; }
      try {
        const res = await fetch('/api/models/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: name })
        });
        const data = await readJsonSafe(res);
        if (res.ok) {
          toast(`Download started for ${name}`, 'ok');
        } else {
          toast(data.error || 'Download could not start', 'err');
        }
      } catch (err) {
        toast(`Download failed: ${err.message}`, 'err');
      }
      refreshModels();
    }

    function downloadRecommendedModels() {
      const wanted = ['gemma-3-1b-it-Q4_K_M.gguf', 'qwen2.5-coder-7b-instruct-q4_k_m.gguf'];
      fetch('/api/models/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models: wanted })
      })
        .then(r => r.json())
        .then(() => { toast('Downloading the ~5.5 GB starter set', 'ok'); refreshModels(); })
        .catch(err => toast(`Could not start download: ${err.message}`, 'err'));
    }
    function closeSidebarOnMobile() {
      if (window.innerWidth > 820) return;
      const sb = document.getElementById('sidebar');
      const overlay = document.getElementById('sidebarOverlay');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (overlay) overlay.classList.remove('show');
      if (openBtn) openBtn.style.display = 'flex';
    }
    function applyEngineStatus(data) {
      const demo = data.demo_brain || {};
      const models = data.models || {};
      const chip = document.getElementById('demoChip');
      const banner = document.getElementById('demoBanner');
      const badge = document.getElementById('welcomeBadge');
      const engineState = document.getElementById('engineState');
      const engineDetail = document.getElementById('engineDetail');
      const composerStatus = document.getElementById('composerStatus');
      const demoActive = Boolean(demo.active);
      if (chip) chip.style.display = demoActive ? 'inline-flex' : 'none';
      if (banner) banner.style.display = demoActive ? 'flex' : 'none';
      if (badge) {
        badge.textContent = demoActive
          ? '🧪 Demo Brain — local computation + knowledge base, no model weights'
          : `⚡ ${models.count || 0} local brain(s) ready`;
      }
      if (engineState) {
        engineState.textContent = demoActive ? '🧪 Demo (simulated)' : '🟢 Local GGUF';
        engineState.style.color = demoActive ? '#fcd34d' : '#34d399';
      }
      if (engineDetail) {
        engineDetail.textContent = demoActive
          ? `No GGUF in models/ · answers in ${demo.latency_ms ? demo.latency_ms.toFixed(1) + ' ms' : '~1 ms'} · install one for real reasoning`
          : `${models.count || 0} model(s) installed in models/`;
      }
      if (composerStatus) {
        composerStatus.textContent = demoActive
          ? 'Demo Brain · template + retrieval · 100% offline'
          : '100% offline · local SSD ROM';
      }
      const dot = document.getElementById('engineDot');
      if (dot) dot.classList.toggle('demo', demoActive);
      renderSandboxQuickCmds(data.platform || '');
    }

    function initUiExtras() {
      /* auto-grow + counter */
      const ta = document.getElementById('promptInput');
      if (ta) {
        const grow = () => {
          ta.style.height = 'auto';
          ta.style.height = Math.min(ta.scrollHeight, 220) + 'px';
          updateComposerMeta();
        };
        ta.addEventListener('input', grow);
        grow();
      }
      /* send button doubles as stop while streaming */
      const sendBtn = document.getElementById('sendBtn');
      if (sendBtn) {
        sendBtn.addEventListener('click', (ev) => {
          if (sendBtn.dataset.mode === 'stop') {
            ev.preventDefault();
            stopStreaming();
          }
        });
      }
      /* sandbox shortcuts */
      const sbEditor = document.getElementById('sandboxEditor');
      if (sbEditor) {
        sbEditor.addEventListener('keydown', (e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            runSandbox();
          } else if (e.key === 'Tab') {
            e.preventDefault();
            const start = sbEditor.selectionStart, end = sbEditor.selectionEnd;
            sbEditor.value = sbEditor.value.slice(0, start) + '    ' + sbEditor.value.slice(end);
            sbEditor.selectionStart = sbEditor.selectionEnd = start + 4;
            sandboxUpdateLineNums();
          }
        });
      }
      /* footer share button on the last answer */
      /* scroll-to-bottom fab */
      const sc = document.getElementById('chatScroll');
      if (sc) sc.addEventListener('scroll', updateScrollFab, { passive: true });
      /* keyboard shortcuts */
      document.addEventListener('keydown', (e) => {
        const meta = e.ctrlKey || e.metaKey;
        if (meta && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          promptInput.focus();
          promptInput.select();
        } else if (meta && e.key.toLowerCase() === 'b') {
          e.preventDefault();
          toggleSidebar();
        } else if (meta && e.shiftKey && e.key.toLowerCase() === 'o') {
          e.preventDefault();
          newChat();
        } else if (e.key === 'Escape' && isSending) {
          stopStreaming();
        } else if (meta && e.shiftKey && e.key.toLowerCase() === 'c' && lastUserPrompt) {
          e.preventDefault();
          copyToClipboard(lastUserPrompt, 'Prompt copied');
        }
      });
      /* mobile drawer overlay */
      if (window.innerWidth <= 820) {
        const sb = document.getElementById('sidebar');
        if (sb && !sb.classList.contains('collapsed')) {
          document.getElementById('sidebarOverlay')?.classList.add('show');
        }
      }
      updateComposerMeta();
    }
    window.addEventListener('resize', () => {
      const overlay = document.getElementById('sidebarOverlay');
      if (window.innerWidth > 820 && overlay) overlay.classList.remove('show');
      updateScrollFab();
    });

  </script>
</body>
</html>
"""


_FOLDER_SIZE_CACHE = {"ts": 0.0, "mb": 0.0, "gb": 0.0}
_FOLDER_SIZE_LOCK = threading.Lock()


def folder_size_mb(force: bool = False) -> float:
    """Total size of ROM databases + model weights, cached for 30s."""
    now = time.time()
    with _FOLDER_SIZE_LOCK:
        if not force and _FOLDER_SIZE_CACHE["ts"] and now - _FOLDER_SIZE_CACHE["ts"] < 30:
            return _FOLDER_SIZE_CACHE["mb"]
        total = 0
        base = os.path.dirname(os.path.abspath(__file__))
        for folder, _dirs, files in os.walk(base):
            if os.sep + ".git" in folder or "__pycache__" in folder:
                continue
            for name in files:
                if name.endswith((".db", ".gguf", ".part", ".safetensors")):
                    try:
                        total += os.path.getsize(os.path.join(folder, name))
                    except OSError:
                        pass
        mb = round(total / (1024 ** 2), 2)
        _FOLDER_SIZE_CACHE.update({"ts": now, "mb": mb, "gb": round(mb / 1024, 3)})
        return mb


def _demo_brain_status() -> dict:
    """Status of the simulated fallback brain (used when no GGUF is installed)."""
    try:
        import demo_brain
        info = demo_brain.status()
    except Exception as exc:  # pragma: no cover - defensive
        return {"enabled": False, "active": False, "error": str(exc)}
    installed = _installed_models_summary()
    info["active"] = bool(info.get("enabled")) and installed["count"] == 0
    info["models_installed"] = installed["count"]
    return info


def _installed_models_summary() -> dict:
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    files = []
    try:
        for name in sorted(os.listdir(models_dir)):
            path = os.path.join(models_dir, name)
            if os.path.isfile(path) and name.lower().endswith(".gguf"):
                size_mb = round(os.path.getsize(path) / (1024 ** 2), 1)
                if size_mb >= 100:
                    files.append({"name": name, "size_mb": size_mb})
    except OSError:
        pass
    return {"count": len(files), "installed": files, "dir": models_dir}


class ArgonHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _safe_write(self, body: bytes) -> bool:
        try:
            self.wfile.write(body)
            self.wfile.flush()
            return True
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
            return False

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._safe_write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            body = HTML_UI.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
                return
        elif self.path == "/api/status":
            storage = check_storage_safety()
            size_mb = folder_size_mb()
            storage["folder_size_mb"] = size_mb
            storage["folder_size_gb"] = round(size_mb / 1024, 3)
            self._send_json(
                {
                    "boot_id": BOOT_ID,
                    "storage": storage,
                    "harvester": harvester.get_status(),
                    "brains": list_cartridges(),
                    "elite_models": get_elite_model_download_status(),
                    "llama_update": dict(LLAMA_UPDATE_STATUS),
                    "offline_mode": get_offline_mode(),
                    "flux_status": get_flux_rom_status(),
                    "matrix": {},
                    "quantum_mode": QUANTUM_MODE["enabled"],
                    "demo_brain": _demo_brain_status(),
                    "models": _installed_models_summary(),
                    "platform": sys.platform,
                }
            )
        elif self.path == "/api/models/download/status":
            self._send_json(get_elite_model_download_status())
        elif self.path == "/api/images":
            self._send_json({"images": list_ssd_images()})
        elif self.path == "/api/images/vault":
            self._send_json({"vault": get_image_model_vault()})
        elif self.path == "/api/benchmark/status":
            from benchmark_speed import list_benchmark_models
            self._send_json({"models": list_benchmark_models()})
        elif self.path == "/api/video/status":
            self._send_json(get_video_studio_status())
        elif self.path.startswith("/gallery/videos/"):
            fname = os.path.basename(self.path.split("?", 1)[0])
            fpath = os.path.join(VIDEOS_DIR, fname)
            if os.path.exists(fpath) and os.path.isfile(fpath):
                ctype = "video/mp4" if fname.lower().endswith(".mp4") else "image/webp"
                with open(fpath, "rb") as f:
                    vdata = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(vdata)))
                self.end_headers()
                self._safe_write(vdata)
            else:
                self._send_json({"error": "Video not found"}, status=404)
        elif self.path.startswith("/api/ide/files"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            req_path = (qs.get("path", [TERMINAL_CWD])[0] or TERMINAL_CWD).strip()
            if not os.path.exists(req_path):
                req_path = TERMINAL_CWD
            items = []
            try:
                for name in sorted(os.listdir(req_path), key=lambda x: (not os.path.isdir(os.path.join(req_path, x)), x.lower())):
                    full = os.path.join(req_path, name)
                    is_dir = os.path.isdir(full)
                    size = 0 if is_dir else (os.path.getsize(full) if os.path.exists(full) else 0)
                    items.append({
                        "name": name,
                        "path": full,
                        "is_dir": is_dir,
                        "size": size,
                        "ext": "" if is_dir else os.path.splitext(name)[1].lower(),
                    })
                self._send_json({"cwd": os.path.abspath(req_path), "parent": os.path.dirname(os.path.abspath(req_path)), "items": items})
            except Exception as exc:
                self._send_json({"error": str(exc), "cwd": req_path, "items": []}, status=400)
        elif self.path.startswith("/ssd_images/"):
            fname = os.path.basename(self.path.split("?", 1)[0])
            fpath = os.path.join(SSD_IMAGES_DIR, fname)
            if os.path.exists(fpath) and os.path.isfile(fpath):
                low_fn = fname.lower()
                ctype = "image/svg+xml" if low_fn.endswith(".svg") else ("image/png" if low_fn.endswith(".png") else "image/jpeg")
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send_json({"error": "Image not found"}, status=404)
        else:
            self._send_json({"error": "Not found"}, status=404)

    @staticmethod
    def _host_allowed(host: str) -> bool:
        """Loopback, explicitly allow-listed hosts, and sandbox preview domains."""
        host = (host or "").strip().lower()
        if not host:
            return False
        name, _, port = host.partition(":")
        if port and port != str(PORT):
            return False
        if name in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]", "::1"):
            return True
        for entry in os.environ.get("ARGON_ALLOWED_HOSTS", "").replace(";", ",").split(","):
            if entry.strip().lower() in (name, host):
                return True
        # Live-preview / tunnel domains used by hosted sandboxes.
        preview_suffixes = (".e2b.app", ".arena.ai", ".ngrok-free.app", ".trycloudflare.com", ".loca.lt")
        if name.endswith(preview_suffixes):
            return True
        return os.environ.get("ARGON_ALLOW_ANY_HOST", "").strip().lower() in ("1", "true", "yes", "on")

    def _request_allowed(self) -> bool:
        """Same-origin guard: the Host must be trusted and Origin must match it."""
        host = (self.headers.get("Host") or "").strip().lower()
        if not self._host_allowed(host):
            return False
        origin = (self.headers.get("Origin") or "").strip().lower()
        if not origin or origin == "null":
            return True
        try:
            origin_host = (urllib.parse.urlsplit(origin).netloc or "").lower()
        except ValueError:
            return False
        return origin_host == host

    def do_POST(self) -> None:
        if not self._request_allowed():
            host_hdr = (self.headers.get("Host") or "").strip()
            origin_hdr = (self.headers.get("Origin") or "").strip()
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            reason = "Forbidden Host" if not self._host_allowed(host_hdr) else "CSRF Origin Blocked by ARGON Guard"
            payload = json.dumps({
                "error": reason,
                "hint": "Set ARGON_ALLOWED_HOSTS=<host> (comma separated) to allow another hostname.",
                "host": host_hdr,
                "origin": origin_hdr,
            })
            self._safe_write(payload.encode("utf-8"))
            return
        data = self._read_json()
        if self.path == "/api/chat":
            q_mode = data.get("quantum_mode")
            self._send_json(
                answer_query(
                    str(data.get("prompt", "")),
                    model=str(data.get("model", "auto")),
                    language=str(data.get("language", "auto")),
                    quantum_mode=q_mode,
                    swarm_enabled=bool(data.get("swarm_enabled", True)),
                )
            )
        elif self.path == "/api/chat/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            lang = str(data.get("language", "auto"))
            q_mode = data.get("quantum_mode")
            t_level = str(data.get("thinking_level", "mid"))
            for ev in stream_query(str(data.get("prompt", "")), language=lang, quantum_mode=q_mode, thinking_level=t_level):
                chunk = f"data: {json.dumps(ev)}\n\n".encode("utf-8")
                if not self._safe_write(chunk):
                    return
        elif self.path == "/api/harvester/start":
            self._send_json(harvester.start())
        elif self.path == "/api/harvester/pause":
            self._send_json(harvester.pause())
        elif self.path == "/api/benchmark/run":
            from benchmark_speed import list_benchmark_models, run_benchmark
            if str(data.get("action", "")) == "list":
                self._send_json({"models": list_benchmark_models()})
            else:
                self._send_json(run_benchmark(str(data.get("target", "all"))))
        elif self.path == "/api/security/audit":
            self._send_json(audit_laptop_security())
        elif self.path == "/api/security/sast":
            self._send_json(scan_code_vulnerabilities(str(data.get("code", ""))))
        elif self.path == "/api/security/patch":
            self._send_json(auto_patch_code(str(data.get("code", ""))))
        elif self.path == "/api/security/cve_search":
            self._send_json(search_cve_vault(str(data.get("query", ""))))
        elif self.path == "/api/security/cve_download":
            self._send_json(download_cve_datasets())
        elif self.path == "/api/chat/clear":
            self._send_json(clear_chat_history())
        elif self.path == "/api/offline_mode":
            self._send_json({"offline_mode": set_offline_mode(bool(data.get("offline", True)))})
        elif self.path == "/api/models/select":
            self._send_json(set_selected_mode(str(data.get("mode", "auto"))))
        elif self.path == "/api/models/verify":
          self._send_json({"results": verify_elite_model_urls()})
        elif self.path == "/api/models/cleanup":
          self._send_json(cleanup_legacy_text_models())
        elif self.path == "/api/models/update_llama":
          if LLAMA_UPDATE_STATUS["running"]:
            self._send_json(dict(LLAMA_UPDATE_STATUS))
          else:
            threading.Thread(target=_update_llama_server_background, daemon=True, name="llama-server-updater").start()
            self._send_json({"running": True, "message": "llama.cpp update started."})
        elif self.path == "/api/language/select":
            self._send_json(set_selected_language(str(data.get("language", "auto"))))
        elif self.path == "/api/quantum/toggle":
            QUANTUM_MODE["enabled"] = bool(data.get("enabled", True))
            self._send_json({"quantum_mode": QUANTUM_MODE["enabled"]})
        elif self.path == "/api/matrix/ingest":
            added = ingest_into_100m_matrix(str(data.get("text", "")), source_tag=str(data.get("source_tag", "ui_upload")))
            self._send_json({"added_tokens": added, "matrix": get_matrix_telemetry()})
        elif self.path == "/api/sandbox/run":
            global TERMINAL_CWD
            import subprocess as _sp
            import sys as _sys
            code_str = str(data.get("code", "")).strip()
            mode = str(data.get("mode", "auto")).strip().lower()
            timeout_sec = int(data.get("timeout", 30))
            is_term = (mode == "terminal") or code_str.startswith("!")
            try:
                if is_term:
                    cmd = code_str[1:].strip() if code_str.startswith("!") else code_str
                    low_cmd = cmd.lower()
                    if low_cmd in ("cls", "clear"):
                        self._send_json({"stdout": "", "stderr": "", "returncode": 0, "cwd": TERMINAL_CWD, "clear": True})
                        return
                    # Persistent cd / drive-switch support
                    if low_cmd == "cd" or low_cmd.startswith("cd ") or (len(cmd) == 2 and cmd[1] == ":" and cmd[0].isalpha()):
                        if low_cmd == "cd":
                            self._send_json({"stdout": TERMINAL_CWD + "\n", "stderr": "", "returncode": 0, "cwd": TERMINAL_CWD})
                            return
                        target = cmd if (len(cmd) == 2 and cmd[1] == ":") else cmd[3:].strip().strip('"').strip("'")
                        if target == "~":
                            new_dir = os.path.expanduser("~")
                        elif len(target) == 2 and target[1] == ":" and target[0].isalpha():
                            new_dir = target.upper() + "\\"
                        else:
                            new_dir = os.path.abspath(os.path.join(TERMINAL_CWD, target))
                        if os.path.isdir(new_dir):
                            TERMINAL_CWD = new_dir
                            self._send_json({"stdout": "", "stderr": "", "returncode": 0, "cwd": TERMINAL_CWD})
                        else:
                            self._send_json({"stdout": "", "stderr": f"Directory not found: {new_dir}", "returncode": 1, "cwd": TERMINAL_CWD})
                        return
                    if os.name == "nt":
                        proc = _sp.run(
                            ["powershell.exe", "-NoProfile", "-Command", cmd],
                            cwd=TERMINAL_CWD,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=timeout_sec,
                        )
                    else:
                        proc = _sp.run(
                            cmd,
                            shell=True,
                            cwd=TERMINAL_CWD,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=timeout_sec,
                        )
                else:
                    proc = _sp.run(
                        [_sys.executable, "-c", code_str],
                        cwd=TERMINAL_CWD,
                        input="1\n2\n3\n4\n5\ny\nn\n",
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout_sec,
                    )
                self._send_json({"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode, "cwd": TERMINAL_CWD})
            except Exception as exc:
                self._send_json({"stdout": "", "stderr": str(exc), "returncode": -1, "cwd": TERMINAL_CWD})
        elif self.path == "/api/image":
            prompt = str(data.get("prompt", "")).strip()
            force_offline = bool(data.get("force_offline", False))
            img = generate_image(prompt, force_offline=force_offline)
            failed = not img.get("url")
            self._send_json(
                {
                    "reply": (
                        f"⚠️ Image generation failed: {img.get('mode') or 'unknown error'}"
                        if failed else f"Generated image for: **{img['prompt']}**"
                    ),
                    "ok": not failed,
                    "image_url": img.get("url", ""),
                    "image_filename": img.get("filename", ""),
                    "image_mode": img.get("mode", ""),
                    "latency_ms": img.get("latency_ms", 0.0),
                    "source": img.get("mode", ""),
                    "badge": (
                        f"⚠️ {img.get('mode', 'error')}" if failed
                        else f"🎨 {img.get('latency_ms', 0)}ms | {img.get('mode', '')}"
                    ),
                }
            )
        elif self.path == "/api/generate_image":
            prompt = str(data.get("prompt", "")).strip()
            style = str(data.get("style", "Realistic Photo")).strip()
            width = int(data.get("width", 768))
            height = int(data.get("height", 768))
            engine_pref = str(data.get("engine_pref", "auto")).strip()
            self._send_json(generate_image_hybrid(prompt, style=style, width=width, height=height, engine_pref=engine_pref))
        elif self.path == "/api/flux/download":
            import download_flux_rom as dfr
            self._send_json(dfr.start_flux_download())
        elif self.path == "/api/flux/status":
            import download_flux_rom as dfr
            self._send_json({"flux": get_flux_rom_status(), "download": dfr.get_flux_download_status()})
        elif self.path == "/api/image/upload":
            data_url = str(data.get("data_url", ""))
            filename = os.path.basename(str(data.get("filename", "upload.png"))) or "upload.png"
            raw_b64 = data_url.split(",", 1)[-1]
            os.makedirs(SSD_IMAGES_DIR, exist_ok=True)
            out_name = f"upload_{int(time.time()*1000)}_{filename}"
            out_path = os.path.join(SSD_IMAGES_DIR, out_name)
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(raw_b64))
            self._send_json({"url": f"/ssd_images/{out_name}", "filename": out_name})
        elif self.path == "/api/image/edit":
            image_url = str(data.get("image_url", "")).strip()
            command = str(data.get("command", "")).strip()
            mask_b64 = data.get("mask_b64")
            try:
                res = apply_image_edit(image_url, command, mask_b64=mask_b64)
                self._send_json(res)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
        elif self.path == "/api/ide/files":
            action = str(data.get("action", "")).strip()
            if action in ("", "list"):
                target = str(data.get("path", "")).strip() or TERMINAL_CWD
                if not os.path.exists(target):
                    target = TERMINAL_CWD
                items = []
                try:
                    for name in sorted(os.listdir(target), key=lambda x: (not os.path.isdir(os.path.join(target, x)), x.lower())):
                        full = os.path.join(target, name)
                        is_dir = os.path.isdir(full)
                        items.append({
                            "name": name,
                            "path": full,
                            "is_dir": is_dir,
                            "size": 0 if is_dir or not os.path.exists(full) else os.path.getsize(full),
                            "ext": "" if is_dir else os.path.splitext(name)[1].lower(),
                        })
                    self._send_json({"cwd": os.path.abspath(target), "parent": os.path.dirname(os.path.abspath(target)), "items": items})
                except Exception as exc:
                    self._send_json({"error": str(exc), "items": []}, status=400)
                return
            target = str(data.get("path", "")).strip()
            try:
                if action == "read":
                    with open(target, "r", encoding="utf-8", errors="replace") as f:
                        self._send_json({"path": target, "content": f.read()})
                elif action == "save":
                    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(str(data.get("content", "")))
                    self._send_json({"ok": True, "path": target})
                elif action == "create_file":
                    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                    with open(target, "w", encoding="utf-8") as f:
                        f.write("")
                    self._send_json({"ok": True, "path": target})
                elif action == "create_folder":
                    os.makedirs(target, exist_ok=True)
                    self._send_json({"ok": True, "path": target})
                elif action == "rename":
                    new_path = str(data.get("new_path", "")).strip()
                    os.rename(target, new_path)
                    self._send_json({"ok": True, "path": new_path})
                elif action == "delete":
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                    elif os.path.exists(target):
                        os.remove(target)
                    self._send_json({"ok": True})
                else:
                    self._send_json({"error": "Unknown action"}, status=400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
        elif self.path == "/api/ide/terminal":
            cmd = str(data.get("cmd", "")).strip()
            run_file = str(data.get("run_file", "")).strip()
            if run_file:
                ext = os.path.splitext(run_file)[1].lower()
                fdir = os.path.dirname(os.path.abspath(run_file))
                if os.path.isdir(fdir):
                    TERMINAL_CWD = fdir
                if ext == ".py":
                    cmd = f'"{sys.executable}" "{run_file}"'
                elif ext == ".js":
                    cmd = f'node "{run_file}"'
                elif ext == ".ps1":
                    cmd = f'powershell -ExecutionPolicy Bypass -File "{run_file}"'
                else:
                    cmd = f'"{run_file}"'

            if not cmd:
                self._send_json({"stdout": "", "stderr": "", "exit_code": 0, "cwd": TERMINAL_CWD})
                return

            if cmd.lower().startswith("cd ") or cmd.lower() == "cd":
                arg = cmd[2:].strip().strip('"')
                if not arg:
                    self._send_json({"stdout": TERMINAL_CWD, "stderr": "", "exit_code": 0, "cwd": TERMINAL_CWD})
                    return
                new_dir = os.path.abspath(os.path.join(TERMINAL_CWD, arg)) if not os.path.isabs(arg) else os.path.abspath(arg)
                if os.path.isdir(new_dir):
                    TERMINAL_CWD = new_dir
                    self._send_json({"stdout": "", "stderr": "", "exit_code": 0, "cwd": TERMINAL_CWD})
                else:
                    self._send_json({"stdout": "", "stderr": f"Directory not found: {new_dir}", "exit_code": 1, "cwd": TERMINAL_CWD})
                return

            try:
                proc = subprocess.run(cmd, shell=True, cwd=TERMINAL_CWD, capture_output=True, text=True, timeout=20)
                self._send_json({
                    "stdout": (proc.stdout or "")[:8000],
                    "stderr": (proc.stderr or "")[:4000],
                    "exit_code": proc.returncode,
                    "cwd": TERMINAL_CWD,
                })
            except subprocess.TimeoutExpired:
                self._send_json({"stdout": "", "stderr": "Command timed out after 20s.", "exit_code": 124, "cwd": TERMINAL_CWD})
            except Exception as exc:
                self._send_json({"stdout": "", "stderr": str(exc), "exit_code": 1, "cwd": TERMINAL_CWD})
        elif self.path == "/api/ide/fix_line":
            code = str(data.get("code", ""))
            err = str(data.get("error", ""))
            lang = str(data.get("lang", "python"))
            self._send_json(line_by_line_fix_code(code, err, lang=lang))
        elif self.path == "/api/ide/agent":
            import code_agent
            try:
                result = code_agent.run(
                    prompt=str(data.get("prompt", "")),
                    mode=str(data.get("mode", "auto")),
                    path=(str(data.get("path", "")).strip() or None),
                    cwd=str(data.get("cwd", "") or TERMINAL_CWD),
                    content=data.get("content") if isinstance(data.get("content"), str) else None,
                )
            except Exception as exc:  # never take the server down for an agent request
                result = {"ok": False, "error": str(exc)}
            self._send_json(result)
        elif self.path in ("/api/ide/agent/apply", "/api/ide/agent/discard", "/api/ide/agent/revert"):
            import code_agent
            token = str(data.get("token", "")).strip()
            action = self.path.rsplit("/", 1)[-1]
            fn = {"apply": code_agent.apply, "discard": code_agent.discard, "revert": code_agent.revert}[action]
            self._send_json(fn(token))
        elif self.path == "/api/ide/ai_assist":
            prompt = str(data.get("prompt", "")).strip()
            filename = str(data.get("filename", "untitled")).strip()
            code = str(data.get("code", ""))[:6000]
            err = str(data.get("error", ""))[:2000]
            full_prompt = (
                f"You are Antigravity IDE Coding Agent. Active file: `{filename}`.\n"
                f"Current file code:\n```\n{code}\n```\n"
                + (f"Recent terminal error/traceback:\n```\n{err}\n```\n" if err else "")
                + f"Task: {prompt}\nProvide clean, complete runnable code inside a fenced code block."
            )
            res = answer_query(full_prompt, model="auto", swarm_enabled=False)
            self._send_json({"reply": res.get("reply", ""), "ms": res.get("ms", 0), "gear": res.get("gear", "")})
        elif self.path == "/api/video/download":
            fname = str(data.get("filename", "")).strip()
            self._send_json(start_video_model_download(fname))
        elif self.path == "/api/video/generate":
            res = generate_video(
                prompt=str(data.get("prompt", "")).strip() or "cinematic neon cyberpunk city",
                engine=str(data.get("engine", "auto")),
                width=int(data.get("width", 480)),
                height=int(data.get("height", 320)),
                fps=int(data.get("fps", 16)),
                frames=int(data.get("frames", 33)),
                source_image=data.get("source_image"),
            )
            self._send_json(res)
        elif self.path == "/api/sandbox_run":
            mode = str(data.get("mode", "python")).strip().lower()
            code = str(data.get("code", ""))
            if mode == "terminal":
                self._send_json(run_safe_terminal_command(code))
            else:
                self._send_json(run_code_in_sandbox(code))
        elif self.path == "/api/models/download":
          only = data.get("models") if isinstance(data.get("models"), list) else None
          single = data.get("model")
          if not only and isinstance(single, str) and single.strip():
            only = [single.strip()]
          self._send_json(start_elite_model_download(only))
        elif self.path == "/api/shutdown":
            self._send_json({"status": "stopping"})
            harvester.pause()
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
        elif self.path == "/api/images":
            self._send_json({"images": list_ssd_images()})
        elif self.path == "/api/images/vault":
            self._send_json({"vault": get_image_model_vault()})
        elif self.path == "/api/benchmark/status":
            from benchmark_speed import list_benchmark_models
            self._send_json({"models": list_benchmark_models()})
        elif self.path == "/api/video/status":
            self._send_json(get_video_studio_status())
        elif self.path.startswith("/gallery/videos/"):
            fname = os.path.basename(self.path.split("?", 1)[0])
            fpath = os.path.join(VIDEOS_DIR, fname)
            if os.path.exists(fpath) and os.path.isfile(fpath):
                ctype = "video/mp4" if fname.lower().endswith(".mp4") else "image/webp"
                with open(fpath, "rb") as f:
                    vdata = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(vdata)))
                self.end_headers()
                self._safe_write(vdata)
            else:
                self._send_json({"error": "Video not found"}, status=404)
        elif self.path.startswith("/api/ide/files"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            req_path = (qs.get("path", [TERMINAL_CWD])[0] or TERMINAL_CWD).strip()
            if not os.path.exists(req_path):
                req_path = TERMINAL_CWD
            items = []
            try:
                for name in sorted(os.listdir(req_path), key=lambda x: (not os.path.isdir(os.path.join(req_path, x)), x.lower())):
                    full = os.path.join(req_path, name)
                    is_dir = os.path.isdir(full)
                    size = 0 if is_dir else (os.path.getsize(full) if os.path.exists(full) else 0)
                    items.append({
                        "name": name,
                        "path": full,
                        "is_dir": is_dir,
                        "size": size,
                        "ext": "" if is_dir else os.path.splitext(name)[1].lower(),
                    })
                self._send_json({"cwd": os.path.abspath(req_path), "parent": os.path.dirname(os.path.abspath(req_path)), "items": items})
            except Exception as exc:
                self._send_json({"error": str(exc), "cwd": req_path, "items": []}, status=400)
        elif self.path.startswith("/ssd_images/"):
            fname = os.path.basename(self.path.split("?", 1)[0])
            fpath = os.path.join(SSD_IMAGES_DIR, fname)
            if os.path.exists(fpath) and os.path.isfile(fpath):
                low_fn = fname.lower()
                ctype = "image/svg+xml" if low_fn.endswith(".svg") else ("image/png" if low_fn.endswith(".png") else "image/jpeg")
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send_json({"error": "Image not found"}, status=404)
        else:
            self._send_json({"error": "Not found"}, status=404)


def _watch_code_changes(server: ThreadingHTTPServer) -> None:
    """Auto-restart server when any .py file in the project is modified."""
    base_dir = os.path.dirname(os.path.abspath(__file__))

    def _snapshot() -> dict[str, float]:
        mtimes = {}
        for fn in os.listdir(base_dir):
            if fn.endswith(".py"):
                p = os.path.join(base_dir, fn)
                try:
                    mtimes[p] = os.path.getmtime(p)
                except OSError:
                    pass
        return mtimes

    initial = _snapshot()
    while True:
        time.sleep(1.0)
        current = _snapshot()
        if current != initial:
            print("[ARGON-OMEGA] Code change detected! Auto-reloading server...")
            harvester.pause()
            server.server_close()
            os.execv(sys.executable, [sys.executable] + sys.argv)


def launch_native_desktop_window(url: str = "http://127.0.0.1:8080") -> None:
    """Launches ARGON-OMEGA as a standalone native Windows desktop application window."""
    import subprocess
    app_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for path in app_paths:
        if os.path.exists(path):
            subprocess.Popen([path, f"--app={url}", "--window-size=1280,820"])
            return
    webbrowser.open(url)


def create_desktop_shortcut() -> None:
    """Create 'ARGON System AI.lnk' on Windows Desktop pointing to Launch_Argon_System_App.bat."""
    if os.name != "nt":
        return
    try:
        import subprocess
        base_dir = os.path.dirname(os.path.abspath(__file__))
        bat_path = os.path.join(base_dir, "Launch_Argon_System_App.bat")
        lnk_path = os.path.join(os.path.expanduser("~"), "Desktop", "ARGON System AI.lnk")
        if os.path.exists(bat_path) and not os.path.exists(lnk_path):
            ps = (
                f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{lnk_path}");'
                f'$s.TargetPath="{bat_path}";'
                f'$s.WorkingDirectory="{base_dir}";'
                f'$s.Save()'
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass


def main() -> None:
    init_db()
    create_desktop_shortcut()
    threading.Thread(target=ensure_llama_server, daemon=True).start()
    local_url = f"http://127.0.0.1:{PORT}"
    print(f"ARGON-OMEGA Native System App running at {local_url} (127.0.0.1 locked)")
    threading.Timer(0.8, lambda: launch_native_desktop_window(local_url)).start()
    server = ThreadingHTTPServer((HOST, PORT), ArgonHandler)
    threading.Thread(target=_watch_code_changes, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        harvester.pause()
        server.server_close()


if __name__ == "__main__":
    main()
