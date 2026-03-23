"""Persistent incremental cache for TV library inventory indexing."""

import json
import sqlite3
from pathlib import Path
from time import time


class InventoryCache:
    """SQLite store for indexed TV inventory and per-section cursor progress."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path).expanduser())
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_items (
                    server_id TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    rating_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (server_id, item_type, rating_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_progress (
                    server_id TEXT NOT NULL,
                    section_id TEXT NOT NULL,
                    next_start INTEGER NOT NULL,
                    records_total INTEGER NOT NULL,
                    completed INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (server_id, section_id)
                )
                """
            )
            conn.commit()

    def get_next_start(self, server_id: str, section_id: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT next_start, records_total, COALESCE(completed, 0)
                FROM inventory_progress
                WHERE server_id = ? AND section_id = ?
                """,
                (server_id, section_id),
            ).fetchone()
            if not row:
                return 0
            next_s, total, completed = int(row[0]), int(row[1] or 0), int(row[2] or 0)
            # Legacy rows: completed sections saved next_start=0, which re-triggered full re-walks.
            if completed and total > 0 and next_s == 0:
                return total
            return max(next_s, 0)

    def set_progress(self, server_id: str, section_id: str, next_start: int, records_total: int, completed: bool) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO inventory_progress (server_id, section_id, next_start, records_total, completed, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id, section_id) DO UPDATE SET
                    next_start = excluded.next_start,
                    records_total = excluded.records_total,
                    completed = excluded.completed,
                    updated_at = excluded.updated_at
                """,
                (server_id, section_id, max(next_start, 0), max(records_total, 0), 1 if completed else 0, time()),
            )
            conn.commit()

    def upsert_items(self, server_id: str, item_type: str, items: list[dict]) -> None:
        rows = []
        for item in items:
            rating_key = str(item.get("rating_key") or "")
            if not rating_key:
                continue
            rows.append((server_id, item_type, rating_key, json.dumps(item), time()))
        if not rows:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO inventory_items (server_id, item_type, rating_key, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(server_id, item_type, rating_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()

    def get_items(self, server_id: str, item_type: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM inventory_items
                WHERE server_id = ? AND item_type = ?
                """,
                (server_id, item_type),
            ).fetchall()
        out: list[dict] = []
        for row in rows:
            try:
                out.append(json.loads(row[0]))
            except (TypeError, ValueError):
                continue
        return out

    def get_server_progress(self, server_id: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT section_id, next_start, records_total, completed
                FROM inventory_progress
                WHERE server_id = ?
                ORDER BY section_id
                """,
                (server_id,),
            ).fetchall()
        return [
            {
                "section_id": str(section_id),
                "next_start": int(next_start),
                "records_total": int(records_total),
                "completed": bool(completed),
            }
            for section_id, next_start, records_total, completed in rows
        ]
