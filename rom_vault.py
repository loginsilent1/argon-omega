import json
import urllib.parse
import urllib.request

from database import check_storage_safety, insert_rom_batch, search_rom

CVE_DATASETS = [
    {"id": "CyberNative/Code_Vulnerability_Security_DPO", "config": "default", "split": "train", "category": "CVE-Security"},
    {"id": "AlicanKiraz0/Cybersecurity-Dataset-v1", "config": "default", "split": "train", "category": "Cybersecurity"},
]


def download_cve_datasets(max_rows_per_ds: int = 1000) -> dict:
    """Stream public Cybersecurity & CVE datasets into the SSD FTS5 Vault."""
    safety = check_storage_safety()
    if not safety["safe"]:
        return {"inserted": 0, "message": "🛡️ Safety Limit Reached: Keeping 45GB free."}

    total_inserted = 0
    for ds in CVE_DATASETS:
        for offset in range(0, max_rows_per_ds, 100):
            params = urllib.parse.urlencode(
                {
                    "dataset": ds["id"],
                    "config": ds["config"],
                    "split": ds["split"],
                    "offset": offset,
                    "length": 100,
                }
            )
            url = f"https://datasets-server.huggingface.co/rows?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "Argon-Omega-Vault/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                rows_batch: list[tuple[str, str, str]] = []
                for item in payload.get("rows", []):
                    r = item.get("row", {})
                    q = r.get("question") or r.get("prompt") or r.get("instruction") or r.get("vulnerability") or ""
                    a = r.get("chosen") or r.get("answer") or r.get("response") or r.get("output") or ""
                    if q and a:
                        rows_batch.append((str(q)[:2000], str(a)[:8000], ds["category"]))
                if not rows_batch:
                    break
                total_inserted += insert_rom_batch(rows_batch)
            except Exception:
                break

    return {
        "inserted": total_inserted,
        "message": f"Ingested {total_inserted} Cybersecurity & CVE patterns into SSD Vault.",
    }


def search_cve_vault(query: str, limit: int = 8) -> dict:
    """Search the SSD FTS5 Vault for CVE & vulnerability patterns in <1ms."""
    hits, elapsed_ms = search_rom(query, limit=limit)
    return {"results": hits, "latency_ms": elapsed_ms}
