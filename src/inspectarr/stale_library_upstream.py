"""Live upstream (Tautulli / Sonarr) progress for Cold Storage snapshot builds."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Literal

UpstreamName = Literal["tautulli", "sonarr"]

_lock = Lock()
_state: dict[str, Any] = {
    "busy": False,
    "upstream": None,
    "phase": "",
    "started_epoch": 0,
    "updated_epoch": 0,
    "tautulli_by_id": {},
    "sonarr_episode_fetches": 0,
    "sonarr_series_list_count": 0,
    "sonarr_last": None,
}


def begin_stale_library_upstream_trace(
    tautulli_placeholders: list[tuple[str, str]] | None = None,
) -> None:
    """Start upstream trace. Optional ``tautulli_placeholders`` is ``(server_id, server_name)`` per configured server so UI cards exist before the first HTTP call."""
    now = int(time.time())
    with _lock:
        _state["busy"] = True
        _state["upstream"] = None
        _state["phase"] = "Starting…"
        _state["started_epoch"] = now
        _state["updated_epoch"] = now
        _state["tautulli_by_id"] = {}
        _state["sonarr_episode_fetches"] = 0
        _state["sonarr_series_list_count"] = 0
        _state["sonarr_last"] = None
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


def end_stale_library_upstream_trace() -> None:
    now = int(time.time())
    with _lock:
        _state["busy"] = False
        _state["upstream"] = None
        _state["phase"] = ""
        _state["updated_epoch"] = now
        _state["tautulli_by_id"] = {}
        _state["sonarr_episode_fetches"] = 0
        _state["sonarr_series_list_count"] = 0
        _state["sonarr_last"] = None


def set_stale_library_upstream_phase(upstream: UpstreamName | None, phase: str) -> None:
    now = int(time.time())
    with _lock:
        _state["upstream"] = upstream
        _state["phase"] = phase
        _state["updated_epoch"] = now


def record_stale_library_tautulli(
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


def bump_stale_library_tautulli_history_rows(server_id: str, server_name: str, delta: int) -> None:
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


def set_stale_library_sonarr_series_list_count(count: int) -> None:
    now = int(time.time())
    with _lock:
        _state["updated_epoch"] = now
        _state["upstream"] = "sonarr"
        _state["sonarr_series_list_count"] = max(0, int(count))


def record_stale_library_sonarr(label: str, http_status: int, ok: bool, *, is_episode_list: bool = False) -> None:
    now = int(time.time())
    with _lock:
        _state["updated_epoch"] = now
        _state["upstream"] = "sonarr"
        if is_episode_list:
            _state["sonarr_episode_fetches"] = int(_state["sonarr_episode_fetches"]) + 1
        _state["sonarr_last"] = {
            "label": str(label),
            "http_status": int(http_status),
            "ok": bool(ok),
            "last_response_epoch": now,
        }


def stale_library_upstream_snapshot() -> dict[str, Any]:
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
        sonarr_last = _state.get("sonarr_last")
        if isinstance(sonarr_last, dict):
            sonarr_last = dict(sonarr_last)
        return {
            "busy": bool(_state.get("busy")),
            "upstream": _state.get("upstream"),
            "phase": str(_state.get("phase") or ""),
            "started_epoch": int(_state.get("started_epoch") or 0),
            "updated_epoch": int(_state.get("updated_epoch") or 0),
            "tautulli_servers": servers,
            "sonarr": {
                "episode_fetches_completed": int(_state.get("sonarr_episode_fetches") or 0),
                "series_list_count": int(_state.get("sonarr_series_list_count") or 0),
                "last": sonarr_last,
            },
        }
