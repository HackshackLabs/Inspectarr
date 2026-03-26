"""Live upstream (Tautulli / Radarr) progress while stale 4K movie snapshots build."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Literal

UpstreamName = Literal["tautulli", "radarr", "overseerr"]

_lock = Lock()
_state: dict[str, Any] = {
    "busy": False,
    "upstream": None,
    "phase": "",
    "started_epoch": 0,
    "updated_epoch": 0,
    "tautulli_by_id": {},
    "radarr_last": None,
    "radarr_movie_list_count": 0,
}


def begin_stale_4k_movies_upstream_trace(
    tautulli_placeholders: list[tuple[str, str]] | None = None,
) -> None:
    now = int(time.time())
    with _lock:
        _state["busy"] = True
        _state["upstream"] = None
        _state["phase"] = "Starting…"
        _state["started_epoch"] = now
        _state["updated_epoch"] = now
        _state["tautulli_by_id"] = {}
        _state["radarr_last"] = None
        _state["radarr_movie_list_count"] = 0
        if tautulli_placeholders:
            by = _state["tautulli_by_id"]
            for sid_raw, name_raw in tautulli_placeholders:
                sid = str(sid_raw or "unknown")
                by[sid] = {
                    "server_id": sid,
                    "server_name": str(name_raw or sid),
                    "last_cmd": "",
                    "last_http_status": None,
                    "last_ok": None,
                    "last_response_epoch": None,
                    "history_rows_accumulated": 0,
                    "last_history_page_rows": None,
                }


def end_stale_4k_movies_upstream_trace() -> None:
    now = int(time.time())
    with _lock:
        _state["busy"] = False
        _state["upstream"] = None
        _state["phase"] = ""
        _state["updated_epoch"] = now
        _state["tautulli_by_id"] = {}
        _state["radarr_last"] = None
        _state["radarr_movie_list_count"] = 0


def set_stale_4k_movies_upstream_phase(upstream: UpstreamName | None, phase: str) -> None:
    now = int(time.time())
    with _lock:
        _state["upstream"] = upstream
        _state["phase"] = phase
        _state["updated_epoch"] = now


def record_stale_4k_movies_tautulli(
    server_id: str,
    server_name: str,
    cmd: str,
    http_status: int | None,
    ok: bool,
) -> None:
    now = int(time.time())
    with _lock:
        _state["updated_epoch"] = now
        _state["upstream"] = "tautulli"
        by = _state["tautulli_by_id"]
        sid = str(server_id or "unknown")
        cur = dict(by.get(sid) or {})
        cur["server_id"] = sid
        cur["server_name"] = str(server_name or sid)
        cur["last_cmd"] = str(cmd or "")
        cur["last_http_status"] = http_status
        cur["last_ok"] = bool(ok)
        cur["last_response_epoch"] = now
        if "history_rows_accumulated" not in cur:
            cur["history_rows_accumulated"] = 0
        if "last_history_page_rows" not in cur:
            cur["last_history_page_rows"] = None
        by[sid] = cur


def bump_stale_4k_movies_tautulli_history_rows(server_id: str, server_name: str, delta: int) -> None:
    now = int(time.time())
    delta_i = max(0, int(delta))
    with _lock:
        _state["updated_epoch"] = now
        _state["upstream"] = "tautulli"
        by = _state["tautulli_by_id"]
        sid = str(server_id or "unknown")
        cur = dict(by.get(sid) or {})
        cur["server_id"] = sid
        cur["server_name"] = str(server_name or sid)
        cur["last_history_page_rows"] = delta_i
        cur["history_rows_accumulated"] = int(cur.get("history_rows_accumulated") or 0) + delta_i
        cur["last_response_epoch"] = now
        by[sid] = cur


def record_stale_4k_movies_radarr(label: str, http_status: int, ok: bool) -> None:
    now = int(time.time())
    with _lock:
        _state["updated_epoch"] = now
        _state["upstream"] = "radarr"
        _state["radarr_last"] = {
            "label": str(label),
            "http_status": int(http_status),
            "ok": bool(ok),
            "last_response_epoch": now,
        }


def set_stale_4k_movies_radarr_movie_list_count(count: int) -> None:
    """After ``GET /api/v3/movie`` succeeds; mirrors Sonarr series list count in Horizon Watch upstream."""
    now = int(time.time())
    with _lock:
        _state["updated_epoch"] = now
        _state["upstream"] = "radarr"
        _state["radarr_movie_list_count"] = max(0, int(count))


def stale_4k_movies_upstream_snapshot() -> dict[str, Any]:
    with _lock:
        raw_t = _state.get("tautulli_by_id") or {}
        servers = sorted(
            (dict(v) for v in raw_t.values() if isinstance(v, dict)),
            key=lambda x: (str(x.get("server_name") or "").lower(), str(x.get("server_id") or "")),
        )
        for row in servers:
            row["history_rows_accumulated"] = int(row.get("history_rows_accumulated") or 0)
            lhp = row.get("last_history_page_rows")
            row["last_history_page_rows"] = None if lhp is None else int(lhp)
        radarr_last = _state.get("radarr_last")
        if isinstance(radarr_last, dict):
            radarr_last = dict(radarr_last)
        return {
            "busy": bool(_state.get("busy")),
            "upstream": _state.get("upstream"),
            "phase": str(_state.get("phase") or ""),
            "started_epoch": int(_state.get("started_epoch") or 0),
            "updated_epoch": int(_state.get("updated_epoch") or 0),
            "tautulli_servers": servers,
            "radarr": {
                "last": radarr_last,
                "movie_list_count": int(_state.get("radarr_movie_list_count") or 0),
            },
        }
