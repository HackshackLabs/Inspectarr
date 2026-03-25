"""Aggregation helpers for merging server activity results."""

import re
from datetime import datetime, timezone

from scoparr.models import ActivityFetchResult, HistoryFetchResult


def merge_activity(results: list[ActivityFetchResult]) -> dict:
    """Merge sessions and include per-server status information."""
    merged_sessions: list[dict] = []
    server_statuses: list[dict] = []

    for result in results:
        server_statuses.append(
            {
                "server_id": result.server_id,
                "server_name": result.server_name,
                "status": result.status,
                "error": result.error,
                "stream_count": len(result.sessions),
            }
        )
        for session in result.sessions:
            if not isinstance(session, dict):
                continue
            row = dict(session)
            row["server_id"] = result.server_id
            row["server_name"] = result.server_name
            merged_sessions.append(row)

    merged_sessions.sort(
        key=lambda item: (
            str(item.get("friendly_name") or item.get("user") or "").lower(),
            str(item.get("grandparent_title") or item.get("title") or "").lower(),
        )
    )

    return {
        "server_statuses": server_statuses,
        "sessions": merged_sessions,
        "total_streams": len(merged_sessions),
    }


def merge_history(results: list[HistoryFetchResult], start: int = 0, length: int = 50) -> dict:
    """Merge history rows from all servers with canonical UTC sorting."""
    merged_rows: list[dict] = []
    server_statuses: list[dict] = []

    for result in results:
        server_statuses.append(
            {
                "server_id": result.server_id,
                "server_name": result.server_name,
                "status": result.status,
                "error": result.error,
                "history_count": len(result.rows),
                "records_filtered": result.records_filtered,
                "records_total": result.records_total,
            }
        )
        for row in result.rows:
            if not isinstance(row, dict):
                continue
            normalized = dict(row)
            normalized["server_id"] = result.server_id
            normalized["server_name"] = result.server_name
            normalized["canonical_utc_epoch"] = _extract_canonical_utc_epoch(row)
            merged_rows.append(normalized)

    merged_rows.sort(key=lambda item: item.get("canonical_utc_epoch", 0), reverse=True)
    total_rows = len(merged_rows)
    page_start = max(start, 0)
    page_end = page_start + max(length, 1)
    paged_rows = merged_rows[page_start:page_end]

    server_statuses.sort(
        key=lambda item: (
            str(item.get("server_name") or "").lower(),
            str(item.get("server_id") or "").lower(),
        )
    )

    return {
        "server_statuses": server_statuses,
        "rows": paged_rows,
        "total_rows": total_rows,
        "start": page_start,
        "length": max(length, 1),
        "returned_rows": len(paged_rows),
    }


def merge_history_rows_all(results: list[HistoryFetchResult]) -> list[dict]:
    """Merge every history row from all servers (no pagination), newest-first by canonical UTC."""
    merged_rows: list[dict] = []
    for result in results:
        for row in result.rows:
            if not isinstance(row, dict):
                continue
            normalized = dict(row)
            normalized["server_id"] = result.server_id
            normalized["server_name"] = result.server_name
            normalized["canonical_utc_epoch"] = _extract_canonical_utc_epoch(row)
            merged_rows.append(normalized)
    merged_rows.sort(key=lambda item: item.get("canonical_utc_epoch", 0), reverse=True)
    return merged_rows


def canonical_utc_epoch_for_row(row: dict) -> int:
    """Public wrapper for row timestamp normalization."""
    return _extract_canonical_utc_epoch(row)


def epoch_to_utc_display(epoch: int) -> str:
    if epoch <= 0:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def tvdb_id_from_guid(guid: object) -> int | None:
    """Parse TVDB series id from a Plex/Tautulli guid (thetvdb agent URLs)."""
    s = str(guid or "").strip()
    if not s:
        return None
    if "thetvdb" not in s.lower():
        return None
    match = re.search(r"thetvdb://(\d+)", s, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_canonical_utc_epoch(row: dict) -> int:
    """Normalize available history timestamp fields into UTC epoch seconds."""
    for key in ("started", "date", "stopped"):
        value = row.get(key)
        parsed = _parse_epoch(value)
        if parsed is not None:
            return parsed

    for key in ("started_at", "date_time"):
        value = row.get(key)
        parsed = _parse_iso_datetime(value)
        if parsed is not None:
            return parsed

    return 0


def _parse_epoch(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None
