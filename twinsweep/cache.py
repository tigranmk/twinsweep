"""Persistent hash cache backed by SQLite.

Re-scanning a large photo or music library is dominated by disk reads. The
cache stores digests keyed by (path, size, mtime_ns, kind); if any of those
change, the entry simply misses and the file is hashed again, so a stale cache
can never produce a wrong answer for a modified file.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional


def default_cache_path() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "twinsweep", "hashes.sqlite3")


class HashCache:
    """Thread-safe get/put wrapper around a single SQLite connection."""

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS hashes (
                   path TEXT NOT NULL,
                   size INTEGER NOT NULL,
                   mtime_ns INTEGER NOT NULL,
                   kind TEXT NOT NULL,
                   digest TEXT NOT NULL,
                   PRIMARY KEY (path, kind)
               )"""
        )
        self._conn.commit()
        self.hits = 0
        self.misses = 0

    def get(self, path: str, size: int, mtime_ns: int, kind: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT size, mtime_ns, digest FROM hashes WHERE path=? AND kind=?",
                (path, kind),
            ).fetchone()
        if row and row[0] == size and row[1] == mtime_ns:
            self.hits += 1
            return row[2]
        self.misses += 1
        return None

    def put(self, path: str, size: int, mtime_ns: int, kind: str, digest: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO hashes VALUES (?, ?, ?, ?, ?)",
                (path, size, mtime_ns, kind, digest),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
