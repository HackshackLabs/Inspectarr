"""SQLite-backed cache for rendered history query contexts."""

import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from time import time


class HistoryPageCache:
    """Small SQLite cache for history page payloads."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._enabled = bool(db_path.strip())
        if not self._enabled:
            return
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history_cache (
                    cache_key TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def make_key(self, seed: str) -> str:
        return sha256(seed.encode("utf-8")).hexdigest()

    def get(self, cache_key: str, ttl_seconds: float) -> dict | None:
        if not self._enabled:
            return None
        path = Path(self.db_path).expanduser()
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT created_at, payload_json FROM history_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            created_at, payload_json = row
            if (time() - float(created_at)) > max(ttl_seconds, 0.0):
                conn.execute("DELETE FROM history_cache WHERE cache_key = ?", (cache_key,))
                conn.commit()
                return None
            return json.loads(payload_json)

    def set(self, cache_key: str, payload: dict) -> None:
        if not self._enabled:
            return
        path = Path(self.db_path).expanduser()
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                INSERT INTO history_cache (cache_key, created_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    created_at = excluded.created_at,
                    payload_json = excluded.payload_json
                """,
                (cache_key, time(), json.dumps(payload)),
            )
            conn.commit()

    def delete(self, cache_key: str) -> None:
        """Delete cached payload for a key if present."""
        if not self._enabled:
            return
        path = Path(self.db_path).expanduser()
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM history_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
