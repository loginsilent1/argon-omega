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
from image_engine import SSD_IMAGES_DIR, generate_image, generate_image_hybrid, get_flux_rom_status, list_ssd_images
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
  </style>
</head>
<body>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <button class="btn-new-chat" onclick="newChat()">
        <span>＋</span> New chat
      </button>
      <button class="icon-btn" onclick="toggleSidebar()" title="Close sidebar">☰</button>
    </div>

    <div class="sidebar-section-header">
      <span class="sidebar-section-title">Recent Chats</span>
      <button class="clear-all-btn" onclick="clearAllChats()" title="Delete all chats">Clear all</button>
    </div>
    <div class="history-list" id="historyList"></div>

    <div class="telemetry-stack">
      <div class="card">
        <div class="card-row">
          <span class="card-label">SSD ROM</span>
          <span class="metric" id="romSize" style="font-size:0.8rem;">0.00 MB</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" id="romProgress"></div></div>
        <div class="sub-metric" id="totalRows">Brains: 0 • Memories: <span id="memoryRows">0</span></div>
      </div>
    </div>
  </aside>

  <main class="main">
    <header class="topbar">
      <div class="topbar-left">
        <button class="icon-btn" id="openSidebarBtn" onclick="toggleSidebar()" title="Open sidebar" style="display:none;">☰</button>
        <div class="model-pill" onclick="newChat()">
          <span>ARGON-OMEGA</span>
          <span class="tag">99.9% SSD ROM</span>
        </div>
      </div>
      <div class="topbar-controls" style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; width:100%;">
        <span style="font-size:0.75rem;color:var(--text-secondary);">🧠 Brain:</span>
        <select id="brainSelect" onchange="selectBrain()" style="max-width:430px;">
          <option value="auto">⚡ Auto (Qwen Coder + Phi-4 Math/Logic + Gemma General/Fast)</option>
          <option value="qwen2.5-coder-7b-instruct-q4_k_m.gguf">💻 qwen2.5-coder-7b-instruct-q4_k_m.gguf (7B Fast Daily Coder)</option>
          <option value="qwen2.5-coder-32b-instruct-IQ3_XS.gguf">🏗️ qwen2.5-coder-32b-instruct-IQ3_XS.gguf (32B Architect Coder)</option>
          <option value="Phi-4-mini-instruct-Q4_K_M.gguf">🧮 Phi-4-mini-instruct-Q4_K_M.gguf (3.8B Fast Math &amp; Logic)</option>
          <option value="phi-4-Q4_K_M.gguf">🧠 phi-4-Q4_K_M.gguf (14B Deep Math &amp; Algorithms)</option>
          <option value="gemma-3-4b-it-Q4_K_M.gguf">🌐 gemma-3-4b-it-Q4_K_M.gguf (4B General &amp; Multilingual)</option>
          <option value="gemma-3-1b-it-Q4_K_M.gguf">⚡ gemma-3-1b-it-Q4_K_M.gguf (1B Fast Router &amp; Terminal)</option>
        </select>
        <span style="font-size:0.75rem;color:var(--text-secondary);">🌐 Language:</span>
        <select id="langSelect" onchange="selectLanguage()" style="max-width:160px;">
          <option value="auto">🌐 Auto-Detect</option>
          <option value="en">English</option>
          <option value="ne">नेपाली (Nepali)</option>
          <option value="romanized_ne">Romanized Nepali</option>
          <option value="hi">हिन्दी (Hindi)</option>
          <option value="es">Español</option>
          <option value="fr">Français</option>
          <option value="zh-CN">中文 (Chinese)</option>
        </select>
        <button class="btn-subtle" id="offlineModeBtn" onclick="toggleOfflineMode()" style="width:auto;padding:6px 10px;border-color:#10b981;color:#10b981;font-weight:700;">✈️ OFFLINE ONLY</button>
      </div>
      <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('chat', this)">💬 Chat</button>
        <button class="tab-btn" id="sandboxTabBtn" onclick="switchTab('sandbox', this)">🧪 Sandbox &amp; Terminal</button>
        <button class="tab-btn" onclick="switchTab('gallery', this); loadGallery();">🎨 SSD Image Gallery</button>
        <button class="tab-btn" onclick="switchTab('editor', this); loadEditorPicker();">🖌️ Image Editor</button>
        <button class="tab-btn" onclick="switchTab('security', this)">🛡️ Security Lab</button>
        <button class="tab-btn" onclick="switchTab('benchmark', this); loadBenchmarkModels();">⚡ AI Benchmark</button>
      </div>
    </header>

    <!-- CHAT TAB -->
    <div id="tab-chat" class="tab-view active">
      <div class="chat-scroll" id="chatScroll">
        <div class="chat-container" id="chatBox">
          <div class="welcome-screen" id="welcomeScreen">
            <div class="welcome-title">What can I help with?</div>
            <div class="welcome-sub">100% offline AI powered by your local SSD ROM &amp; llama-server.</div>
            <div class="suggestion-grid">
              <button class="suggestion-chip" onclick="useSuggestion('Explain how mmap works in llama-server')">
                <strong>Explain mmap</strong>
                <span>How local model memory mapping works</span>
              </button>
              <button class="suggestion-chip" onclick="useSuggestion('Write a Python script to scan a directory for large files')">
                <strong>Write a Python script</strong>
                <span>Scan a directory for large files</span>
              </button>
              <button class="suggestion-chip" onclick="useSuggestion('Remember that my favorite language is Python')">
                <strong>Save to memory</strong>
                <span>Store a personal fact in SSD ROM</span>
              </button>
              <button class="suggestion-chip" onclick="useSuggestion('What are common SQL injection vulnerabilities and how to fix them?')">
                <strong>Security advice</strong>
                <span>SQL injection patterns &amp; fixes</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="input-wrapper">
        <div id="terminalDrawer" style="display:none;max-width:768px;margin:0 auto 10px;background:#090d16;border:1px solid rgba(16,185,129,0.4);border-radius:14px;padding:10px 12px;font-family:ui-monospace,Consolas,monospace;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap;">
            <span style="font-size:0.78rem;color:#34d399;font-weight:700;">💻 Read-Only Terminal &amp; Python Sandbox</span>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <button type="button" class="btn-subtle" onclick="runDrawerCmd('terminal','ipconfig')" style="font-size:0.72rem;padding:3px 8px;">$ ipconfig</button>
              <button type="button" class="btn-subtle" onclick="runDrawerCmd('terminal','netstat -ano')" style="font-size:0.72rem;padding:3px 8px;">$ netstat -ano</button>
              <button type="button" class="btn-subtle" onclick="runDrawerCmd('terminal','tasklist')" style="font-size:0.72rem;padding:3px 8px;">$ tasklist</button>
              <button type="button" class="btn-subtle" onclick="runDrawerCmd('terminal','whoami')" style="font-size:0.72rem;padding:3px 8px;">$ whoami</button>
              <button type="button" class="btn-subtle" onclick="runDrawerCmd('python','print([x**2 for x in range(1, 11)])')" style="font-size:0.72rem;padding:3px 8px;">🧪 Test Python Sandbox</button>
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-bottom:8px;">
            <select id="drawerMode" style="max-width:120px;font-size:0.78rem;">
              <option value="terminal">$ Terminal</option>
              <option value="python">🐍 Python</option>
            </select>
            <input id="drawerInput" type="text" placeholder="Enter read-only command (e.g. ipconfig) or Python snippet..." style="flex:1;font-family:ui-monospace,Consolas,monospace;font-size:0.8rem;" onkeydown="if(event.key==='Enter'){event.preventDefault();runDrawerCmd();}" />
            <button type="button" class="btn-subtle" onclick="runDrawerCmd()" style="background:#065f46;color:#ecfdf5;border-color:#10b981;">Run</button>
          </div>
          <pre id="drawerOutput" style="margin:0;max-height:160px;overflow-y:auto;font-size:0.78rem;color:#34d399;white-space:pre-wrap;background:#04070d;padding:8px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.08);">Ready (Read-Only Guard Active).</pre>
        </div>
        <form class="input-pill" onsubmit="sendMessage(event)">
          <button type="button" id="imageModeBtn" class="img-toggle-btn" onclick="toggleImageMode()" title="Toggle Image Generation Mode">🎨 Image</button>
          <button type="button" id="terminalToggleBtn" class="img-toggle-btn" onclick="toggleTerminalDrawer()" title="Toggle Read-Only Terminal & Sandbox">💻 Terminal</button>
          <textarea id="promptInput" rows="1" placeholder="Message ARGON-OMEGA (or type $ ipconfig / run: print(2**10))..." autocomplete="off"></textarea>
          <select id="thinkingLevelSelect" title="Thinking Level (Low = Fast, Mid = Balanced, High = Deep)" style="background:rgba(15,23,42,0.85);color:#cbd5e1;border:1px solid rgba(255,255,255,0.14);border-radius:8px;padding:4px 8px;font-size:0.75rem;margin-right:6px;">
            <option value="low">⚡ Low</option>
            <option value="mid" selected>⚖️ Mid</option>
            <option value="high">🧠 High</option>
          </select>
          <button type="submit" id="sendBtn" class="send-btn" title="Send">↑</button>
        </form>
        <div class="disclaimer">ARGON-OMEGA runs 100% offline. Responses come from your local SSD ROM &amp; llama-server.</div>
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
      <div class="lab-container" style="max-width:1200px; height:100%; display:flex; flex-direction:column; gap:12px;">
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
          <button class="btn-subtle" onclick="loadSandboxTemplate('snake')" style="width:auto;padding:6px 12px;">🐍 Playable Snake Game</button>
          <button class="btn-subtle" onclick="loadSandboxTemplate('python')" style="width:auto;padding:6px 12px;">⚡ Python Algorithm Runner</button>
          <button class="btn-subtle" onclick="loadSandboxTemplate('terminal')" style="width:auto;padding:6px 12px;">💻 Full System Terminal (CMD/PowerShell)</button>
          <button class="btn-firehose" onclick="runSandbox()" style="width:auto;padding:6px 16px;margin-left:auto;">▶ Run Code / HTML5</button>
          <button class="btn-subtle" onclick="document.getElementById('sandboxEditor').value=''; document.getElementById('sandboxConsole').textContent=''; document.getElementById('sandboxFrame').srcdoc='';" style="width:auto;padding:6px 12px;">Clear</button>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; flex:1; min-height:420px;">
          <div class="lab-panel" style="display:flex; flex-direction:column; gap:8px;">
            <div class="card-label">Code / Terminal Editor (Python, HTML5/JS, or !Command)</div>
            <textarea id="sandboxEditor" style="flex:1; width:100%; min-height:360px; font-family:monospace; font-size:0.85rem; background:#0b1120; color:#e2e8f0; border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:10px; resize:none;" placeholder="Write Python code, HTML5/JS app, or !command..."></textarea>
          </div>
          <div class="lab-panel" style="display:flex; flex-direction:column; gap:8px;">
            <div class="card-label">Interactive System Terminal &amp; Console Output</div>
            <pre id="sandboxConsole" class="output-pre" style="min-height:160px; max-height:220px; overflow:auto; margin:0; font-family:monospace; font-size:0.82rem;">Full System Terminal ready. Type any CMD/PowerShell command below.</pre>
            <div style="display:flex; align-items:center; gap:6px; background:#020617; border:1px solid rgba(56,189,248,0.35); border-radius:8px; padding:6px 10px;">
              <span id="terminalPromptLabel" style="font-family:monospace; font-size:0.82rem; color:#38bdf8; white-space:nowrap;">PS ~&gt;</span>
              <input id="terminalCmdInput" type="text" autocomplete="off" placeholder="Enter CMD/PowerShell command (e.g. dir, cd .., ipconfig, cls)..." style="flex:1; background:transparent; border:none; outline:none; color:#e2e8f0; font-family:monospace; font-size:0.85rem;" onkeydown="handleTerminalKey(event)">
            </div>
            <div class="card-label">Live HTML5 App / Game Canvas</div>
            <iframe id="sandboxFrame" sandbox="allow-scripts" style="flex:1; width:100%; min-height:180px; background:#0f172a; border:1px solid rgba(255,255,255,0.12); border-radius:8px;"></iframe>
          </div>
        </div>
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
            <button class="btn-subtle" id="ideRightAiBtn" onclick="ideSwitchRight('ai')" style="flex:1;font-size:0.75rem;background:#094771;color:#fff;">🤖 Antigravity AI</button>
            <button class="btn-subtle" id="ideRightPrevBtn" onclick="ideSwitchRight('preview')" style="flex:1;font-size:0.75rem;">🌐 Live Preview</button>
          </div>
          <div id="ideAiPane" style="flex:1;display:flex;flex-direction:column;overflow:hidden;padding:8px;gap:8px;">
            <div style="display:flex;gap:4px;flex-wrap:wrap;">
              <button class="btn-subtle" onclick="ideAiAssist('Refactor and improve this code')" style="font-size:0.72rem;padding:4px 7px;">✨ Refactor</button>
              <button class="btn-subtle" onclick="ideAiAssist('Fix the recent terminal error in this file', true)" style="font-size:0.72rem;padding:4px 7px;">🔧 Fix Error</button>
              <button class="btn-subtle" onclick="ideAiAssist('Explain this code concisely')" style="font-size:0.72rem;padding:4px 7px;">📝 Explain</button>
            </div>
            <div id="ideAiOutput" style="flex:1;overflow-y:auto;background:#111;border:1px solid #2d2d2d;border-radius:8px;padding:8px;font-size:0.8rem;white-space:pre-wrap;color:#e2e8f0;">Open a file and ask Antigravity AI to edit, fix errors, or explain code.</div>
            <button id="ideApplyBtn" class="btn-subtle" onclick="ideApplyCode()" style="display:none;background:#065f46;color:#ecfdf5;border-color:#10b981;">✅ Apply Code to Editor</button>
            <div style="display:flex;gap:6px;">
              <input id="ideAiPrompt" type="text" placeholder="Ask Antigravity AI..." style="flex:1;font-size:0.78rem;padding:6px 8px;" onkeydown="if(event.key==='Enter')ideAiAssist();" />
              <button class="btn-subtle" onclick="ideAiAssist()" style="padding:6px 10px;">Send</button>
            </div>
          </div>
          <div id="idePreviewPane" style="display:none;flex:1;flex-direction:column;overflow:hidden;padding:8px;">
            <iframe id="idePreviewFrame" sandbox="allow-scripts" style="flex:1;width:100%;border:1px solid #2d2d2d;border-radius:8px;background:#ffffff;"></iframe>
          </div>
        </div>

        <!-- Bottom: Integrated Full-System Terminal -->
        <div style="background:#0c0c0c;border-top:1px solid #2d2d2d;border-right:1px solid #2d2d2d;display:flex;flex-direction:column;overflow:hidden;">
          <div style="padding:4px 10px;background:#181818;border-bottom:1px solid #2d2d2d;display:flex;justify-content:space-between;align-items:center;font-size:0.74rem;">
            <span style="color:#34d399;font-weight:700;">💻 TERMINAL (Full PC Access)</span>
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
      sb.classList.toggle('collapsed');
      openBtn.style.display = sb.classList.contains('collapsed') ? 'flex' : 'none';
    }

    function switchTab(name, btn) {
      document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      btn.classList.add('active');
    }

    function renderHistory() {
      const list = document.getElementById('historyList');
      list.innerHTML = '';
      chats.slice().reverse().forEach(c => {
        const item = document.createElement('div');
        item.className = 'history-item' + (c.id === currentChatId ? ' active' : '');
        item.onclick = () => loadChat(c.id);

        const title = document.createElement('span');
        title.className = 'history-item-title';
        title.textContent = (pendingChats.has(c.id) ? '⏳ ' : '') + (c.title || 'New Chat');

        const delBtn = document.createElement('button');
        delBtn.className = 'history-del-btn';
        delBtn.title = 'Delete chat';
        delBtn.textContent = '✕';
        delBtn.onclick = (e) => deleteChat(c.id, e);

        item.appendChild(title);
        item.appendChild(delBtn);
        list.appendChild(item);
      });
    }

    function deleteChat(id, e) {
      e.stopPropagation();
      chats = chats.filter(c => c.id !== id);
      localStorage.setItem('argon_chats', JSON.stringify(chats));
      if (currentChatId === id) {
        newChat();
      } else {




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

    async function ideAiAssist(preset = null, includeErr = false) {
      const inp = document.getElementById('ideAiPrompt');
      const prompt = preset || inp.value.trim();
      if (!prompt) return;
      if (!preset) inp.value = '';
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const out = document.getElementById('ideAiOutput');
      out.textContent = '⏳ Antigravity AI is thinking...';
      const res = await fetch('/api/ide/ai_assist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          prompt,
          filename: cur ? cur.name : 'untitled',
          code: cur ? cur.content : '',
          error: includeErr ? ideLastError : ''
        })
      });
      const data = await res.json();
      out.textContent = data.reply || 'No response.';
      const m = (data.reply || '').match(/```[\w]*\n([\s\S]*?)```/);
      if (m) {
        ideLastAiCode = m[1];
        document.getElementById('ideApplyBtn').style.display = 'block';
      } else {
        document.getElementById('ideApplyBtn').style.display = 'none';
      }
    }

    function ideApplyCode() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur || !ideLastAiCode) return;
      cur.content = ideLastAiCode;
      cur.dirty = true;
      ideRenderTabs();
    }

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
      box.innerHTML = `
        <div class="welcome-screen" id="welcomeScreen">
          <div class="welcome-title">What can I help with?</div>
          <div class="welcome-sub">100% offline AI powered by your local SSD ROM &amp; llama-server.</div>
          <div class="suggestion-grid">
            <button class="suggestion-chip" onclick="useSuggestion('Explain how mmap works in llama-server')">
              <strong>Explain mmap</strong>
              <span>How local model memory mapping works</span>
            </button>
            <button class="suggestion-chip" onclick="useSuggestion('Write a Python script to scan a directory for large files')">
              <strong>Write a Python script</strong>
              <span>Scan a directory for large files</span>
            </button>
            <button class="suggestion-chip" onclick="useSuggestion('Remember that my favorite language is Python')">
              <strong>Save to memory</strong>
              <span>Store a personal fact in SSD ROM</span>
            </button>
            <button class="suggestion-chip" onclick="useSuggestion('What are common SQL injection vulnerabilities and how to fix them?')">
              <strong>Security advice</strong>
              <span>SQL injection patterns &amp; fixes</span>
            </button>
          </div>
        </div>`;




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

    async function ideAiAssist(preset = null, includeErr = false) {
      const inp = document.getElementById('ideAiPrompt');
      const prompt = preset || inp.value.trim();
      if (!prompt) return;
      if (!preset) inp.value = '';
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const out = document.getElementById('ideAiOutput');
      out.textContent = '⏳ Antigravity AI is thinking...';
      const res = await fetch('/api/ide/ai_assist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          prompt,
          filename: cur ? cur.name : 'untitled',
          code: cur ? cur.content : '',
          error: includeErr ? ideLastError : ''
        })
      });
      const data = await res.json();
      out.textContent = data.reply || 'No response.';
      const m = (data.reply || '').match(/```[\w]*\n([\s\S]*?)```/);
      if (m) {
        ideLastAiCode = m[1];
        document.getElementById('ideApplyBtn').style.display = 'block';
      } else {
        document.getElementById('ideApplyBtn').style.display = 'none';
      }
    }

    function ideApplyCode() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur || !ideLastAiCode) return;
      cur.content = ideLastAiCode;
      cur.dirty = true;
      ideRenderTabs();
    }

    renderHistory();
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

    async function ideAiAssist(preset = null, includeErr = false) {
      const inp = document.getElementById('ideAiPrompt');
      const prompt = preset || inp.value.trim();
      if (!prompt) return;
      if (!preset) inp.value = '';
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const out = document.getElementById('ideAiOutput');
      out.textContent = '⏳ Antigravity AI is thinking...';
      const res = await fetch('/api/ide/ai_assist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          prompt,
          filename: cur ? cur.name : 'untitled',
          code: cur ? cur.content : '',
          error: includeErr ? ideLastError : ''
        })
      });
      const data = await res.json();
      out.textContent = data.reply || 'No response.';
      const m = (data.reply || '').match(/```[\w]*\n([\s\S]*?)```/);
      if (m) {
        ideLastAiCode = m[1];
        document.getElementById('ideApplyBtn').style.display = 'block';
      } else {
        document.getElementById('ideApplyBtn').style.display = 'none';
      }
    }

    function ideApplyCode() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur || !ideLastAiCode) return;
      cur.content = ideLastAiCode;
      cur.dirty = true;
      ideRenderTabs();
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

    async function ideAiAssist(preset = null, includeErr = false) {
      const inp = document.getElementById('ideAiPrompt');
      const prompt = preset || inp.value.trim();
      if (!prompt) return;
      if (!preset) inp.value = '';
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const out = document.getElementById('ideAiOutput');
      out.textContent = '⏳ Antigravity AI is thinking...';
      const res = await fetch('/api/ide/ai_assist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          prompt,
          filename: cur ? cur.name : 'untitled',
          code: cur ? cur.content : '',
          error: includeErr ? ideLastError : ''
        })
      });
      const data = await res.json();
      out.textContent = data.reply || 'No response.';
      const m = (data.reply || '').match(/```[\w]*\n([\s\S]*?)```/);
      if (m) {
        ideLastAiCode = m[1];
        document.getElementById('ideApplyBtn').style.display = 'block';
      } else {
        document.getElementById('ideApplyBtn').style.display = 'none';
      }
    }

    function ideApplyCode() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur || !ideLastAiCode) return;
      cur.content = ideLastAiCode;
      cur.dirty = true;
      ideRenderTabs();
    }

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
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(text);
        copyBtn.textContent = '✓ Copied';
        setTimeout(() => copyBtn.textContent = '📋 Copy', 1500);
      };
      footer.appendChild(copyBtn);

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

    function scrollToBottom() {
      const sc = document.getElementById('chatScroll');
      sc.scrollTop = sc.scrollHeight;
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
          totalRowsEl.innerHTML = `Brains: ${s.total_rows.toLocaleString()} • Memories: <span id="memoryRows">${s.memory_rows}</span>`;
        }

        if (typeof data.offline_mode === 'boolean') {
          window._offlineMode = data.offline_mode;
          const oBtn = document.getElementById('offlineModeBtn');
          if (oBtn) {
            oBtn.textContent = data.offline_mode ? '✈️ OFFLINE ONLY' : '🌐 ONLINE';
            oBtn.style.borderColor = data.offline_mode ? '#10b981' : '#38bdf8';
            oBtn.style.color = data.offline_mode ? '#10b981' : '#38bdf8';
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
          const lSel = document.getElementById('langSelect');
          if (lSel && data.brains.languages && lSel.options.length <= 8) {
            const prevLang = data.brains.selected_language || lSel.value || 'auto';
            lSel.innerHTML = '';
            data.brains.languages.forEach(l => {
              const opt = document.createElement('option');
              opt.value = l.code;
              opt.textContent = l.code === 'auto' ? '🌐 Auto-Detect Language' : l.label;
              lSel.appendChild(opt);
            });
            lSel.value = prevLang;
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
        if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
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
      if (qBtn) qBtn.textContent = quantumModeEnabled ? '⚛️ Quantum Mode: ON' : '⚛️ Quantum Mode: OFF';
      await fetch('/api/quantum/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: quantumModeEnabled })
      });
      if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
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
      if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
    }

    async function selectLanguage() {
      const language = document.getElementById('langSelect').value;
      await fetch('/api/language/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language })
      });
      if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
    }

    async function toggleHarvester() {
      await fetch(harvesterRunning ? '/api/harvester/pause' : '/api/harvester/start', { method: 'POST' });
      if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
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

    async function sendMessage(e) {
      e.preventDefault();
      const text = promptInput.value.trim();
      if (!text) return;
      promptInput.value = '';
      promptInput.style.height = 'auto';

      const userIdx = saveMessageToHistory('user', text);
      const originChatId = currentChatId;
      pendingChats.add(originChatId);


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

    async function ideAiAssist(preset = null, includeErr = false) {
      const inp = document.getElementById('ideAiPrompt');
      const prompt = preset || inp.value.trim();
      if (!prompt) return;
      if (!preset) inp.value = '';
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const out = document.getElementById('ideAiOutput');
      out.textContent = '⏳ Antigravity AI is thinking...';
      const res = await fetch('/api/ide/ai_assist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          prompt,
          filename: cur ? cur.name : 'untitled',
          code: cur ? cur.content : '',
          error: includeErr ? ideLastError : ''
        })
      });
      const data = await res.json();
      out.textContent = data.reply || 'No response.';
      const m = (data.reply || '').match(/```[\w]*\n([\s\S]*?)```/);
      if (m) {
        ideLastAiCode = m[1];
        document.getElementById('ideApplyBtn').style.display = 'block';
      } else {
        document.getElementById('ideApplyBtn').style.display = 'none';
      }
    }

    function ideApplyCode() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur || !ideLastAiCode) return;
      cur.content = ideLastAiCode;
      cur.dirty = true;
      ideRenderTabs();
    }

    renderHistory();
      appendUserMessage(text, userIdx);
      appendTypingIndicator();

      try {
        const lang = document.getElementById('langSelect') ? document.getElementById('langSelect').value : 'auto';
        if (imageMode && !hasCodingIntent(text)) {
          const res = await fetch('/api/image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text, language: lang, quantum_mode: quantumModeEnabled })
          });
          const data = await res.json();
          pendingChats.delete(originChatId);
          const replyText = data.reply ?? data.answer ?? (data.error ? `⚠️ ${data.error}` : 'No response received.');
          const badge = data.badge || `⚡ ${data.latency_ms}ms • ${data.source} • 100% Offline`;
          const imgUrl = data.image_url || null;
          const aiIdx = saveMessageToHistory('ai', replyText, badge, imgUrl, originChatId);
          if (currentChatId === originChatId) {
            const typing = document.getElementById('typingRow');
            if (typing) typing.remove();
            appendAiMessage(replyText, badge, aiIdx, imgUrl);
          }
          if (imgUrl) loadGallery();
          if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
          return;
        }

        const typing = document.getElementById('typingRow');
        if (typing) typing.remove();

        const box = document.getElementById('chatBox');
        const row = document.createElement('div');
        row.className = 'msg-row ai';
        const avatar = document.createElement('div');
        avatar.className = 'ai-avatar';
        avatar.textContent = 'A';
        const body = document.createElement('div');
        body.className = 'msg-ai-body';
        const textDiv = document.createElement('div');
        textDiv.className = 'msg-ai-text';
        textDiv.innerHTML = '<span class="typing-cursor">▌</span>';
        const badgeDiv = document.createElement('div');
        badgeDiv.className = 'msg-badge';
        badgeDiv.textContent = '⚡ Streaming live...';
        body.appendChild(textDiv);
        body.appendChild(badgeDiv);
        row.appendChild(avatar);
        row.appendChild(body);
        box.appendChild(row);
        scrollToBottom();

        const selectedModel = document.getElementById('brainSelect') ? document.getElementById('brainSelect').value : 'auto';
        const thinkingLevel = document.getElementById('thinkingLevelSelect') ? document.getElementById('thinkingLevelSelect').value : 'mid';
        const res = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text, language: lang, model: selectedModel, thinking_level: thinkingLevel, quantum_mode: quantumModeEnabled, swarm_enabled: swarmModeEnabled })
        });
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
            const ev = JSON.parse(line.slice(6));
            if (ev.type === 'token') {
              fullText += ev.token;
              textDiv.innerHTML = formatText(fullText) + '<span class="typing-cursor">▌</span>';
              scrollToBottom();
            } else if (ev.type === 'done') {
              fullText = ev.reply || fullText;
              finalBadge = ev.badge || '';
            }
          }
        }
        pendingChats.delete(originChatId);
        textDiv.innerHTML = formatText(fullText || 'No response received.');
        autoRunChatCodeBlocks(textDiv);
        badgeDiv.textContent = finalBadge;
        saveMessageToHistory('ai', fullText, finalBadge, null, originChatId);
        if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
      } catch (err) {
        pendingChats.delete(originChatId);
        const errIdx = saveMessageToHistory('ai', 'Error communicating with local ARGON-OMEGA server.', 'Error', null, originChatId);
        if (currentChatId === originChatId) {
          const typing = document.getElementById('typingRow');
          if (typing) typing.remove();
          appendAiMessage('Error communicating with local ARGON-OMEGA server.', 'Error', errIdx);
        }
      }
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
        ed.value = `!python --version; Get-Location; dir`;
        const termIn = document.getElementById('terminalCmdInput');
        if (termIn) termIn.focus();
      }
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

    async function runSandbox() {
      const code = document.getElementById('sandboxEditor').value.trim();
      const out = document.getElementById('sandboxConsole');
      const frame = document.getElementById('sandboxFrame');
      if (!code) return;
      if (code.startsWith('<!DOCTYPE') || code.startsWith('<html') || code.includes('<canvas') || code.includes('<script>')) {
        frame.srcdoc = code;
        out.textContent = 'Rendered HTML5 App / Game in Live Canvas.';
        return;
      }
      out.textContent = 'Running in sandbox...';
      const res = await fetch('/api/sandbox/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });
      const data = await res.json();
      out.textContent = (data.stdout || '') + (data.stderr ? '\n[STDERR]\n' + data.stderr : '');
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
          <div style="margin:6px 0;font-size:0.85rem;">⚡ TTFT: <strong>${r.ttft_ms}ms</strong> • 🚀 Speed: <strong>${r.tok_per_sec} tok/s</strong> • ⏱️ ${r.ms_per_token} ms/tok</div>
          <div style="font-size:0.82rem;margin-bottom:4px;">Accuracy Score: <strong>${r.accuracy_score}%</strong></div>
          <div class="progress-bar"><div class="progress-fill" style="width:${r.accuracy_score}%;"></div></div>
        </div>`).join('') || '<div class="sub-metric">No results.</div>';
    }

    async function runLaptopAudit() {
      const out = document.getElementById('auditOutput');
      out.textContent = 'Running offline laptop security audit...';
      const res = await fetch('/api/security/audit', { method: 'POST' });
      const data = await res.json();
      out.textContent = data.report;
    }

    async function scanCode() {
      const code = document.getElementById('codeInput').value;
      const out = document.getElementById('sastOutput');
      out.textContent = 'Scanning code offline...';
      const res = await fetch('/api/security/sast', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });
      const data = await res.json();
      out.textContent = data.report;
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
      const data = await res.json();
      out.textContent = `--- PATCHES APPLIED ---\n${data.notes.join('\n')}\n\n--- PATCHED CODE ---\n${data.patched_code}`;
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
      const data = await res.json();
      out.textContent = data.results;
    }

    async function downloadCVE() {
      const out = document.getElementById('cveOutput');
      out.textContent = 'Downloading & indexing CVE/Security datasets into SSD ROM...';
      const res = await fetch('/api/security/cve_download', { method: 'POST' });
      const data = await res.json();
      out.textContent = `Downloaded & indexed ${data.inserted} security/CVE records into SSD ROM.`;
      if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
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

    function toggleTerminalDrawer() {
      const d = document.getElementById('terminalDrawer');
      const btn = document.getElementById('terminalToggleBtn');
      const open = d.style.display === 'none';
      d.style.display = open ? 'block' : 'none';
      btn.classList.toggle('active', open);
      if (open) document.getElementById('drawerInput').focus();
    }

    async function runDrawerCmd(presetMode = null, presetCode = null) {
      const mode = presetMode || document.getElementById('drawerMode').value;
      const inputEl = document.getElementById('drawerInput');
      const code = presetCode !== null ? presetCode : inputEl.value.trim();
      const outEl = document.getElementById('drawerOutput');
      if (!code) return;
      if (presetCode !== null) {
        document.getElementById('drawerMode').value = mode;
        inputEl.value = code;
      }
      outEl.style.color = '#94a3b8';
      outEl.textContent = '⏳ Executing...';
      try {
        const res = await fetch('/api/sandbox_run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode, code })
        });
        const data = await res.json();
        outEl.style.color = data.ok ? '#34d399' : '#f87171';
        const body = [data.stdout, data.stderr].filter(Boolean).join('\n') || '(No output)';
        outEl.textContent = `[${data.ok ? 'EXIT 0' : 'BLOCKED/ERR'} | ${data.elapsed_ms}ms]\n${body}`;
      } catch (e) {
        outEl.style.color = '#f87171';
        outEl.textContent = 'Error: ' + e;
      }
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

    async function ideAiAssist(preset = null, includeErr = false) {
      const inp = document.getElementById('ideAiPrompt');
      const prompt = preset || inp.value.trim();
      if (!prompt) return;
      if (!preset) inp.value = '';
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      const out = document.getElementById('ideAiOutput');
      out.textContent = '⏳ Antigravity AI is thinking...';
      const res = await fetch('/api/ide/ai_assist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          prompt,
          filename: cur ? cur.name : 'untitled',
          code: cur ? cur.content : '',
          error: includeErr ? ideLastError : ''
        })
      });
      const data = await res.json();
      out.textContent = data.reply || 'No response.';
      const m = (data.reply || '').match(/```[\w]*\n([\s\S]*?)```/);
      if (m) {
        ideLastAiCode = m[1];
        document.getElementById('ideApplyBtn').style.display = 'block';
      } else {
        document.getElementById('ideApplyBtn').style.display = 'none';
      }
    }

    function ideApplyCode() {
      const cur = ideOpenTabs.find(t => t.path === ideActivePath);
      if (!cur || !ideLastAiCode) return;
      cur.content = ideLastAiCode;
      cur.dirty = true;
      ideRenderTabs();
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
        if (data.error) return alert(data.error);
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
      const btn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Video'));
      if (btn) switchTab('video', btn);
      runVideoGen(filename, promptText || 'cinematic motion');
    }

    renderHistory();
    if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      const openBtn = document.getElementById('openSidebarBtn');
      if (sb) sb.classList.add('collapsed');
      if (openBtn) openBtn.style.display = 'flex';
    }
    fetchTelemetry();
    setInterval(fetchTelemetry, 2000);
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
          if (dl.ready && typeof loadStatus === 'function') loadStatus();
        }
      } catch (e) { clearInterval(timer); }
    }, 1500);
  }
  </script>
</body>
</html>
"""


_FOLDER_SIZE_CACHE = {"ts": 0.0, "mb": 0.0, "gb": 0.0}


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
            storage["folder_size_mb"] = _FOLDER_SIZE_CACHE["mb"]
            storage["folder_size_gb"] = _FOLDER_SIZE_CACHE["gb"]
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

    def do_POST(self) -> None:
        host_hdr = (self.headers.get("Host") or "").lower()
        origin_hdr = (self.headers.get("Origin") or "").lower()
        if host_hdr not in ("127.0.0.1:8080", "localhost:8080"):
            self.send_response(403)
            self.end_headers()
            self._safe_write(b'{"error": "Forbidden Host"}')
            return
        if origin_hdr and origin_hdr not in ("http://127.0.0.1:8080", "http://localhost:8080", "null"):
            self.send_response(403)
            self.end_headers()
            self._safe_write(b'{"error": "CSRF Origin Blocked by ARGON Guard"}')
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
            self._send_json(set_quantum_mode(bool(data.get("enabled", True))))
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
            self._send_json(
                {
                    "reply": f"Generated image for: **{img['prompt']}**",
                    "image_url": img["url"],
                    "image_filename": img["filename"],
                    "image_mode": img["mode"],
                    "latency_ms": img["latency_ms"],
                    "source": img["mode"],
                    "badge": f"🎨 {img['latency_ms']}ms | {img['mode']}",
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
          self._send_json(start_elite_model_download())
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
