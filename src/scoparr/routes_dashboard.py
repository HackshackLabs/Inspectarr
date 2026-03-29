"""HTML dashboard routes."""

import asyncio
import logging
from datetime import datetime, time, timezone
from pathlib import Path
from time import monotonic, time as wall_time
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from scoparr.activity_cache import ActivitySnapshotCache
from scoparr.dashboard_config import build_template_globals
from scoparr.aggregate import merge_activity, merge_history_unpaged
from scoparr.history_cache import HistoryPageCache
from scoparr.history_health import enrich_history_server_statuses
from scoparr.live_streams import group_live_streams_by_server
from scoparr.history_resolution import history_row_is_uhd_playback
from scoparr.history_scope import crawl_trim_cutoff_epoch, resolve_upstream_history_dates
from scoparr.settings import Settings, get_settings
from scoparr.tautulli_client import TautulliClient

logger = logging.getLogger(__name__)

BROADSIDE_RANGE_MODE_COOKIE = "broadside_range_mode"
_VALID_BROADSIDE_RANGE_MODES = frozenset({"week", "all"})

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _template_ctx(request: Request, page_title: str | None = None, **extra: Any) -> dict[str, Any]:
    ctx = build_template_globals(page_title, csrf_token=getattr(request.state, "csrf_token", "") or "")
    ctx["request"] = request
    nav_current = str(extra.pop("nav_current", "") or "")
    ctx["nav_current"] = nav_current
    ctx.update(extra)
    return ctx
_activity_cache: ActivitySnapshotCache | None = None
_history_cache: HistoryPageCache | None = None
_history_refresh_tasks: dict[str, asyncio.Task] = {}
_history_timeout_retry_tasks: dict[str, asyncio.Task] = {}
_history_retry_due_monotonic: dict[str, float] = {}
_history_timeout_failure_streak: dict[str, int] = {}
_history_last_timeout_snapshot_epoch: dict[str, int | None] = {}
_history_next_retry_interval_seconds: dict[str, float | None] = {}

_HISTORY_BASE_CACHE_VERSION = 3


def _history_redirect_if_range_mode_missing(
    request: Request,
    range_mode: Literal["week", "all"] | None,
) -> RedirectResponse | None:
    """When the query omits range_mode, redirect once so URL carries the persisted (cookie) choice."""
    if range_mode is not None:
        return None
    raw = (request.cookies.get(BROADSIDE_RANGE_MODE_COOKIE) or "week").lower()
    pref = raw if raw in _VALID_BROADSIDE_RANGE_MODES else "week"
    q = dict(request.query_params)
    q["range_mode"] = pref
    return RedirectResponse(
        url=f"{request.url.path}?{urlencode(q)}",
        status_code=303,
    )


def _history_stamp_range_mode_cookie(response: HTMLResponse, range_mode: str) -> HTMLResponse:
    response.set_cookie(
        BROADSIDE_RANGE_MODE_COOKIE,
        range_mode,
        max_age=365 * 24 * 3600,
        path="/",
        samesite="lax",
        httponly=True,
    )
    return response


def _history_base_payload_ok(payload: dict[str, Any] | None) -> bool:
    return (
        isinstance(payload, dict)
        and int(payload.get("v") or 0) == _HISTORY_BASE_CACHE_VERSION
        and isinstance(payload.get("all_rows"), list)
        and isinstance(payload.get("server_statuses"), list)
    )


@router.get("/", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard(request: Request) -> HTMLResponse:
    """Render live merged activity dashboard."""
    settings = get_settings()
    cache = _get_activity_cache(settings)

    async def fetch_merged_activity() -> dict:
        client = TautulliClient(
            timeout_seconds=settings.request_timeout_seconds,
            max_parallel_servers=settings.upstream_max_parallel_servers,
            per_request_delay_seconds=settings.upstream_per_request_delay_seconds,
        )
        results = await client.fetch_all_activity(settings.tautulli_servers)
        return merge_activity(results)

    merged, cache_state, cache_age_seconds = await cache.get(fetch_merged_activity)
    sessions = merged["sessions"]
    total_streams = merged["total_streams"]
    server_statuses = sorted(
        merged["server_statuses"],
        key=lambda item: str(item.get("server_name") or "").lower(),
    )
    timed_out_servers = [s for s in server_statuses if str(s.get("status")) == "timeout"]
    if timed_out_servers:
        next_retry = cache.update_timeout_retry_state(
            has_timeouts=True,
            base_retry_seconds=settings.activity_timeout_retry_seconds,
        )
        cache.schedule_retry(fetch_merged_activity, retry_after_seconds=next_retry or settings.activity_timeout_retry_seconds)
    else:
        cache.update_timeout_retry_state(
            has_timeouts=False,
            base_retry_seconds=settings.activity_timeout_retry_seconds,
        )
    retry_countdown_seconds = cache.retry_countdown_seconds()
    retry_interval_seconds = cache.current_retry_interval_seconds() or settings.activity_timeout_retry_seconds
    server_max = max([int(s.get("stream_count", 0)) for s in server_statuses] + [1])
    media_counts: dict[str, int] = {}
    for session in sessions:
        key = str(session.get("media_type") or "unknown").lower()
        media_counts[key] = media_counts.get(key, 0) + 1
    media_rows = sorted(media_counts.items(), key=lambda item: item[1], reverse=True)
    live_streams_by_server = group_live_streams_by_server(sessions)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_template_ctx(
            request,
            "Deck Watch",
            nav_current="live",
            server_statuses=server_statuses,
            sessions=sessions,
            total_streams=total_streams,
            configured_servers=len(settings.tautulli_servers),
            updated_at=datetime.now(timezone.utc),
            cache_state=cache_state,
            cache_age_seconds=cache_age_seconds,
            timed_out_servers=timed_out_servers,
            retry_countdown_seconds=retry_countdown_seconds,
            retry_interval_seconds=retry_interval_seconds,
            server_stream_max=server_max,
            media_rows=media_rows,
            live_streams_by_server=live_streams_by_server,
        ),
    )


@router.get("/history", response_class=HTMLResponse, tags=["dashboard"])
async def history(
    request: Request,
    start: int = Query(default=0, ge=0),
    length: int = Query(default=50, ge=1, le=200_000),
    user: str | None = Query(default=None),
    media_type: str | None = Query(default=None),
    uhd_only: bool = Query(
        default=False,
        description="When true, keep only rows that look like 4K/UHD playback (Tautulli video metadata).",
    ),
    start_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    range_mode: Literal["week", "all"] | None = Query(
        default=None,
        description='Omitted: use last choice (cookie) or "week". "week" / "all" sent on every URL after redirect.',
    ),
    refresh: bool = Query(default=False),
) -> HTMLResponse:
    """Render merged history timeline with global ordering."""
    redir = _history_redirect_if_range_mode_missing(request, range_mode)
    if redir is not None:
        return redir
    assert range_mode is not None

    settings = get_settings()
    history_cache = _get_history_cache(settings)
    filters = {
        "user": user or "",
        "media_type": media_type or "",
        "uhd_only": uhd_only,
        "start_date": start_date or "",
        "end_date": end_date or "",
        "range_mode": range_mode,
    }
    upstream_after, upstream_before = resolve_upstream_history_dates(
        range_mode,
        filters["start_date"],
        filters["end_date"],
        week_days=settings.history_default_week_days,
    )
    scope_note = _history_scope_description(
        range_mode=range_mode,
        settings=settings,
        upstream_after=upstream_after,
        upstream_before=upstream_before,
    )
    if uhd_only:
        scope_note += " Showing 4K/UHD plays only (from Tautulli video height / resolution fields when present)."
    filters["scope_note"] = scope_note
    if history_cache.enabled:
        filters["scope_note"] += (
            " While cold storage is fresh, pagination and the 4K/UHD filter are applied from the cached merge (no extra Tautulli fetch)."
        )
    filters["upstream_after"] = upstream_after or ""
    filters["upstream_before"] = upstream_before or ""
    # Cold storage keys the upstream merge only; UHD, pagination, etc. apply in memory while fresh.
    base_cache_key_seed = "|".join(
        [
            f"history-v{_HISTORY_BASE_CACHE_VERSION}-base",
            ",".join(sorted([server.id for server in settings.tautulli_servers])),
            f"range_mode={range_mode}",
            f"upstream_after={upstream_after or ''}",
            f"upstream_before={upstream_before or ''}",
            f"user={filters['user']}",
            f"media_type={filters['media_type']}",
            f"start_date={filters['start_date']}",
            f"end_date={filters['end_date']}",
        ]
    )
    base_cache_key = history_cache.make_key(base_cache_key_seed)
    if refresh and history_cache.enabled:
        _history_cancel_timeout_retry(base_cache_key)
        _force_refresh_key(history_cache, base_cache_key, _history_refresh_tasks)

    async def compute_base_payload() -> dict[str, Any]:
        parallel = (
            min(settings.upstream_max_parallel_servers, settings.history_full_max_parallel_servers)
            if range_mode == "all"
            else settings.upstream_max_parallel_servers
        )
        per_delay = settings.upstream_per_request_delay_seconds + settings.history_additional_per_request_delay_seconds
        client = TautulliClient(
            timeout_seconds=settings.history_request_timeout_seconds,
            max_parallel_servers=parallel,
            per_request_delay_seconds=per_delay,
        )
        if range_mode == "all":
            page_size = settings.history_full_page_size
            inter_page = settings.history_full_inter_page_delay_seconds
            max_rows = settings.history_full_max_rows_per_server
        else:
            page_size = settings.history_week_page_size
            inter_page = settings.history_week_inter_page_delay_seconds
            max_rows = settings.history_week_max_rows_per_server

        stop_before = crawl_trim_cutoff_epoch(upstream_after)
        results = await client.fetch_all_history_crawled(
            settings.tautulli_servers,
            user=user,
            media_type=media_type,
            after=upstream_after,
            before=upstream_before,
            page_size=page_size,
            inter_page_delay_seconds=inter_page,
            max_rows_per_server=max_rows,
            stop_before_epoch=stop_before,
        )
        merged = merge_history_unpaged(results)
        filtered_rows = _apply_date_range(merged["rows"], start_date=start_date, end_date=end_date)
        statuses = [dict(s) for s in merged["server_statuses"]]
        _enrich_server_statuses_oldest_item(statuses, filtered_rows)
        return {
            "v": _HISTORY_BASE_CACHE_VERSION,
            "server_statuses": statuses,
            "all_rows": filtered_rows,
            "configured_servers": len(settings.tautulli_servers),
            "updated_at_epoch": int(datetime.now(timezone.utc).timestamp()),
        }

    if history_cache.enabled:
        base_cached, cache_state = await _history_cold_storage_resolve(
            history_cache=history_cache,
            cache_key=base_cache_key,
            ttl_seconds=settings.history_cache_ttl_seconds,
            compute_fn=compute_base_payload,
            task_registry=_history_refresh_tasks,
        )
        if base_cached is None:
            cache_payload = None
        else:
            cache_payload = _history_materialize_from_base(
                base_cached,
                uhd_only=uhd_only,
                start=start,
                length=length,
                filters=filters,
            )
    else:
        base_live = await compute_base_payload()
        cache_payload = _history_materialize_from_base(
            base_live,
            uhd_only=uhd_only,
            start=start,
            length=length,
            filters=filters,
        )
        cache_state = "live_compute"

    cold_storage_rebuilding = history_cache.enabled and cache_state == "cache_stale_rebuild"

    if cache_payload is None:
        loading_now = int(datetime.now(timezone.utc).timestamp())
        pending_statuses = [
            {
                "server_id": srv.id,
                "server_name": srv.name,
                "status": "pending",
                "error": None,
                "history_count": 0,
                "records_filtered": None,
                "records_total": None,
            }
            for srv in settings.tautulli_servers
        ]
        pending_cards = enrich_history_server_statuses(
            _sorted_history_server_statuses(pending_statuses),
            loading_now,
        )
        resp = templates.TemplateResponse(
            request=request,
            name="history.html",
            context=_template_ctx(
                request,
                "Broadside Range",
                nav_current="history",
                server_statuses=pending_cards,
                rows=[],
                configured_servers=len(settings.tautulli_servers),
                updated_at=datetime.now(timezone.utc),
                start=start,
                length=length,
                returned_rows=0,
                total_rows=0,
                next_start=None,
                prev_start=None,
                filters=filters,
                cache_state=cache_state,
                loading=True,
                refresh=refresh,
                timed_out_servers=[],
                retry_countdown_seconds=None,
                retry_interval_seconds=settings.history_timeout_retry_seconds,
                history_slow_crawl=range_mode == "all",
                history_week_days=settings.history_default_week_days,
                history_poll_ms=8000 if range_mode == "all" else 3000,
                cold_storage_rebuilding=False,
                history_cache_ttl_seconds=settings.history_cache_ttl_seconds,
            ),
        )
        return _history_stamp_range_mode_cookie(resp, range_mode)

    updated_at_epoch = int(cache_payload.get("updated_at_epoch", int(datetime.now(timezone.utc).timestamp())))
    updated_at = datetime.fromtimestamp(updated_at_epoch, tz=timezone.utc)
    server_statuses = enrich_history_server_statuses(
        _sorted_history_server_statuses(cache_payload.get("server_statuses") or []),
        updated_at_epoch,
    )
    timed_out_servers = [s for s in server_statuses if str(s.get("status")) == "timeout"]
    retry_countdown_seconds: int | None = None
    retry_interval_seconds = settings.history_timeout_retry_seconds
    if history_cache.enabled:
        if timed_out_servers:
            delay = _history_update_timeout_retry_state(
                cache_key=base_cache_key,
                snapshot_updated_at_epoch=updated_at_epoch,
                base_retry_seconds=settings.history_timeout_retry_seconds,
            )
            retry_interval_seconds = float(
                _history_next_retry_interval_seconds.get(base_cache_key) or settings.history_timeout_retry_seconds
            )
            _history_schedule_timeout_retry(
                cache_key=base_cache_key,
                delay_seconds=delay,
                history_cache=history_cache,
                compute_fn=compute_base_payload,
            )
            retry_countdown_seconds = _history_retry_countdown_seconds(base_cache_key)
        else:
            _history_cancel_timeout_retry(base_cache_key)

    resp = templates.TemplateResponse(
        request=request,
        name="history.html",
        context=_template_ctx(
            request,
            "Broadside Range",
            nav_current="history",
            server_statuses=server_statuses,
            rows=_with_humanized_history_rows(cache_payload["rows"]),
            configured_servers=cache_payload["configured_servers"],
            updated_at=updated_at,
            start=cache_payload["start"],
            length=cache_payload["length"],
            returned_rows=cache_payload["returned_rows"],
            total_rows=cache_payload["total_rows"],
            next_start=cache_payload["next_start"],
            prev_start=cache_payload["prev_start"],
            filters=filters,
            cache_state=cache_state,
            loading=False,
            refresh=refresh,
            timed_out_servers=timed_out_servers,
            retry_countdown_seconds=retry_countdown_seconds,
            retry_interval_seconds=retry_interval_seconds,
            history_slow_crawl=range_mode == "all",
            history_week_days=settings.history_default_week_days,
            history_poll_ms=8000 if range_mode == "all" else 3000,
            cold_storage_rebuilding=cold_storage_rebuilding,
            history_cache_ttl_seconds=settings.history_cache_ttl_seconds,
        ),
    )
    return _history_stamp_range_mode_cookie(resp, range_mode)


async def _history_cold_storage_resolve(
    *,
    history_cache: HistoryPageCache,
    cache_key: str,
    ttl_seconds: float,
    compute_fn: Callable[[], Awaitable[dict]],
    task_registry: dict[str, asyncio.Task],
) -> tuple[dict | None, str]:
    """
    Broadside Range cold storage: fresh TTL window, then serve stale snapshot and rebuild in background.
    """
    peeked = history_cache.peek(cache_key)
    payload: dict | None = None
    created_at = 0.0
    if peeked is not None:
        cand, created_at = peeked
        if _history_base_payload_ok(cand):
            payload = cand
        else:
            history_cache.delete(cache_key)

    if payload is None:
        task = task_registry.get(cache_key)
        if task is None or task.done():
            task_registry[cache_key] = asyncio.create_task(
                _refresh_cached_payload(history_cache, cache_key, compute_fn, task_registry)
            )
        return None, "refresh_pending"

    age = wall_time() - float(created_at)
    if age <= max(float(ttl_seconds), 0.0):
        return payload, "cache_hit"

    task = task_registry.get(cache_key)
    if task is None or task.done():
        task_registry[cache_key] = asyncio.create_task(
            _refresh_cached_payload(history_cache, cache_key, compute_fn, task_registry)
        )
    return payload, "cache_stale_rebuild"


def _history_scope_description(
    *,
    range_mode: Literal["week", "all"],
    settings: Settings,
    upstream_after: str | None,
    upstream_before: str | None,
) -> str:
    if range_mode == "week":
        parts = [
            f"Rolling window: last {settings.history_default_week_days} days (UTC) sent to Tautulli as after={upstream_after!r}"
        ]
        if upstream_before:
            parts.append(f"before={upstream_before!r}")
        return "; ".join(parts) + "."
    cap = settings.history_full_max_rows_per_server
    parts = [
        "All time: paginated upstream fetch with strict throttling",
        f"cap {cap} rows per server",
        f"parallel servers ≤ {settings.history_full_max_parallel_servers}",
    ]
    if upstream_after:
        parts.append(f"after={upstream_after!r}")
    if upstream_before:
        parts.append(f"before={upstream_before!r}")
    return "; ".join(parts) + "."


def _apply_date_range(rows: list[dict], start_date: str | None, end_date: str | None) -> list[dict]:
    start_epoch = _date_start_epoch(start_date)
    end_epoch = _date_end_epoch(end_date)
    if start_epoch is None and end_epoch is None:
        return rows

    filtered: list[dict] = []
    for row in rows:
        row_epoch = int(row.get("canonical_utc_epoch", 0))
        if start_epoch is not None and row_epoch < start_epoch:
            continue
        if end_epoch is not None and row_epoch > end_epoch:
            continue
        filtered.append(row)
    return filtered


def _date_start_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _date_end_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        inclusive = datetime.combine(dt.date(), time.max, tzinfo=timezone.utc)
        return int(inclusive.timestamp())
    except ValueError:
        return None


def _get_activity_cache(settings: Settings) -> ActivitySnapshotCache:
    global _activity_cache
    if _activity_cache is None:
        _activity_cache = ActivitySnapshotCache(
            ttl_seconds=settings.activity_cache_ttl_seconds,
            stale_seconds=settings.activity_cache_stale_seconds,
        )
    return _activity_cache


def _get_history_cache(settings: Settings) -> HistoryPageCache:
    global _history_cache
    if _history_cache is None:
        _history_cache = HistoryPageCache(db_path=settings.history_cache_db_path)
    return _history_cache


def _with_humanized_history_rows(rows: list[dict]) -> list[dict]:
    transformed: list[dict] = []
    for row in rows:
        new_row = dict(row)
        new_row["canonical_utc_display"] = _format_epoch_utc(row.get("canonical_utc_epoch"))
        transformed.append(new_row)
    return transformed


def _format_epoch_utc(value: object) -> str:
    try:
        epoch = int(value) if value is not None else 0
        if epoch <= 0:
            return "-"
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OSError):
        return "-"


def _enrich_server_statuses_oldest_item(statuses: list[dict], rows: list[dict]) -> None:
    """Smallest canonical_utc_epoch per server_id among rows (oldest play in this snapshot)."""
    oldest_by_sid: dict[str, int] = {}
    for row in rows:
        sid = str(row.get("server_id") or "")
        try:
            ep = int(row.get("canonical_utc_epoch") or 0)
        except (TypeError, ValueError):
            continue
        if not sid or ep <= 0:
            continue
        cur = oldest_by_sid.get(sid)
        if cur is None or ep < cur:
            oldest_by_sid[sid] = ep
    for s in statuses:
        sid = str(s.get("server_id") or "")
        om = oldest_by_sid.get(sid)
        s["history_oldest_item_epoch"] = om
        s["history_oldest_item_display"] = _format_epoch_utc(om) if om else "—"


def _history_materialize_from_base(
    base: dict[str, Any],
    *,
    uhd_only: bool,
    start: int,
    length: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict] = list(base.get("all_rows") or [])
    if uhd_only:
        rows = [r for r in rows if history_row_is_uhd_playback(r)]
    page_start = max(start, 0)
    page_end = page_start + length
    paged_rows = rows[page_start:page_end]
    next_start = page_start + length if len(rows) > page_end else None
    prev_start = max(page_start - length, 0) if page_start > 0 else None
    return {
        "server_statuses": [dict(s) for s in (base.get("server_statuses") or [])],
        "rows": paged_rows,
        "configured_servers": int(base.get("configured_servers") or 0),
        "updated_at_epoch": int(base.get("updated_at_epoch") or 0),
        "start": page_start,
        "length": length,
        "returned_rows": len(paged_rows),
        "total_rows": len(rows),
        "next_start": next_start,
        "prev_start": prev_start,
        "filters": dict(filters),
    }


async def _refresh_cached_payload(
    cache: HistoryPageCache,
    cache_key: str,
    compute_fn: Callable[[], Awaitable[dict]],
    task_registry: dict[str, asyncio.Task],
) -> None:
    try:
        payload = await compute_fn()
        cache.set(cache_key=cache_key, payload=payload)
    finally:
        task_registry.pop(cache_key, None)


def _force_refresh_key(
    cache: HistoryPageCache,
    cache_key: str,
    task_registry: dict[str, asyncio.Task],
) -> None:
    """Cancel in-flight task (if any) and clear cached payload for a key."""
    task = task_registry.pop(cache_key, None)
    if task and not task.done():
        task.cancel()
    cache.delete(cache_key)


def _sorted_history_server_statuses(statuses: list[dict]) -> list[dict]:
    return sorted(
        statuses,
        key=lambda item: (
            str(item.get("server_name") or "").lower(),
            str(item.get("server_id") or "").lower(),
        ),
    )


def _history_update_timeout_retry_state(
    cache_key: str,
    snapshot_updated_at_epoch: int,
    base_retry_seconds: float,
) -> float:
    """Track per-snapshot timeout streaks and return delay until the next retry attempt."""
    base = max(base_retry_seconds, 0.0)
    last_epoch = _history_last_timeout_snapshot_epoch.get(cache_key)
    if last_epoch != snapshot_updated_at_epoch:
        streak = _history_timeout_failure_streak.get(cache_key, 0) + 1
        _history_timeout_failure_streak[cache_key] = streak
        _history_last_timeout_snapshot_epoch[cache_key] = snapshot_updated_at_epoch
        multiplier = min(2 ** max(streak - 1, 0), 4)
        _history_next_retry_interval_seconds[cache_key] = base * multiplier

    interval = float(_history_next_retry_interval_seconds.get(cache_key) or base)
    return interval


def _history_clear_timeout_retry_state(cache_key: str) -> None:
    _history_timeout_failure_streak.pop(cache_key, None)
    _history_last_timeout_snapshot_epoch.pop(cache_key, None)
    _history_next_retry_interval_seconds.pop(cache_key, None)


def _history_cancel_timeout_retry(cache_key: str) -> None:
    task = _history_timeout_retry_tasks.pop(cache_key, None)
    if task and not task.done():
        task.cancel()
    _history_retry_due_monotonic.pop(cache_key, None)
    _history_clear_timeout_retry_state(cache_key)


def _history_retry_countdown_seconds(cache_key: str) -> int | None:
    due = _history_retry_due_monotonic.get(cache_key)
    if due is None:
        return None
    remaining = int(due - monotonic())
    return max(remaining, 0)


def _history_schedule_timeout_retry(
    cache_key: str,
    delay_seconds: float,
    history_cache: HistoryPageCache,
    compute_fn: Callable[[], Awaitable[dict]],
) -> None:
    delay = max(delay_seconds, 0.0)
    task = _history_timeout_retry_tasks.get(cache_key)
    if task and not task.done():
        return
    _history_retry_due_monotonic[cache_key] = monotonic() + delay
    _history_timeout_retry_tasks[cache_key] = asyncio.create_task(
        _history_delayed_recompute(delay, history_cache, cache_key, compute_fn)
    )


async def _history_delayed_recompute(
    delay_seconds: float,
    history_cache: HistoryPageCache,
    cache_key: str,
    compute_fn: Callable[[], Awaitable[dict]],
) -> None:
    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        payload = await compute_fn()
        history_cache.set(cache_key=cache_key, payload=payload)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Scheduled history timeout retry failed for cache_key=%s", cache_key)
    finally:
        _history_retry_due_monotonic.pop(cache_key, None)
        _history_timeout_retry_tasks.pop(cache_key, None)
