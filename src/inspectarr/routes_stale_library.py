"""Stale library (Sonarr + Tautulli) UI and JSON API."""

import logging
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from inspectarr.limiter import limiter
from inspectarr.routes_dashboard import _template_ctx, cancel_library_unwatched_insights_refresh
from inspectarr.settings import get_settings, sonarr_is_configured
from inspectarr.stale_library_plex import cold_storage_plex_delete_on_all_servers, plex_any_configured_for_cold_storage
from inspectarr.sonarr_client import (
    SonarrKind,
    sonarr_delete,
    sonarr_monitor,
    sonarr_remove_files_and_unmonitor,
    sonarr_unmonitor,
)
from inspectarr.stale_library_service import (
    apply_stale_library_cache_after_delete,
    apply_stale_library_cache_after_monitor_toggle,
    get_stale_library_cached,
    invalidate_stale_library_cache,
    kick_stale_library_rebuild,
)
from inspectarr.stale_library_export import ExportFormat, build_stale_export
from inspectarr.stale_library_upstream import stale_library_upstream_snapshot

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class StaleSonarrBody(BaseModel):
    kind: Literal["show", "season"]
    tvdb_id: int | None = None
    sonarr_series_id: int | None = Field(
        default=None,
        description="Sonarr internal series id from the card; used to match cache rows when TVDB is missing.",
    )
    series_title: str | None = Field(default=None, max_length=500)
    season_number: int | None = None
    chain_plex_delete: bool = Field(
        default=True,
        description="After Sonarr finishes (series removal for show scope; file removal + unmonitor for season), delete matching show or season on every configured Plex server.",
    )


def _validate_kind(kind: str) -> SonarrKind:
    if kind not in ("show", "season"):
        raise HTTPException(status_code=400, detail="kind must be show or season")
    return kind  # type: ignore[return-value]


@router.get("/insights/stale-library", response_class=HTMLResponse, tags=["dashboard"])
async def stale_library_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)
    return templates.TemplateResponse(
        request=request,
        name="stale_library.html",
        context=_template_ctx(
            request,
            "Cold Storage",
            nav_current="stale_library",
            sonarr_enabled=sonarr_is_configured(settings),
            lookback_days=730,
            plex_chain_enabled=plex_any_configured_for_cold_storage(settings),
        ),
    )


@router.get("/insights/stale-library/api/upstream", tags=["dashboard"])
@limiter.limit("120/minute")
async def stale_library_api_upstream(request: Request) -> dict[str, Any]:
    """Live Sonarr vs Tautulli progress while a Cold Storage snapshot is building."""
    return stale_library_upstream_snapshot()


@router.get("/insights/stale-library/api/export", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_library_api_export(
    request: Request,
    fmt: ExportFormat = Query(
        alias="format",
        description="Download format: json, csv, txt, or xml (full stale list from snapshot, not paginated).",
    ),
    sort: Literal["asc", "desc"] = Query(default="asc"),
    force_refresh: bool = Query(default=False),
) -> Response:
    """Download the complete stale-library series list from the cached snapshot."""
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)
    payload = await get_stale_library_cached(settings, force=force_refresh)
    if not payload.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=str(payload.get("error") or "Snapshot is not ready; fix configuration or refresh."),
        )
    body, mime, name = build_stale_export(fmt, payload, sort)
    return Response(
        content=body,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


@router.get("/insights/stale-library/api/data", tags=["dashboard"])
@limiter.limit("120/minute")
async def stale_library_api_data(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, ge=1, le=60),
    sort: Literal["asc", "desc"] = Query(default="asc"),
    force_refresh: bool = Query(default=False),
) -> dict[str, Any]:
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)
    payload = await get_stale_library_cached(settings, force=force_refresh)
    series: list[dict[str, Any]] = list(payload.get("series") or [])
    reverse = sort == "desc"
    series.sort(key=lambda x: str(x.get("title") or "").lower(), reverse=reverse)
    total = len(series)
    start = (page - 1) * per_page
    chunk = series[start : start + per_page]
    total_pages = max((total + per_page - 1) // per_page, 1)
    return {
        **payload,
        "series": chunk,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }


@router.post("/insights/stale-library/api/refresh", tags=["dashboard"])
@limiter.limit("10/minute")
async def stale_library_api_refresh(request: Request) -> dict[str, Any]:
    """Invalidate cache, start rebuild in the background, return immediately.

    The browser should call GET ``/api/data`` next (or poll); that request joins the
    in-flight build instead of blocking this POST for the full crawl duration.
    """
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)
    invalidate_stale_library_cache()
    await kick_stale_library_rebuild(settings)
    return {
        "ok": True,
        "rebuilding": True,
        "message": "Snapshot rebuild started; loading data will wait until it finishes.",
    }


async def _sonarr_action_body(
    settings: Any,
    body: StaleSonarrBody,
    fn: Any,
) -> dict[str, Any]:
    if not sonarr_is_configured(settings):
        raise HTTPException(status_code=503, detail="Sonarr is not configured.")
    if body.tvdb_id is None and not (body.series_title and str(body.series_title).strip()):
        raise HTTPException(status_code=400, detail="tvdb_id or series_title is required.")
    k = _validate_kind(body.kind)
    if k == "season" and body.season_number is None:
        raise HTTPException(status_code=400, detail="season_number required for season scope.")
    try:
        async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as client:
            return await fn(
                client,
                settings.sonarr_base_url,
                settings.sonarr_api_key,
                kind=k,
                tvdb_id=body.tvdb_id,
                series_title=body.series_title,
                season_number=body.season_number,
                episode_number=None,
            )
    except httpx.HTTPStatusError as exc:
        logger.warning("stale-library Sonarr HTTP %s", exc.response.status_code)
        raise HTTPException(status_code=502, detail="Sonarr returned an error.") from exc
    except httpx.RequestError as exc:
        logger.warning("stale-library Sonarr request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Sonarr.") from exc


@router.post("/insights/stale-library/sonarr/unmonitor-delete", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_library_unmonitor_delete(request: Request, body: StaleSonarrBody) -> dict[str, Any]:
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)

    async def _call_show(client: httpx.AsyncClient, base_url: str, api_key: str, **kw: Any) -> dict[str, Any]:
        return await sonarr_delete(client, base_url, api_key, **kw)

    async def _call_season(client: httpx.AsyncClient, base_url: str, api_key: str, **kw: Any) -> dict[str, Any]:
        return await sonarr_remove_files_and_unmonitor(client, base_url, api_key, **kw)

    fn = _call_show if body.kind == "show" else _call_season
    result = await _sonarr_action_body(settings, body, fn)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Sonarr action failed.")
    plex_results: list[dict[str, Any]] = []
    if body.chain_plex_delete and plex_any_configured_for_cold_storage(settings):
        plex_results = await cold_storage_plex_delete_on_all_servers(
            settings,
            kind=body.kind,
            tvdb_id=body.tvdb_id,
            series_title=str(body.series_title or ""),
            season_number=body.season_number,
        )
    await apply_stale_library_cache_after_delete(
        settings,
        kind=body.kind,
        tvdb_id=body.tvdb_id,
        sonarr_series_id=body.sonarr_series_id,
        series_title=body.series_title,
        season_number=body.season_number,
    )
    return {**result, "plex_delete_results": plex_results}


@router.post("/insights/stale-library/sonarr/unmonitor", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_library_unmonitor_only(request: Request, body: StaleSonarrBody) -> dict[str, Any]:
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)

    async def _call(client: httpx.AsyncClient, base_url: str, api_key: str, **kw: Any) -> dict[str, Any]:
        return await sonarr_unmonitor(client, base_url, api_key, **kw)

    result = await _sonarr_action_body(settings, body, _call)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Sonarr action failed.")
    await apply_stale_library_cache_after_monitor_toggle(
        settings,
        kind=body.kind,
        tvdb_id=body.tvdb_id,
        sonarr_series_id=body.sonarr_series_id,
        series_title=body.series_title,
        season_number=body.season_number,
        monitored=False,
    )
    return result


@router.post("/insights/stale-library/sonarr/monitor", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_library_monitor(request: Request, body: StaleSonarrBody) -> dict[str, Any]:
    settings = get_settings()
    cancel_library_unwatched_insights_refresh(settings)

    async def _call(client: httpx.AsyncClient, base_url: str, api_key: str, **kw: Any) -> dict[str, Any]:
        return await sonarr_monitor(client, base_url, api_key, **kw)

    result = await _sonarr_action_body(settings, body, _call)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Sonarr action failed.")
    await apply_stale_library_cache_after_monitor_toggle(
        settings,
        kind=body.kind,
        tvdb_id=body.tvdb_id,
        sonarr_series_id=body.sonarr_series_id,
        series_title=body.series_title,
        season_number=body.season_number,
        monitored=True,
    )
    return result
