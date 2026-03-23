"""HTML dashboard routes."""

import asyncio
import logging
from datetime import datetime, time, timezone
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Awaitable, Callable, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from inspectarr.activity_cache import ActivitySnapshotCache
from inspectarr.dashboard_config import build_template_globals
from inspectarr.aggregate import (
    build_library_unwatched_tv_report,
    build_unwatched_media_report,
    epoch_to_utc_display,
    merge_activity,
    merge_history,
    merge_history_rows_all,
)
from inspectarr.history_cache import HistoryPageCache
from inspectarr.history_health import enrich_history_server_statuses
from inspectarr.live_streams import group_live_streams_by_server
from inspectarr.history_scope import crawl_trim_cutoff_epoch, resolve_upstream_history_dates
from inspectarr.inventory_cache import InventoryCache
from inspectarr.models import InventoryFetchResult
from inspectarr.report_export import build_export_body, build_export_filename, media_type_for_format
from inspectarr.settings import (
    Settings,
    TautulliServer,
    get_settings,
    plex_mapped_tautulli_server_ids,
    plex_per_server_actions_available,
    sonarr_is_configured,
)
from inspectarr.sonarr_client import (
    filter_library_inventory_results_by_sonarr_disk,
    prune_library_unwatched_report_show_seasons_without_sonarr_files,
)
from inspectarr.tautulli_client import TautulliClient

logger = logging.getLogger(__name__)

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
_inventory_cache: InventoryCache | None = None
_insights_cache: HistoryPageCache | None = None
_history_refresh_tasks: dict[str, asyncio.Task] = {}
_insights_refresh_tasks: dict[str, asyncio.Task] = {}
_history_timeout_retry_tasks: dict[str, asyncio.Task] = {}
_history_retry_due_monotonic: dict[str, float] = {}
_history_timeout_failure_streak: dict[str, int] = {}
_history_last_timeout_snapshot_epoch: dict[str, int | None] = {}
_history_next_retry_interval_seconds: dict[str, float | None] = {}


class ExportFormat(str, Enum):
    txt = "txt"
    csv = "csv"
    json = "json"
    xml = "xml"


class LibraryUnwatchedExportGroup(str, Enum):
    cumulative_shows = "cumulative_shows"
    cumulative_seasons = "cumulative_seasons"
    cumulative_episodes = "cumulative_episodes"
    server_shows = "server_shows"
    server_seasons = "server_seasons"
    server_episodes = "server_episodes"


class UnwatchedInsightsExportGroup(str, Enum):
    cumulative_stale = "cumulative_stale"
    server_stale = "server_stale"


def _insights_unwatched_cache_key_seed(settings: Settings, days: int, media_type: str) -> str:
    return "|".join(
        [
            "insights-unwatched-v1",
            ",".join(sorted([server.id for server in settings.tautulli_servers])),
            f"days={days}",
            f"media_type={media_type}",
            f"history_len={settings.insights_history_length}",
        ]
    )


def _insights_library_unwatched_cache_key_seed(settings: Settings) -> str:
    return "|".join(
        [
            "insights-library-unwatched-v10",
            ",".join(sorted([server.id for server in settings.tautulli_servers])),
            f"history_len={settings.insights_history_length}",
            f"lib_hist_full={1 if settings.library_unwatched_use_full_history_crawl else 0}",
            f"batch={settings.tv_inventory_batch_shows_per_server}",
            f"inventory_max={settings.tv_inventory_max_shows_per_server}",
            f"sonarr_disk_filter={'1' if sonarr_is_configured(settings) else '0'}",
        ]
    )


def _library_unwatched_inventory_progress_fingerprint(
    inventory_cache: InventoryCache,
    servers: list[TautulliServer],
) -> tuple[tuple[str, str, int, int], ...]:
    parts: list[tuple[str, str, int, int]] = []
    for srv in servers:
        for row in inventory_cache.get_server_progress(srv.id):
            parts.append(
                (
                    srv.id,
                    str(row.get("section_id") or ""),
                    int(row.get("next_start") or 0),
                    1 if row.get("completed") else 0,
                )
            )
    return tuple(sorted(parts))


def _library_unwatched_should_stop_inventory_loop(
    chunk_results: list[InventoryFetchResult],
    servers: list[TautulliServer],
) -> tuple[bool, str]:
    for r in chunk_results:
        if r.server_id == "unknown":
            return True, "internal_error"
    if not servers:
        return True, "no_servers"
    by_id = {r.server_id: r for r in chunk_results}
    for srv in servers:
        chunk = by_id.get(srv.id)
        if chunk is None:
            return True, "missing_server"
        if chunk.status != "ok":
            return True, f"server_status:{chunk.status}"
    if all(by_id[srv.id].index_complete for srv in servers):
        return True, "complete"
    return False, "continue"


async def _library_unwatched_run_inventory_index(
    *,
    settings: Settings,
    inventory_cache: InventoryCache,
    inv_client: TautulliClient,
) -> list[InventoryFetchResult]:
    servers = settings.tautulli_servers
    max_chunks = max(1, int(settings.library_unwatched_max_inventory_chunks_per_job))
    last_results: list[InventoryFetchResult] = []
    prev_fp: tuple[tuple[str, str, int, int], ...] | None = None

    for iteration in range(max_chunks):
        chunk_results = await inv_client.fetch_all_tv_inventory_chunk(
            servers,
            batch_shows_per_server=settings.tv_inventory_batch_shows_per_server,
            get_next_start=lambda sid, sec: inventory_cache.get_next_start(sid, sec),
        )
        last_results = chunk_results

        for chunk in chunk_results:
            if chunk.server_id == "unknown":
                continue
            inventory_cache.upsert_items(chunk.server_id, "show", chunk.shows)
            inventory_cache.upsert_items(chunk.server_id, "season", chunk.seasons)
            inventory_cache.upsert_items(chunk.server_id, "episode", chunk.episodes)
            for progress in chunk.section_progress:
                inventory_cache.set_progress(
                    server_id=chunk.server_id,
                    section_id=str(progress.get("section_id") or ""),
                    next_start=int(progress.get("next_start") or 0),
                    records_total=int(progress.get("records_total") or 0),
                    completed=bool(progress.get("completed")),
                )

        stop, reason = _library_unwatched_should_stop_inventory_loop(chunk_results, servers)
        if stop:
            if reason not in ("complete", "no_servers"):
                logger.warning(
                    "Library unwatched inventory index stopped: %s (iteration=%s)",
                    reason,
                    iteration,
                )
            break

        fp = _library_unwatched_inventory_progress_fingerprint(inventory_cache, servers)
        if fp == prev_fp:
            logger.warning("Library unwatched inventory index stalled (iteration=%s)", iteration)
            break
        prev_fp = fp
    else:
        logger.warning(
            "Library unwatched inventory reached max chunk iterations (%s); index may be partial",
            max_chunks,
        )

    if not last_results and servers:
        last_results = [
            InventoryFetchResult(
                server_id=s.id,
                server_name=s.name,
                status="internal_error",
                error="inventory index produced no results",
            )
            for s in servers
        ]
    return last_results


def _library_unwatched_export_rows(report: dict, group: LibraryUnwatchedExportGroup, server_id: str | None) -> list[dict]:
    cu = report.get("cumulative_unwatched") or {}
    if group == LibraryUnwatchedExportGroup.cumulative_shows:
        raw = cu.get("shows")
        return list(raw) if isinstance(raw, list) else []
    if group == LibraryUnwatchedExportGroup.cumulative_seasons:
        raw = cu.get("seasons")
        return list(raw) if isinstance(raw, list) else []
    if group == LibraryUnwatchedExportGroup.cumulative_episodes:
        raw = cu.get("episodes")
        return list(raw) if isinstance(raw, list) else []
    cat = group.value.removeprefix("server_")
    sid = (server_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="server_id is required for per-server exports.")
    for server in report.get("per_server") or []:
        if str(server.get("server_id")) != sid:
            continue
        uw = server.get("unwatched") or {}
        raw = uw.get(cat)
        return list(raw) if isinstance(raw, list) else []
    return []


def _unwatched_insights_export_rows(report: dict, group: UnwatchedInsightsExportGroup, server_id: str | None) -> list[dict]:
    if group == UnwatchedInsightsExportGroup.cumulative_stale:
        raw = report.get("cumulative_unwatched")
        return list(raw) if isinstance(raw, list) else []
    sid = (server_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="server_id is required for per-server stale export.")
    for server in report.get("per_server_unwatched") or []:
        if str(server.get("server_id")) != sid:
            continue
        raw = server.get("items")
        return list(raw) if isinstance(raw, list) else []
    return []


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
            "Stream Stakeout",
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
    length: int = Query(default=50, ge=1, le=250),
    user: str | None = Query(default=None),
    media_type: str | None = Query(default=None),
    start_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    range_mode: Literal["week", "all"] = Query(
        default="week",
        description='Default "week" uses last HISTORY_DEFAULT_WEEK_DAYS with upstream date filter; "all" paginates slowly.',
    ),
    refresh: bool = Query(default=False),
) -> HTMLResponse:
    """Render merged history timeline with global ordering."""
    settings = get_settings()
    history_cache = _get_history_cache(settings)
    filters = {
        "user": user or "",
        "media_type": media_type or "",
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
    filters["scope_note"] = scope_note
    filters["upstream_after"] = upstream_after or ""
    filters["upstream_before"] = upstream_before or ""
    cache_key_seed = "|".join(
        [
            "history-v2",
            ",".join(sorted([server.id for server in settings.tautulli_servers])),
            f"range_mode={range_mode}",
            f"upstream_after={upstream_after or ''}",
            f"upstream_before={upstream_before or ''}",
            f"start={start}",
            f"length={length}",
            f"user={filters['user']}",
            f"media_type={filters['media_type']}",
            f"start_date={filters['start_date']}",
            f"end_date={filters['end_date']}",
        ]
    )
    cache_key = history_cache.make_key(cache_key_seed)
    if refresh:
        _history_cancel_timeout_retry(cache_key)
        _force_refresh_key(history_cache, cache_key, _history_refresh_tasks)

    async def compute_payload() -> dict:
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
        row_total = sum(len(r.rows) for r in results)
        merged = merge_history(results, start=0, length=max(row_total, 1))
        filtered_rows = _apply_date_range(merged["rows"], start_date=start_date, end_date=end_date)

        page_start = max(start, 0)
        page_end = page_start + length
        paged_rows = filtered_rows[page_start:page_end]
        next_start = page_start + length if len(filtered_rows) > page_end else None
        prev_start = max(page_start - length, 0) if page_start > 0 else None

        return {
            "server_statuses": merged["server_statuses"],
            "rows": paged_rows,
            "configured_servers": len(settings.tautulli_servers),
            "updated_at_epoch": int(datetime.now(timezone.utc).timestamp()),
            "start": page_start,
            "length": length,
            "returned_rows": len(paged_rows),
            "total_rows": len(filtered_rows),
            "next_start": next_start,
            "prev_start": prev_start,
            "filters": dict(filters),
        }

    if history_cache.enabled:
        cache_payload, cache_state = await _get_or_schedule_cached_payload(
            cache=history_cache,
            cache_key_seed=cache_key_seed,
            ttl_seconds=settings.history_cache_ttl_seconds,
            compute_fn=compute_payload,
            task_registry=_history_refresh_tasks,
        )
    else:
        cache_payload = await compute_payload()
        cache_state = "live_compute"

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
        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context=_template_ctx(
                request,
                "Case File Replay",
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
            ),
        )

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
                cache_key=cache_key,
                snapshot_updated_at_epoch=updated_at_epoch,
                base_retry_seconds=settings.history_timeout_retry_seconds,
            )
            retry_interval_seconds = float(
                _history_next_retry_interval_seconds.get(cache_key) or settings.history_timeout_retry_seconds
            )
            _history_schedule_timeout_retry(
                cache_key=cache_key,
                delay_seconds=delay,
                history_cache=history_cache,
                compute_fn=compute_payload,
            )
            retry_countdown_seconds = _history_retry_countdown_seconds(cache_key)
        else:
            _history_cancel_timeout_retry(cache_key)

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context=_template_ctx(
            request,
            "Case File Replay",
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
            filters=cache_payload.get("filters") or filters,
            cache_state=cache_state,
            loading=False,
            refresh=refresh,
            timed_out_servers=timed_out_servers,
            retry_countdown_seconds=retry_countdown_seconds,
            retry_interval_seconds=retry_interval_seconds,
            history_slow_crawl=range_mode == "all",
            history_week_days=settings.history_default_week_days,
            history_poll_ms=8000 if range_mode == "all" else 3000,
        ),
    )


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


@router.get("/insights/unwatched", response_class=HTMLResponse, tags=["dashboard"])
async def unwatched_insights(
    request: Request,
    days: int = Query(default=180, ge=1, le=3650),
    media_type: str = Query(default="episode", pattern="^(episode|movie)$"),
    max_items: int = Query(default=150, ge=1, le=5000),
    refresh: bool = Query(default=False),
) -> HTMLResponse:
    """Render stale media insights from merged multi-server history."""
    settings = get_settings()
    insights_cache = _get_insights_cache(settings)
    cache_key_seed = _insights_unwatched_cache_key_seed(settings, days, media_type)
    cache_key = insights_cache.make_key(cache_key_seed)
    if refresh:
        _force_refresh_key(insights_cache, cache_key, _insights_refresh_tasks)

    async def compute_payload() -> dict:
        client = TautulliClient(
            timeout_seconds=settings.history_request_timeout_seconds,
            max_parallel_servers=settings.upstream_max_parallel_servers,
            per_request_delay_seconds=settings.upstream_per_request_delay_seconds,
        )
        per_server_length = max(settings.insights_history_length, 100)
        results = await client.fetch_all_history(
            settings.tautulli_servers,
            start=0,
            length=per_server_length,
            media_type=media_type,
        )
        merged = merge_history(
            results,
            start=0,
            length=per_server_length * max(len(settings.tautulli_servers), 1),
        )
        cutoff_epoch = int(datetime.now(timezone.utc).timestamp()) - (days * 24 * 60 * 60)
        report = build_unwatched_media_report(
            rows=merged["rows"],
            cutoff_epoch=cutoff_epoch,
            media_type=media_type,
            max_items=50000,
        )
        report = _with_humanized_unwatched_report(report)
        return {
            "configured_servers": len(settings.tautulli_servers),
            "server_statuses": sorted(
                merged["server_statuses"],
                key=lambda item: str(item.get("server_name") or "").lower(),
            ),
            "updated_at_epoch": int(datetime.now(timezone.utc).timestamp()),
            "report": report,
            "cutoff_epoch": cutoff_epoch,
            "history_rows_considered": len(merged["rows"]),
        }

    cache_payload, load_state = await _get_or_schedule_cached_payload(
        cache=insights_cache,
        cache_key_seed=cache_key_seed,
        ttl_seconds=settings.insights_cache_ttl_seconds,
        compute_fn=compute_payload,
        task_registry=_insights_refresh_tasks,
    )

    if cache_payload is None:
        return templates.TemplateResponse(
            request=request,
            name="insights_unwatched.html",
            context=_template_ctx(
                request,
                "Missing Links",
                nav_current="unwatched_insights",
                configured_servers=len(settings.tautulli_servers),
                server_statuses=[],
                updated_at=datetime.now(timezone.utc),
                report={"indexed_item_count": 0, "cumulative_unwatched": [], "per_server_unwatched": []},
                days=days,
                max_items=max_items,
                media_type=media_type,
                cutoff_epoch=0,
                history_rows_considered=0,
                loading=True,
                load_state=load_state,
                refresh=refresh,
            ),
        )

    full_report = cache_payload["report"]
    report = dict(full_report)
    report["cumulative_unwatched"] = full_report["cumulative_unwatched"][: max(max_items, 1)]
    report["per_server_unwatched"] = []
    for server in full_report["per_server_unwatched"]:
        server_copy = dict(server)
        server_copy["items"] = server["items"][: max(max_items, 1)]
        report["per_server_unwatched"].append(server_copy)
    updated_at = datetime.fromtimestamp(int(cache_payload["updated_at_epoch"]), tz=timezone.utc)

    return templates.TemplateResponse(
        request=request,
        name="insights_unwatched.html",
        context=_template_ctx(
            request,
            "Missing Links",
            nav_current="unwatched_insights",
            configured_servers=cache_payload["configured_servers"],
            server_statuses=cache_payload["server_statuses"],
            updated_at=updated_at,
            report=report,
            days=days,
            max_items=max_items,
            media_type=media_type,
            cutoff_epoch=cache_payload["cutoff_epoch"],
            history_rows_considered=cache_payload["history_rows_considered"],
            loading=False,
            load_state=load_state,
            refresh=refresh,
        ),
    )


@router.get("/insights/library-unwatched", response_class=HTMLResponse, tags=["dashboard"])
async def library_unwatched_insights(
    request: Request,
    show_start: int = Query(default=0, ge=0),
    season_start: int = Query(default=0, ge=0),
    episode_start: int = Query(default=0, ge=0),
    server_start: int = Query(default=0, ge=0),
    length: int = Query(default=25, ge=1, le=1000),
    max_items: int = Query(default=20000, ge=100, le=100000),
    refresh: bool = Query(default=False),
) -> HTMLResponse:
    """Render inventory-joined TV report for items not watched in index window."""
    settings = get_settings()
    insights_cache = _get_insights_cache(settings)
    inventory_cache = _get_inventory_cache(settings)
    cache_key_seed = _insights_library_unwatched_cache_key_seed(settings)
    cache_key = insights_cache.make_key(cache_key_seed)
    if refresh:
        _force_refresh_key(insights_cache, cache_key, _insights_refresh_tasks)

    async def compute_payload() -> dict:
        inv_timeout = max(
            float(settings.history_request_timeout_seconds),
            float(settings.tv_inventory_request_timeout_seconds),
        )
        inv_client = TautulliClient(
            timeout_seconds=inv_timeout,
            max_parallel_servers=settings.upstream_max_parallel_servers,
            per_request_delay_seconds=settings.upstream_per_request_delay_seconds
            + settings.library_unwatched_history_extra_delay_seconds,
            inventory_inter_request_delay_seconds=settings.tv_inventory_inter_request_delay_seconds,
            inventory_metadata_max_parallel=settings.tv_inventory_metadata_max_parallel,
        )
        activity_client = TautulliClient(
            timeout_seconds=settings.request_timeout_seconds,
            max_parallel_servers=settings.upstream_max_parallel_servers,
            per_request_delay_seconds=settings.upstream_per_request_delay_seconds,
        )
        per_server_length = max(settings.insights_history_length, 100)

        if settings.library_unwatched_use_full_history_crawl:
            crawl_parallel = min(
                settings.upstream_max_parallel_servers,
                settings.history_full_max_parallel_servers,
            )
            history_client = TautulliClient(
                timeout_seconds=settings.history_request_timeout_seconds,
                max_parallel_servers=crawl_parallel,
                per_request_delay_seconds=settings.upstream_per_request_delay_seconds
                + settings.library_unwatched_history_extra_delay_seconds
                + settings.history_additional_per_request_delay_seconds,
            )
            history_task = asyncio.create_task(
                history_client.fetch_all_history_crawled(
                    settings.tautulli_servers,
                    media_type="episode",
                    page_size=settings.history_full_page_size,
                    inter_page_delay_seconds=settings.history_full_inter_page_delay_seconds,
                    max_rows_per_server=settings.history_full_max_rows_per_server,
                )
            )
        else:
            history_task = asyncio.create_task(
                inv_client.fetch_all_history(
                    settings.tautulli_servers,
                    start=0,
                    length=per_server_length,
                    media_type="episode",
                )
            )
        inventory_task = asyncio.create_task(
            _library_unwatched_run_inventory_index(
                settings=settings,
                inventory_cache=inventory_cache,
                inv_client=inv_client,
            )
        )
        activity_task = asyncio.create_task(activity_client.fetch_all_activity(settings.tautulli_servers))
        history_results, activity_results, chunk_results = await asyncio.gather(
            history_task,
            activity_task,
            inventory_task,
        )
        merged_activity = merge_activity(activity_results)
        activity_server_statuses = list(merged_activity.get("server_statuses") or [])

        if settings.library_unwatched_use_full_history_crawl:
            history_rows = merge_history_rows_all(history_results)
        else:
            merged_history = merge_history(
                history_results,
                start=0,
                length=per_server_length * max(len(settings.tautulli_servers), 1),
            )
            history_rows = merged_history["rows"]
        epochs = [int(row.get("canonical_utc_epoch", 0)) for row in history_rows if int(row.get("canonical_utc_epoch", 0)) > 0]
        index_start_epoch = min(epochs) if epochs else 0
        index_end_epoch = max(epochs) if epochs else 0

        inventory_results: list[InventoryFetchResult] = []
        for chunk in chunk_results:
            if chunk.server_id == "unknown":
                inventory_results.append(chunk)
                continue
            progress_rows = inventory_cache.get_server_progress(chunk.server_id)
            status_chunk = str(chunk.status or "").strip() or "unknown"
            inventory_results.append(
                InventoryFetchResult(
                    server_id=chunk.server_id,
                    server_name=chunk.server_name,
                    status=status_chunk,
                    error=chunk.error,
                    shows=inventory_cache.get_items(chunk.server_id, "show"),
                    seasons=inventory_cache.get_items(chunk.server_id, "season"),
                    episodes=inventory_cache.get_items(chunk.server_id, "episode"),
                    section_progress=progress_rows,
                    index_complete=all(bool(row.get("completed")) for row in progress_rows) if progress_rows else False,
                )
            )

        if sonarr_is_configured(settings):
            try:
                async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as sonarr_http:
                    inventory_results = await filter_library_inventory_results_by_sonarr_disk(
                        sonarr_http,
                        settings.sonarr_base_url,
                        settings.sonarr_api_key,
                        inventory_results,
                        max_parallel_series_fetches=max(4, settings.upstream_max_parallel_servers * 3),
                    )
            except Exception as exc:
                logger.warning("Library unwatched Sonarr disk filter skipped: %s", exc)

        report = build_library_unwatched_tv_report(
            inventory_results=inventory_results,
            history_rows=history_rows,
            index_start_epoch=index_start_epoch,
            index_end_epoch=index_end_epoch,
            max_items=max_items,
            restrict_history_to_index_window=not settings.library_unwatched_use_full_history_crawl,
        )

        if sonarr_is_configured(settings):
            try:
                async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as sonarr_http:
                    await prune_library_unwatched_report_show_seasons_without_sonarr_files(
                        sonarr_http,
                        settings.sonarr_base_url,
                        settings.sonarr_api_key,
                        report,
                        max_parallel_series_fetches=max(4, settings.upstream_max_parallel_servers * 3),
                    )
            except Exception as exc:
                logger.warning("Library unwatched Sonarr show/season file prune skipped: %s", exc)

        index_span_days = 0.0
        if index_start_epoch > 0 and index_end_epoch >= index_start_epoch:
            index_span_days = (index_end_epoch - index_start_epoch) / 86400.0
        return {
            "updated_at_epoch": int(datetime.now(timezone.utc).timestamp()),
            "configured_servers": len(settings.tautulli_servers),
            "history_rows_considered": len(history_rows),
            "index_start_epoch": index_start_epoch,
            "index_end_epoch": index_end_epoch,
            "index_span_days": index_span_days,
            "report": report,
            "activity_server_statuses": activity_server_statuses,
        }

    cache_payload, load_state = await _get_or_schedule_cached_payload(
        cache=insights_cache,
        cache_key_seed=cache_key_seed,
        ttl_seconds=settings.insights_cache_ttl_seconds,
        compute_fn=compute_payload,
        task_registry=_insights_refresh_tasks,
    )

    if cache_payload is None:
        return templates.TemplateResponse(
            request=request,
            name="insights_library_unwatched.html",
            context=_template_ctx(
                request,
                "Unshelved Mysteries",
                nav_current="library_unwatched",
                updated_at=datetime.now(timezone.utc),
                configured_servers=len(settings.tautulli_servers),
                history_rows_considered=0,
                index_start_display="-",
                index_end_display="-",
                index_span_days=0.0,
                show_start=show_start,
                season_start=season_start,
                episode_start=episode_start,
                server_start=server_start,
                length=length,
                show_pagination=_empty_library_unwatched_pager(),
                season_pagination=_empty_library_unwatched_pager(),
                episode_pagination=_empty_library_unwatched_pager(),
                server_prev_start=None,
                server_next_start=None,
                max_items=max_items,
                batch_shows=settings.tv_inventory_batch_shows_per_server,
                throttle_parallel=settings.upstream_max_parallel_servers,
                throttle_delay_seconds=settings.upstream_per_request_delay_seconds,
                sonarr_enabled=sonarr_is_configured(settings),
                plex_per_server_enabled=plex_per_server_actions_available(settings),
                plex_mapped_server_ids=plex_mapped_tautulli_server_ids(settings),
                library_unwatched_server_cards=_library_unwatched_server_card_rows(
                    settings,
                    {"per_server": []},
                    loading=True,
                    activity_server_statuses=[],
                ),
                report={
                    "cumulative_unwatched": {
                        "shows": {"items": [], "total": 0, "has_next": False},
                        "seasons": {"items": [], "total": 0, "has_next": False},
                        "episodes": {"items": [], "total": 0, "has_next": False},
                    },
                    "per_server": [],
                },
                loading=True,
                load_state=load_state,
                refresh=refresh,
                retry_needed=True,
            ),
        )

    report = cache_payload["report"]
    _normalize_library_unwatched_report(report)
    retry_needed = any(
        (server.get("status") != "ok") and (not bool(server.get("index_complete")))
        for server in report.get("per_server", [])
    )
    if retry_needed and not refresh:
        _schedule_cached_refresh(
            cache=insights_cache,
            cache_key=cache_key,
            compute_fn=compute_payload,
            task_registry=_insights_refresh_tasks,
        )
        load_state = "cache_hit_retry_scheduled"

    paginated_report = dict(report)
    paginated_report["cumulative_unwatched"] = dict(report["cumulative_unwatched"])
    paginated_report["cumulative_unwatched"]["shows"] = _paginate_list(
        report["cumulative_unwatched"]["shows"], start=show_start, length=length
    )
    paginated_report["cumulative_unwatched"]["seasons"] = _paginate_list(
        report["cumulative_unwatched"]["seasons"], start=season_start, length=length
    )
    paginated_report["cumulative_unwatched"]["episodes"] = _paginate_list(
        report["cumulative_unwatched"]["episodes"], start=episode_start, length=length
    )
    paginated_servers: list[dict] = []
    for server in report["per_server"]:
        server_copy = dict(server)
        server_copy["unwatched"] = dict(server["unwatched"])
        server_copy["unwatched"]["shows"] = _paginate_list(
            server["unwatched"]["shows"], start=server_start, length=length
        )
        server_copy["unwatched"]["seasons"] = _paginate_list(
            server["unwatched"]["seasons"], start=server_start, length=length
        )
        server_copy["unwatched"]["episodes"] = _paginate_list(
            server["unwatched"]["episodes"], start=server_start, length=length
        )
        paginated_servers.append(server_copy)
    paginated_report["per_server"] = paginated_servers
    cu = paginated_report["cumulative_unwatched"]
    show_pagination = _library_unwatched_column_pager(
        "show", show_start, length, cu["shows"], show_start, season_start, episode_start
    )
    season_pagination = _library_unwatched_column_pager(
        "season", season_start, length, cu["seasons"], show_start, season_start, episode_start
    )
    episode_pagination = _library_unwatched_column_pager(
        "episode", episode_start, length, cu["episodes"], show_start, season_start, episode_start
    )
    server_has_next = any(
        s["unwatched"]["shows"]["has_next"]
        or s["unwatched"]["seasons"]["has_next"]
        or s["unwatched"]["episodes"]["has_next"]
        for s in paginated_servers
    )
    server_prev_start = max(server_start - length, 0) if server_start > 0 else None
    server_next_start = server_start + length if server_has_next else None
    updated_at = datetime.fromtimestamp(int(cache_payload["updated_at_epoch"]), tz=timezone.utc)
    library_unwatched_server_cards = _library_unwatched_server_card_rows(
        settings,
        report,
        loading=False,
        activity_server_statuses=cache_payload.get("activity_server_statuses") or [],
    )

    return templates.TemplateResponse(
        request=request,
        name="insights_library_unwatched.html",
        context=_template_ctx(
            request,
            "Unshelved Mysteries",
            nav_current="library_unwatched",
            updated_at=updated_at,
            configured_servers=cache_payload["configured_servers"],
            history_rows_considered=cache_payload["history_rows_considered"],
            index_start_display=epoch_to_utc_display(cache_payload["index_start_epoch"]),
            index_end_display=epoch_to_utc_display(cache_payload["index_end_epoch"]),
            index_span_days=cache_payload["index_span_days"],
            show_start=show_start,
            season_start=season_start,
            episode_start=episode_start,
            server_start=server_start,
            length=length,
            show_pagination=show_pagination,
            season_pagination=season_pagination,
            episode_pagination=episode_pagination,
            server_prev_start=server_prev_start,
            server_next_start=server_next_start,
            max_items=max_items,
            batch_shows=settings.tv_inventory_batch_shows_per_server,
            throttle_parallel=settings.upstream_max_parallel_servers,
            throttle_delay_seconds=settings.upstream_per_request_delay_seconds,
            sonarr_enabled=sonarr_is_configured(settings),
            plex_per_server_enabled=plex_per_server_actions_available(settings),
            plex_mapped_server_ids=plex_mapped_tautulli_server_ids(settings),
            library_unwatched_server_cards=library_unwatched_server_cards,
            report=paginated_report,
            loading=False,
            load_state=load_state,
            refresh=refresh,
            retry_needed=retry_needed,
        ),
    )


@router.get("/insights/library-unwatched/build-status", tags=["dashboard"])
async def library_unwatched_build_status() -> dict[str, bool]:
    """Lightweight JSON for the indexing wait page: poll until the insights cache has a payload."""
    settings = get_settings()
    insights_cache = _get_insights_cache(settings)
    cache_key_seed = _insights_library_unwatched_cache_key_seed(settings)
    cache_key = insights_cache.make_key(cache_key_seed)
    payload = insights_cache.get(cache_key=cache_key, ttl_seconds=settings.insights_cache_ttl_seconds)
    task = _insights_refresh_tasks.get(cache_key)
    refresh_in_progress = task is not None and not task.done()
    return {
        "ready": payload is not None,
        "refresh_in_progress": refresh_in_progress,
    }


@router.get("/insights/library-unwatched/export", tags=["dashboard"])
async def export_library_unwatched(
    group: LibraryUnwatchedExportGroup,
    export_format: ExportFormat = Query(..., alias="format"),
    server_id: str | None = Query(default=None),
) -> Response:
    """Download one library-unwatched table (full cached dataset, not HTML page slice)."""
    settings = get_settings()
    insights_cache = _get_insights_cache(settings)
    cache_key = insights_cache.make_key(_insights_library_unwatched_cache_key_seed(settings))
    payload = insights_cache.get(cache_key=cache_key, ttl_seconds=settings.insights_cache_ttl_seconds)
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail="Report is not in cache yet. Open Unshelved Mysteries and wait for indexing to finish, then retry.",
        )
    report = payload.get("report") or {}
    rows = _library_unwatched_export_rows(report, group, server_id)
    rows_out = [dict(r) for r in rows]
    meta = {
        "export": "library-unwatched",
        "group": group.value,
        "server_id": server_id or "",
        "row_count": str(len(rows_out)),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    body = build_export_body(rows_out, export_format.value, meta=meta)
    slug = f"library-unwatched-{group.value}"
    if server_id:
        slug = f"{slug}-{server_id}"
    filename = build_export_filename(slug, export_format.value)
    return Response(
        content=body,
        media_type=media_type_for_format(export_format.value),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/insights/unwatched/export", tags=["dashboard"])
async def export_unwatched_insights(
    group: UnwatchedInsightsExportGroup,
    export_format: ExportFormat = Query(..., alias="format"),
    days: int = Query(default=180, ge=1, le=3650),
    media_type: str = Query(default="episode", pattern="^(episode|movie)$"),
    server_id: str | None = Query(default=None),
) -> Response:
    """Download one unwatched-insights table (full cached dataset for the selected filters)."""
    settings = get_settings()
    insights_cache = _get_insights_cache(settings)
    cache_key = insights_cache.make_key(_insights_unwatched_cache_key_seed(settings, days, media_type))
    payload = insights_cache.get(cache_key=cache_key, ttl_seconds=settings.insights_cache_ttl_seconds)
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail="Report is not in cache yet. Open Missing Links and wait for it to load, then retry.",
        )
    report = payload.get("report") or {}
    rows = _unwatched_insights_export_rows(report, group, server_id)
    rows_out = [dict(r) for r in rows]
    meta = {
        "export": "unwatched-insights",
        "group": group.value,
        "media_type": media_type,
        "days": str(days),
        "server_id": server_id or "",
        "row_count": str(len(rows_out)),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    body = build_export_body(rows_out, export_format.value, meta=meta)
    slug = f"unwatched-insights-{group.value}"
    if server_id:
        slug = f"{slug}-{server_id}"
    filename = build_export_filename(slug, export_format.value)
    return Response(
        content=body,
        media_type=media_type_for_format(export_format.value),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


def _get_inventory_cache(settings: Settings) -> InventoryCache:
    global _inventory_cache
    if _inventory_cache is None:
        _inventory_cache = InventoryCache(db_path=settings.inventory_cache_db_path)
    return _inventory_cache


def _get_insights_cache(settings: Settings) -> HistoryPageCache:
    global _insights_cache
    if _insights_cache is None:
        _insights_cache = HistoryPageCache(db_path=settings.insights_cache_db_path)
    return _insights_cache


def _with_humanized_history_rows(rows: list[dict]) -> list[dict]:
    transformed: list[dict] = []
    for row in rows:
        new_row = dict(row)
        new_row["canonical_utc_display"] = _format_epoch_utc(row.get("canonical_utc_epoch"))
        transformed.append(new_row)
    return transformed


def _with_humanized_unwatched_report(report: dict) -> dict:
    transformed = dict(report)

    cumulative = []
    for item in report.get("cumulative_unwatched", []):
        new_item = dict(item)
        new_item["global_last_watched_display"] = _format_epoch_utc(item.get("global_last_watched_epoch"))
        cumulative.append(new_item)
    transformed["cumulative_unwatched"] = cumulative

    per_server = []
    for server in report.get("per_server_unwatched", []):
        new_server = dict(server)
        new_items = []
        for item in server.get("items", []):
            new_item = dict(item)
            new_item["last_watched_display"] = _format_epoch_utc(item.get("last_watched_epoch"))
            new_items.append(new_item)
        new_server["items"] = new_items
        per_server.append(new_server)
    transformed["per_server_unwatched"] = per_server

    return transformed


def _format_epoch_utc(value: object) -> str:
    try:
        epoch = int(value) if value is not None else 0
        if epoch <= 0:
            return "-"
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OSError):
        return "-"


def _normalize_library_unwatched_report(report: dict) -> None:
    """Ensure cached or legacy payloads have fields the library-unwatched template expects."""
    per = report.get("per_server")
    if not isinstance(per, list):
        return
    for entry in per:
        if not isinstance(entry, dict):
            continue
        st = entry.get("status")
        if st is None or (isinstance(st, str) and not str(st).strip()):
            entry["status"] = "unknown"
        counts = entry.get("inventory_counts")
        if not isinstance(counts, dict):
            entry["inventory_counts"] = {"shows": 0, "seasons": 0, "episodes": 0}
        else:
            counts.setdefault("shows", 0)
            counts.setdefault("seasons", 0)
            counts.setdefault("episodes", 0)


def _library_unwatched_server_card_rows(
    settings: Settings,
    report: dict,
    *,
    loading: bool,
    activity_server_statuses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One status card per configured Tautulli server, merged with report rows (never an empty grid)."""
    per_raw = report.get("per_server")
    per_list: list[dict] = (
        [x for x in per_raw if isinstance(x, dict)] if isinstance(per_raw, list) else []
    )
    by_id: dict[str, dict[str, Any]] = {}
    for row in per_list:
        sid = str(row.get("server_id") or "").strip()
        if sid:
            by_id[sid] = row

    act_by_id: dict[str, dict[str, Any]] = {}
    for row in activity_server_statuses or []:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("server_id") or "").strip()
        if aid:
            act_by_id[aid] = row

    def _attach_activity(card: dict[str, Any]) -> None:
        sid = str(card.get("server_id") or "").strip()
        act = act_by_id.get(sid) if sid else None
        if act:
            st = str(act.get("status") or "").strip() or "unknown"
            try:
                sc = int(act.get("stream_count") or 0)
            except (TypeError, ValueError):
                sc = 0
            card["activity_status"] = st
            card["activity_stream_count"] = sc
            card["activity_error"] = act.get("error")
        elif loading:
            card["activity_status"] = "pending"
            card["activity_stream_count"] = 0
            card["activity_error"] = None
        else:
            card["activity_status"] = "unknown"
            card["activity_stream_count"] = 0
            card["activity_error"] = None

    cards: list[dict[str, Any]] = []
    configured: set[str] = set()
    for srv in settings.tautulli_servers:
        sid = str(srv.id)
        configured.add(sid)
        if sid in by_id:
            card = dict(by_id[sid])
            _attach_activity(card)
            cards.append(card)
        elif loading:
            card = {
                "server_id": sid,
                "server_name": srv.name,
                "status": "indexing",
                "index_complete": False,
                "inventory_counts": {"shows": 0, "seasons": 0, "episodes": 0},
                "error": None,
            }
            _attach_activity(card)
            cards.append(card)
        else:
            card = {
                "server_id": sid,
                "server_name": srv.name,
                "status": "unknown",
                "index_complete": False,
                "inventory_counts": {"shows": 0, "seasons": 0, "episodes": 0},
                "error": None,
            }
            _attach_activity(card)
            cards.append(card)
    for sid, row in by_id.items():
        if sid not in configured:
            card = dict(row)
            _attach_activity(card)
            cards.append(card)
    _normalize_library_unwatched_report({"per_server": cards})
    return cards


def _paginate_list(items: list[dict], start: int, length: int) -> dict:
    safe_start = max(start, 0)
    safe_length = max(length, 1)
    total = len(items)
    end = safe_start + safe_length
    return {
        "items": items[safe_start:end],
        "total": total,
        "has_next": total > end,
    }


def _library_unwatched_column_pager(
    axis: Literal["show", "season", "episode"],
    start: int,
    length: int,
    block: dict[str, Any],
    show_coord: int,
    season_coord: int,
    episode_coord: int,
) -> dict[str, Any]:
    """First / prev / next / last triples (show_start, season_start, episode_start) for cumulative columns."""
    total = int(block.get("total") or 0)
    items = block.get("items")
    item_count = len(items) if isinstance(items, list) else 0
    has_next = bool(block.get("has_next"))
    L = max(int(length), 1)
    start = max(int(start), 0)
    if total <= 0:
        return {
            "any": False,
            "page_label": "",
            "range_label": "",
            "triples": {},
        }
    total_pages = max(1, (total + L - 1) // L)
    page_num = min(start // L + 1, total_pages)
    last_start = (total_pages - 1) * L
    range_end = start + item_count
    range_label = f"{start + 1}–{range_end} of {total}" if item_count else f"0 of {total}"
    page_label = f"Page {page_num} / {total_pages}"

    def triple(s_show: int, s_season: int, s_episode: int) -> tuple[int, int, int]:
        return (s_show, s_season, s_episode)

    if axis == "show":
        def coords(sv: int) -> tuple[int, int, int]:
            return triple(sv, season_coord, episode_coord)
    elif axis == "season":
        def coords(sv: int) -> tuple[int, int, int]:
            return triple(show_coord, sv, episode_coord)
    else:

        def coords(sv: int) -> tuple[int, int, int]:
            return triple(show_coord, season_coord, sv)

    triples: dict[str, tuple[int, int, int]] = {}
    if start > 0:
        triples["first"] = coords(0)
        triples["prev"] = coords(max(0, start - L))
    if has_next:
        triples["next"] = coords(start + L)
    if start < last_start:
        triples["last"] = coords(last_start)

    return {
        "any": total_pages > 1,
        "page_label": page_label,
        "range_label": range_label,
        "triples": triples,
    }


def _empty_library_unwatched_pager() -> dict[str, Any]:
    return {
        "any": False,
        "page_label": "",
        "range_label": "",
        "triples": {},
    }


async def _get_or_schedule_cached_payload(
    cache: HistoryPageCache,
    cache_key_seed: str,
    ttl_seconds: float,
    compute_fn: Callable[[], Awaitable[dict]],
    task_registry: dict[str, asyncio.Task],
) -> tuple[dict | None, str]:
    cache_key = cache.make_key(cache_key_seed)
    cache_payload = cache.get(cache_key=cache_key, ttl_seconds=ttl_seconds)
    if cache_payload is not None:
        return cache_payload, "cache_hit"

    task = task_registry.get(cache_key)
    if task is None or task.done():
        task_registry[cache_key] = asyncio.create_task(
            _refresh_cached_payload(cache, cache_key, compute_fn, task_registry)
        )
    return None, "refresh_pending"


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


def _schedule_cached_refresh(
    cache: HistoryPageCache,
    cache_key: str,
    compute_fn: Callable[[], Awaitable[dict]],
    task_registry: dict[str, asyncio.Task],
) -> None:
    """Schedule background refresh if no active refresh task exists."""
    task = task_registry.get(cache_key)
    if task is None or task.done():
        task_registry[cache_key] = asyncio.create_task(
            _refresh_cached_payload(cache, cache_key, compute_fn, task_registry)
        )


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
