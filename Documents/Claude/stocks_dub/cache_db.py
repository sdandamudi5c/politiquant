"""
Centralised SQLite cache — replaces all the individual JSON cache files.

Why SQLite over JSON:
  - Concurrent reads: WAL mode allows multiple threads to read simultaneously
  - No corruption: ACID writes mean a mid-write crash leaves the old data intact
  - Faster: indexed lookups vs full JSON deserialise for every read
  - Smaller: SQLite compresses better than pretty-printed JSON
  - Single file: one cache.db instead of 10+ *.json files

API
---
    import cache_db
    data = cache_db.get("fundamentals", "AAPL")   # → dict or None
    cache_db.set("fundamentals", "AAPL", result)  # stores dict as JSON blob
    cache_db.delete_old("fundamentals", 86400)    # prune entries older than 24h

Namespaces used across the project:
    fundamentals, insider, institutional, trends, fred, sentiment,
    sector, sector_stocks, party
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

_DIR     = os.path.dirname(os.path.abspath(__file__))
_DB_FILE = os.path.join(_DIR, "cache.db")

# One connection per thread (SQLite connections are not thread-safe)
_local = threading.local()
_INIT_LOCK = threading.Lock()
_INITIALISED = False


def _conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating it if needed."""
    global _INITIALISED
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(_DB_FILE, check_same_thread=False, timeout=10)
        # WAL mode: readers don't block writers and vice-versa
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
        _local.conn = c

    # Create table once (idempotent)
    if not _INITIALISED:
        with _INIT_LOCK:
            if not _INITIALISED:
                _local.conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache (
                        namespace TEXT NOT NULL,
                        key       TEXT NOT NULL,
                        value     TEXT NOT NULL,
                        cached_ts REAL NOT NULL,
                        PRIMARY KEY (namespace, key)
                    )
                """)
                _local.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ns_ts ON cache(namespace, cached_ts)"
                )
                _local.conn.commit()
                _INITIALISED = True

    return _local.conn


def get(namespace: str, key: str) -> "dict | None":
    """Return cached dict for (namespace, key), or None if not found."""
    try:
        row = _conn().execute(
            "SELECT value FROM cache WHERE namespace=? AND key=?",
            (namespace, key),
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def set(namespace: str, key: str, data: dict) -> None:
    """Store data dict under (namespace, key). Overwrites existing entry."""
    try:
        value = json.dumps(data, default=str)
        ts    = datetime.utcnow().timestamp()
        c = _conn()
        c.execute(
            "INSERT OR REPLACE INTO cache (namespace, key, value, cached_ts) VALUES (?,?,?,?)",
            (namespace, key, value, ts),
        )
        c.commit()
    except Exception:
        pass


def delete_old(namespace: str, older_than_seconds: float) -> int:
    """Delete entries in namespace older than given TTL. Returns deleted count."""
    try:
        cutoff = datetime.utcnow().timestamp() - older_than_seconds
        c = _conn()
        cur = c.execute(
            "DELETE FROM cache WHERE namespace=? AND cached_ts < ?",
            (namespace, cutoff),
        )
        c.commit()
        return cur.rowcount
    except Exception:
        return 0


def clear(namespace: str) -> None:
    """Delete all entries in a namespace (useful for testing / forced refresh)."""
    try:
        c = _conn()
        c.execute("DELETE FROM cache WHERE namespace=?", (namespace,))
        c.commit()
    except Exception:
        pass
