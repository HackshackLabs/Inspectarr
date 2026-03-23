"""Per-server last-successful history fetch tracking for dashboard cards."""

from datetime import datetime, timezone

_last_ok_epoch_by_server: dict[str, int] = {}


def enrich_history_server_statuses(statuses: list[dict], snapshot_epoch: int) -> list[dict]:
    """
    Attach last_ok_at_* fields for each server card.

    When status is ``ok`` for this snapshot, refresh the stored epoch for that ``server_id``.
    When degraded, keep the previous epoch so operators see how stale success is.
    """
    out: list[dict] = []
    for raw in statuses:
        s = dict(raw)
        sid = str(s.get("server_id") or "")
        if sid and str(s.get("status")) == "ok":
            _last_ok_epoch_by_server[sid] = snapshot_epoch
        last = _last_ok_epoch_by_server.get(sid) if sid else None
        s["last_ok_at_epoch"] = last
        s["last_ok_at_display"] = format_last_ok_display(last)
        out.append(s)
    return out


def format_last_ok_display(epoch: int | None) -> str:
    if epoch is None:
        return "— (no successful fetch yet in this app process)"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
