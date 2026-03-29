"""Detect UHD / ~4K playback from Tautulli history row fields."""

from __future__ import annotations

from typing import Any, Mapping


def _intish(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def history_row_is_uhd_playback(row: Mapping[str, Any]) -> bool:
    """
    True when the row likely represents 4K / UHD video, using fields Tautulli exposes
    on history (e.g. video_height, video_resolution, video_full_resolution).
    """
    h = _intish(row.get("video_height")) or _intish(row.get("height"))
    w = _intish(row.get("video_width")) or _intish(row.get("width"))
    if h is not None and h >= 2000:
        return True
    if w is not None and w >= 3000:
        return True

    for key in (
        "video_resolution",
        "video_full_resolution",
        "stream_video_resolution",
        "stream_video_full_resolution",
    ):
        s = str(row.get(key) or "").strip().lower()
        if not s:
            continue
        if any(tok in s for tok in ("4k", "2160", "uhd", "3840", "4320")):
            return True
        if s in ("2160", "4k"):
            return True
    return False
