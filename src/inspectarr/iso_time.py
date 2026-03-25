"""Parse ISO-8601 timestamps from Sonarr / Overseerr JSON into UTC epoch seconds."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_iso8601_utc_epoch(raw: Any) -> int | None:
    """Best-effort UTC epoch from Sonarr/Overseerr date strings; None if missing or invalid."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError, OSError):
        return None
