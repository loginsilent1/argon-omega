import os
import shutil
import sqlite3
import string
import threading
import time
from pathlib import Path

MAX_ROM_GB = 65.0
MIN_FREE_DISK_GB = 45.0

_DB_LOCK = threading.RLock()
_CURRENT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "argon_rom.db"))

STARTER_FACTS = [
    (
        "What is Argon-ROM?",
        "Argon-ROM is a Space-Time Tradeoff AI system that offloads intelligence to a 65GB+ memory-mapped SQLite FTS5 disk index, achieving <0.5ms SSD response times with <30MB RAM and 0% GPU.",
        "System",
    ),
    (
        "How does Space-Time Tradeoff work in AI?",
        "Instead of running billions of floating-point matrix multiplications in RAM/GPU at inference time, Space-Time Tradeoff pre-stores vast knowledge on SSD/HDD and uses BM25 full-text indexing and memory-mapped I/O for instant retrieval.",
        "Architecture",
    ),
    (
        "What is Python's Global Interpreter Lock (GIL)?",
        "The GIL is a mutex in CPython that allows only one thread to execute Python bytecode at a time. I/O-bound tasks like disk reads and network requests release the GIL, while CPU-bound tasks can use multiprocessing.",
        "Coding",
    ),
    (
        "How do I reverse a string in Python?",
        "Use slice notation: `reversed_str = my_string[::-1]`. This creates a reversed copy in O(n) time.",
        "Coding",
    ),
    (
        "What is the time complexity of binary search?",
        "Binary search has O(log n) worst-case and average-case time complexity, and O(1) space complexity for iterative implementations on a sorted array.",
        "Math",
    ),
    (
        "What is the speed of light in a vacuum?",
        "The speed of light in a vacuum (c) is exactamente 299,792,458 meters per second (approximately 300,000 km/s).",
        "Science",
    ),
    (
        "What is SQLite FTS5?",
        "FTS5 is a SQLite virtual table module that provides full-text search with Porter stemming and BM25 relevance ranking, enabling sub-millisecond keyword lookups across tens of gigabytes of text.",
        "Database",
    ),
    (
        "What is BM25 ranking?",
        "BM25 (Best Matching 25) is a probabilistic ranking function used by search engines to estimate the relevance of documents to a search query based on term frequency, inverse document frequency, and document length.",
        "Algorithms",
    ),
    (
        "How do HTTP status codes 200, 404, and 500 differ?",
        "200 OK means the request succeeded. 404 Not Found means the requested resource does not exist on the server. 500 Internal Server Error means the server encountered an unexpected condition.",
        "Web",
    ),
    (
        "What is the Pythagorean theorem?",
        "In a right-angled triangle, the square of the hypotenuse (c) equals the sum of the squares of the other two sides (a and b): a^2 + b^2 = c^2.",
        "Math",
    ),
]


def get_db_path() -> str:
    with _DB_LOCK:
        return _CURRENT_DB_PATH


def set_db_path(new_dir: str) -> str:
    """Switch the active database directory (e.g., to another drive) and initialize it."""
    global _CURRENT_DB_PATH
    with _DB_LOCK:
        target_dir = os.path.abspath(new_dir)
        os.makedirs(target_dir, exist_ok=True)
        new_path = os.path.join(target_dir, "argon_rom.db")
        _CURRENT_DB_PATH = new_path
    init_db()
    return _CURRENT_DB_PATH


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with maximum SSD speed & strict 4MB RAM cap."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(path, timeout=60.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = OFF;")       # Maximum SSD write speed
    conn.execute("PRAGMA temp_store = FILE;")
    conn.execute("PRAGMA cache_size = -4000;")       # Max 4MB RAM cache
    conn.execute("PRAGMA mmap_size = 30000000000;")  # 0.5ms SSD direct read
    return conn


def purge_corrupted_rows(conn: sqlite3.Connection) -> int:
    """Delete any old corrupted Pollinations error rows."""
    cur = conn.execute(
        """
        DELETE FROM rom_brain
        WHERE answer LIKE '%reached its budget%'
           OR answer LIKE '%Topping up the wallet%';
        """
    )
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def init_db() -> None:
    """Create FTS5 tables, purge corrupted Pollinations rows, and seed starter facts."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS rom_brain USING fts5(
                    question,
                    answer,
                    category,
                    tokenize='porter'
                );
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS user_memory USING fts5(
                    fact,
                    timestamp,
                    tokenize='porter'
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verified_neural_cache (
                    prompt_norm TEXT PRIMARY KEY,
                    response TEXT NOT NULL,
                    model TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            purge_corrupted_rows(conn)
            row = conn.execute("SELECT COUNT(*) AS cnt FROM rom_brain;").fetchone()
            if row and row["cnt"] == 0:
                conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
                conn.executemany(
                    "INSERT INTO rom_brain (question, answer, category) VALUES (?, ?, ?);",
                    STARTER_FACTS,
                )
                conn.execute("COMMIT;")
        finally:
            conn.close()


def detect_drives() -> list[dict]:
    """Detect available drives/partitions and their free space in GB."""
    drives = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                try:
                    usage = shutil.disk_usage(drive_path)
                    drives.append(
                        {
                            "drive": drive_path,
                            "path": os.path.join(drive_path, "ArgonROM"),
                            "total_gb": round(usage.total / (1024 ** 3), 2),
                            "free_gb": round(usage.free / (1024 ** 3), 2),
                        }
                    )
                except OSError:
                    continue
    else:
        for mount in ["/", "/mnt", "/media", str(Path.home())]:
            if os.path.exists(mount):
                try:
                    usage = shutil.disk_usage(mount)
                    drives.append(
                        {
                            "drive": mount,
                            "path": os.path.join(mount, "ArgonROM") if mount != "/" else os.path.dirname(get_db_path()),
                            "total_gb": round(usage.total / (1024 ** 3), 2),
                            "free_gb": round(usage.free / (1024 ** 3), 2),
                        }
                    )
                except OSError:
                    continue
    return drives


def _get_db_size_bytes(db_path: str) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    return total


def check_storage_safety() -> dict:
    """Return DB size, free disk space, row counts, and whether writes are safe."""
    db_path = get_db_path()
    db_dir = os.path.dirname(db_path) or "."
    os.makedirs(db_dir, exist_ok=True)

    usage = shutil.disk_usage(db_dir)
    free_gb = usage.free / (1024 ** 3)
    db_bytes = _get_db_size_bytes(db_path)
    db_mb = db_bytes / (1024 ** 2)
    db_gb = db_bytes / (1024 ** 3)

    total_rows = 0
    memory_rows = 0
    try:
        conn = get_connection(db_path)
        try:
            r1 = conn.execute("SELECT COUNT(*) AS cnt FROM rom_brain;").fetchone()
            r2 = conn.execute("SELECT COUNT(*) AS cnt FROM user_memory;").fetchone()
            total_rows = r1["cnt"] if r1 else 0
            memory_rows = r2["cnt"] if r2 else 0
        finally:
            conn.close()
    except sqlite3.Error:
        pass

    safe = (free_gb >= MIN_FREE_DISK_GB) and (db_gb < MAX_ROM_GB)
    reason = "OK"
    if free_gb < MIN_FREE_DISK_GB:
        reason = f"Free disk space ({free_gb:.2f} GB) is below {MIN_FREE_DISK_GB} GB safety limit."
    elif db_gb >= MAX_ROM_GB:
        reason = f"ROM DB size ({db_gb:.2f} GB) reached {MAX_ROM_GB} GB cap."

    return {
        "safe": safe,
        "reason": reason,
        "db_path": db_path,
        "db_size_mb": round(db_mb, 2),
        "db_size_gb": round(db_gb, 4),
        "max_rom_gb": MAX_ROM_GB,
        "free_disk_gb": round(free_gb, 2),
        "min_free_disk_gb": MIN_FREE_DISK_GB,
        "total_rows": total_rows,
        "memory_rows": memory_rows,
    }


def _sanitize_fts_query(text: str) -> str:
    """Convert raw user text into a safe FTS5 OR query."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) > 1]
    if not tokens:
        tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:16])


def search_rom(query: str, limit: int = 5) -> tuple[list[dict], float]:
    """Query rom_brain with BM25 ranking and return (results, elapsed_ms)."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return [], 0.0

    t0 = time.perf_counter()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT question, answer, category, bm25(rom_brain) AS score
            FROM rom_brain
            WHERE rom_brain MATCH ?
            ORDER BY score
            LIMIT ?;
            """,
            (fts_query, limit),
        ).fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        results = [
            {
                "question": r["question"],
                "answer": r["answer"],
                "category": r["category"],
                "score": float(r["score"]),
            }
            for r in rows
        ]
        return results, round(elapsed_ms, 2)
    except sqlite3.Error:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return [], round(elapsed_ms, 2)
    finally:
        conn.close()


def insert_rom_batch(rows: list[tuple[str, str, str]]) -> int:
    """Bulk-insert rows inside BEGIN EXCLUSIVE TRANSACTION for maximum SSD throughput."""
    if not rows:
        return 0
    safety = check_storage_safety()
    if not safety["safe"]:
        return 0

    with _DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            conn.executemany(
                "INSERT INTO rom_brain (question, answer, category) VALUES (?, ?, ?);",
                rows,
            )
            conn.execute("COMMIT;")
            return len(rows)
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                pass
            return 0
        finally:
            conn.close()


def add_user_memory(fact: str) -> bool:
    """Save a personal user fact to user_memory."""
    safety = check_storage_safety()
    if not safety["safe"]:
        return False
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with _DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            conn.execute(
                "INSERT INTO user_memory (fact, timestamp) VALUES (?, ?);",
                (fact.strip(), ts),
            )
            conn.execute("COMMIT;")
            return True
        finally:
            conn.close()


def search_user_memory(query: str, limit: int = 3, allow_fallback: bool = False) -> list[str]:
    """Retrieve relevant personal facts from user_memory without bleeding unrelated topics."""
    fts_query = _sanitize_fts_query(query)
    conn = get_connection()
    try:
        if fts_query:
            rows = conn.execute(
                """
                SELECT fact FROM user_memory
                WHERE user_memory MATCH ?
                ORDER BY bm25(user_memory)
                LIMIT ?;
                """,
                (fts_query, limit),
            ).fetchall()
            if rows:
                return [r["fact"] for r in rows]
        if allow_fallback:
            rows = conn.execute(
                "SELECT fact FROM user_memory ORDER BY rowid DESC LIMIT ?;",
                (limit,),
            ).fetchall()
            return [r["fact"] for r in rows]
        return []
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _normalize_prompt(prompt: str, lang: str = "en") -> str:
    norm = " ".join(prompt.lower().strip().split())
    if not norm:
        return ""
    lang_clean = (lang or "en").strip().lower()
    return f"{lang_clean}::{norm}"


def get_verified_cache(prompt: str, lang: str = "en") -> tuple[str, str] | None:
    """Return (response, model) from verified_neural_cache in ~0.5ms if exact match exists."""
    norm = _normalize_prompt(prompt, lang)
    if not norm:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT response, model FROM verified_neural_cache WHERE prompt_norm = ? LIMIT 1;",
            (norm,),
        ).fetchone()
        if row:
            return row["response"], row["model"]
        return None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def set_verified_cache(prompt: str, response: str, model: str, lang: str = "en") -> None:
    """Save a verified neural response to SSD cache."""
    norm = _normalize_prompt(prompt, lang)
    if not norm or not response:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with _DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            conn.execute(
                """
                INSERT OR REPLACE INTO verified_neural_cache (prompt_norm, response, model, timestamp)
                VALUES (?, ?, ?, ?);
                """,
                (norm, response, model, ts),
            )
            conn.execute("COMMIT;")
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                pass
        finally:
            conn.close()


def wipe_corrupted_caches() -> dict:
    """Clear verified_neural_cache and user_memory to eliminate stale/bleeding context."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            c1 = conn.execute("DELETE FROM verified_neural_cache;").rowcount
            c2 = conn.execute("DELETE FROM user_memory;").rowcount
            conn.execute("COMMIT;")
            return {"cleared_cache": c1 or 0, "cleared_memory": c2 or 0}
        except sqlite3.Error:
            return {"cleared_cache": 0, "cleared_memory": 0}
        finally:
            conn.close()
