import csv
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from database import check_storage_safety, insert_rom_batch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "harvester_state.json")
IMPORT_DIR = os.path.join(BASE_DIR, "import_to_rom")
PROCESSED_DIR = os.path.join(IMPORT_DIR, "processed")

FILE_BATCH_SIZE = 5000
API_BATCH_SIZE = 2000
CHUNK_BYTES = 1024 * 1024  # 1MB streaming chunks

DIRECT_PACKS = [
    {
        "name": "Dolly-15k",
        "url": "https://huggingface.co/datasets/databricks/databricks-dolly-15k/resolve/main/databricks-dolly-15k.jsonl",
        "format": "jsonl",
        "category": "General Intelligence",
    },
    {
        "name": "Alpaca-52k",
        "url": "https://huggingface.co/datasets/yahma/alpaca-cleaned/resolve/main/alpaca_data_cleaned.json",
        "format": "json_array",
        "category": "Instruction",
    },
    {
        "name": "CodeAlpaca-20k",
        "url": "https://huggingface.co/datasets/sahil2801/CodeAlpaca-20k/resolve/main/code_alpaca_20k.json",
        "format": "json_array",
        "category": "Coding",
    },
    {
        "name": "OpenHermes-2.5",
        "url": "https://huggingface.co/datasets/teknium/OpenHermes-2.5/resolve/main/openhermes2_5.json",
        "format": "json_array",
        "category": "GPT-4 Brains",
    },
]

PARALLEL_HF_DATASETS = [
    {"id": "openai/gsm8k", "config": "main", "split": "train", "category": "Math"},
    {"id": "Open-Orca/OpenOrca", "config": "default", "split": "train", "category": "Deep Reasoning"},
    {"id": "meta-math/MetaMathQA", "config": "default", "split": "train", "category": "Math"},
    {"id": "glaiveai/glaive-code-assistant-v3", "config": "default", "split": "train", "category": "Coding"},
    {"id": "wikimedia/wikipedia", "config": "20231101.en", "split": "train", "category": "World Facts"},
]


def ensure_import_dirs() -> None:
    os.makedirs(IMPORT_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)


def _extract_qa(row: dict, category: str) -> tuple[str, str, str] | None:
    """Normalize diverse dataset schemas into (question, answer, category)."""
    if not isinstance(row, dict):
        return None

    q = (
        row.get("question")
        or row.get("instruction")
        or row.get("prompt")
        or row.get("query")
        or row.get("title")
        or ""
    )
    if row.get("input") and q:
        q = f"{q}\n\nInput: {row['input']}"
    elif row.get("context") and q:
        q = f"{q}\n\nContext: {row['context']}"

    a = (
        row.get("answer")
        or row.get("response")
        or row.get("output")
        or row.get("completion")
        or row.get("correct_answer")
        or row.get("text")
        or ""
    )

    if (not q or not a) and isinstance(row.get("conversations"), list):
        convs = row["conversations"]
        for i, msg in enumerate(convs):
            role = (msg.get("from") or msg.get("role") or "").lower()
            val = msg.get("value") or msg.get("content") or ""
            if role in ("human", "user") and not q:
                q = val
                if i + 1 < len(convs):
                    a = convs[i + 1].get("value") or convs[i + 1].get("content") or ""
                break

    q_str = str(q).strip()
    a_str = str(a).strip()
    if not q_str or not a_str:
        return None
    return (q_str[:2000], a_str[:8000], category)


class FirehoseHarvester:
    def __init__(self) -> None:
        ensure_import_dirs()
        self._running = False
        self._lock = threading.Lock()
        self._status = "Paused"
        self._current_dataset = "Idle"
        self._last_log = "Firehose ready."
        self._rows_session = 0
        self._bytes_session = 0
        self._start_time = 0.0
        self._offsets = self._load_state()

    def _load_state(self) -> dict[str, int]:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return {k: int(v) for k, v in data.items()}
            except (OSError, ValueError):
                pass
        return {ds["id"]: 0 for ds in PARALLEL_HF_DATASETS}

    def _save_state(self) -> None:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._offsets, f, indent=2)
        except OSError:
            pass

    def get_status(self) -> dict:
        with self._lock:
            elapsed = max(0.1, time.perf_counter() - self._start_time) if self._running and self._start_time else 1.0
            brains_per_sec = round(self._rows_session / elapsed, 1) if self._running else 0.0
            mb_per_sec = round((self._bytes_session / (1024 * 1024)) / elapsed, 2) if self._running else 0.0
            return {
                "running": self._running,
                "status": self._status,
                "current_dataset": self._current_dataset,
                "last_log": self._last_log,
                "rows_session": self._rows_session,
                "brains_per_sec": brains_per_sec,
                "mb_per_sec": mb_per_sec,
            }

    def start(self) -> dict:
        with self._lock:
            if self._running:
                return self.get_status()
            safety = check_storage_safety()
            if not safety["safe"]:
                self._status = "Safety Paused"
                self._last_log = "🛡️ Safety Limit Reached: Keeping 45GB free for laptop speed."
                return self.get_status()
            self._running = True
            self._status = "Firehose Active"
            self._rows_session = 0
            self._bytes_session = 0
            self._start_time = time.perf_counter()
            self._last_log = "Launching 4-thread Direct CDN + 10-thread HF Firehose..."
            threading.Thread(target=self._run_firehose, daemon=True).start()
            return self.get_status()

    def pause(self) -> dict:
        with self._lock:
            self._running = False
            self._status = "Paused"
            self._last_log = "Firehose paused by user."
            return self.get_status()

    def _is_running(self) -> bool:
        with self._lock:
            return self._running

    def _record_progress(self, rows: int, nbytes: int, dataset_name: str, msg: str) -> None:
        with self._lock:
            self._rows_session += rows
            self._bytes_session += nbytes
            self._current_dataset = dataset_name
            self._last_log = msg

    def _check_safety_or_stop(self) -> bool:
        if not self._is_running():
            return False
        safety = check_storage_safety()
        if not safety["safe"]:
            with self._lock:
                self._running = False
                self._status = "Safety Paused"
                self._last_log = "🛡️ Safety Limit Reached: Keeping 45GB free for laptop speed."
            return False
        return True

    def _run_firehose(self) -> None:
        # Phase 1: 4-worker Direct CDN File Streamer for Dolly, Alpaca, CodeAlpaca, OpenHermes
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self._stream_direct_pack, pack) for pack in DIRECT_PACKS]
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass

        # Phase 2: 10-thread Parallel HF Rows API Streamer
        while self._check_safety_or_stop():
            for ds in PARALLEL_HF_DATASETS:
                if not self._check_safety_or_stop():
                    break
                self._stream_hf_parallel_10(ds)

    def _stream_direct_pack(self, pack: dict) -> None:
        name = pack["name"]
        url = pack["url"]
        fmt = pack["format"]
        cat = pack["category"]
        req = urllib.request.Request(url, headers={"User-Agent": "Argon-ROM-Firehose/2.0"})

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if fmt == "jsonl":
                    batch: list[tuple[str, str, str]] = []
                    buffer = b""
                    while self._check_safety_or_stop():
                        chunk = resp.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        buffer += chunk
                        lines = buffer.split(b"\n")
                        buffer = lines.pop()
                        for raw_line in lines:
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if not line:
                                continue
                            try:
                                qa = _extract_qa(json.loads(line), cat)
                                if qa:
                                    batch.append(qa)
                            except json.JSONDecodeError:
                                continue
                            if len(batch) >= FILE_BATCH_SIZE:
                                inserted = insert_rom_batch(batch)
                                self._record_progress(inserted, len(chunk), name, f"Streamed {inserted} from {name}")
                                batch.clear()
                    if batch and self._check_safety_or_stop():
                        inserted = insert_rom_batch(batch)
                        self._record_progress(inserted, 0, name, f"Completed {name}")

                elif fmt == "json_array":
                    # Incremental JSON array object parser using raw_decode in 1MB chunks
                    decoder = json.JSONDecoder()
                    text_buf = ""
                    batch = []
                    in_array = False
                    while self._check_safety_or_stop():
                        chunk = resp.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        text_buf += chunk.decode("utf-8", errors="replace")
                        if not in_array:
                            idx = text_buf.find("[")
                            if idx == -1:
                                continue
                            text_buf = text_buf[idx + 1 :]
                            in_array = True

                        pos = 0
                        n = len(text_buf)
                        while pos < n:
                            while pos < n and text_buf[pos] in " \t\r\n,":
                                pos += 1
                            if pos >= n or text_buf[pos] == "]":
                                break
                            try:
                                obj, next_pos = decoder.raw_decode(text_buf, pos)
                                pos = next_pos
                                qa = _extract_qa(obj, cat)
                                if qa:
                                    batch.append(qa)
                                if len(batch) >= FILE_BATCH_SIZE:
                                    inserted = insert_rom_batch(batch)
                                    self._record_progress(inserted, len(chunk), name, f"Streamed {inserted} from {name}")
                                    batch.clear()
                            except json.JSONDecodeError:
                                break
                        text_buf = text_buf[pos:]
                    if batch and self._check_safety_or_stop():
                        inserted = insert_rom_batch(batch)
                        self._record_progress(inserted, 0, name, f"Completed {name}")
        except Exception as exc:
            self._record_progress(0, 0, name, f"Skipped {name}: {exc}")

    def _fetch_hf_slice(self, ds: dict, offset: int, length: int = 100) -> tuple[list[tuple[str, str, str]], int]:
        params = urllib.parse.urlencode(
            {
                "dataset": ds["id"],
                "config": ds["config"],
                "split": ds["split"],
                "offset": offset,
                "length": length,
            }
        )
        url = f"https://datasets-server.huggingface.co/rows?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Argon-ROM-Firehose/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        rows_out: list[tuple[str, str, str]] = []
        for item in payload.get("rows", []):
            qa = _extract_qa(item.get("row", {}), ds["category"])
            if qa:
                rows_out.append(qa)
        return rows_out, len(raw)

    def _stream_hf_parallel_10(self, ds: dict) -> None:
        ds_id = ds["id"]
        base_offset = self._offsets.get(ds_id, 0)
        offsets = [base_offset + i * 100 for i in range(10)]
        combined: list[tuple[str, str, str]] = []
        total_bytes = 0

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(self._fetch_hf_slice, ds, off, 100) for off in offsets]
            for f in futures:
                try:
                    rows, nbytes = f.result()
                    combined.extend(rows)
                    total_bytes += nbytes
                except Exception:
                    pass

        if combined and self._check_safety_or_stop():
            # Write in up to 2,000-row bulk transactions
            for i in range(0, len(combined), API_BATCH_SIZE):
                chunk = combined[i : i + API_BATCH_SIZE]
                inserted = insert_rom_batch(chunk)
                self._record_progress(inserted, total_bytes, ds_id, f"10-Thread HF: +{inserted} from {ds_id}")
            self._offsets[ds_id] = base_offset + 1000
            self._save_state()


def scan_import_folder() -> dict:
    """Stream .jsonl, .json, .csv, and .txt files from import_to_rom/ into rom_brain."""
    ensure_import_dirs()
    files = [
        f
        for f in os.listdir(IMPORT_DIR)
        if os.path.isfile(os.path.join(IMPORT_DIR, f))
        and f.lower().endswith((".jsonl", ".json", ".csv", ".txt"))
    ]
    total_imported = 0
    processed_files: list[str] = []

    for fname in files:
        fpath = os.path.join(IMPORT_DIR, fname)
        safety = check_storage_safety()
        if not safety["safe"]:
            return {
                "imported": total_imported,
                "files": processed_files,
                "message": "🛡️ Safety Limit Reached: Keeping 45GB free for laptop speed.",
            }

        batch: list[tuple[str, str, str]] = []
        ext = os.path.splitext(fname)[1].lower()
        try:
            if ext == ".jsonl":
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            qa = _extract_qa(json.loads(line), "Local Import")
                            if qa:
                                batch.append(qa)
                        except json.JSONDecodeError:
                            continue
                        if len(batch) >= FILE_BATCH_SIZE:
                            total_imported += insert_rom_batch(batch)
                            batch.clear()
            elif ext == ".json":
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                items = data if isinstance(data, list) else [data]
                for obj in items:
                    qa = _extract_qa(obj, "Local Import")
                    if qa:
                        batch.append(qa)
                    if len(batch) >= FILE_BATCH_SIZE:
                        total_imported += insert_rom_batch(batch)
                        batch.clear()
            elif ext == ".csv":
                with open(fpath, "r", encoding="utf-8", errors="replace", newline="") as f:
                    for row in csv.DictReader(f):
                        qa = _extract_qa(row, "Local Import")
                        if qa:
                            batch.append(qa)
                        if len(batch) >= FILE_BATCH_SIZE:
                            total_imported += insert_rom_batch(batch)
                            batch.clear()
            elif ext == ".txt":
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        text = line.strip()
                        if len(text) < 10:
                            continue
                        batch.append((text[:120], text[:4000], "Local Text"))
                        if len(batch) >= FILE_BATCH_SIZE:
                            total_imported += insert_rom_batch(batch)
                            batch.clear()

            if batch:
                total_imported += insert_rom_batch(batch)
                batch.clear()

            dest = os.path.join(PROCESSED_DIR, fname)
            if os.path.exists(dest):
                base, e = os.path.splitext(fname)
                dest = os.path.join(PROCESSED_DIR, f"{base}_{int(time.time())}{e}")
            shutil.move(fpath, dest)
            processed_files.append(fname)
        except Exception:
            continue

    return {
        "imported": total_imported,
        "files": processed_files,
        "message": f"Imported {total_imported} rows from {len(processed_files)} file(s).",
    }


harvester = FirehoseHarvester()
