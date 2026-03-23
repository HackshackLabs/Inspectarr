"""History date-range helpers for Tautulli get_history parameters."""

from datetime import datetime, timedelta, timezone
from typing import Literal

RangeMode = Literal["week", "all"]


def utc_date_days_ago(days: int) -> str:
    """Return YYYY-MM-DD for (now UTC - days)."""
    dt = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
    return dt.date().isoformat()


def resolve_upstream_history_dates(
    range_mode: RangeMode,
    start_date: str,
    end_date: str,
    *,
    week_days: int = 7,
) -> tuple[str | None, str | None]:
    """
    Map UI range mode and optional date inputs to Tautulli get_history `after` / `before`.

    Empty strings are treated as unset. Dates are YYYY-MM-DD in UTC calendar terms.
    """
    start = (start_date or "").strip()
    end = (end_date or "").strip()
    before = end if end else None

    if range_mode == "week":
        week_start = utc_date_days_ago(week_days)
        after = start if start else week_start
        return after, before

    after = start if start else None
    return after, before


def crawl_trim_cutoff_epoch(after: str | None) -> int | None:
    """UTC start-of-day epoch for `after` date, used to stop paginated crawls early."""
    if not after:
        return None
    try:
        return int(datetime.fromisoformat(after).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None
