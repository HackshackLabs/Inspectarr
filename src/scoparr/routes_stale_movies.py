"""Stale movies (Radarr + Tautulli) UI and JSON API."""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from scoparr.limiter import limiter
from scoparr.overseerr_client import overseerr_is_configured
from scoparr.routes_dashboard import _template_ctx
from scoparr.settings import get_settings, radarr_is_configured
from scoparr.stale_movies_export import ExportFormat, StaleMoviesSort, build_stale_movies_export, sort_stale_movies
from scoparr.stale_movies_service import (
    get_stale_movies_cached,
    invalidate_stale_movies_cache,
    kick_stale_movies_rebuild,
)
from scoparr.stale_movies_upstream import stale_movies_upstream_snapshot

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/insights/stale-movies", response_class=HTMLResponse, tags=["dashboard"])
async def stale_movies_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="stale_movies.html",
        context=_template_ctx(
            request,
            "Harbor Watch",
            nav_current="stale_movies",
            radarr_enabled=radarr_is_configured(settings),
            lookback_days=730,
            overseerr_enabled=overseerr_is_configured(settings),
        ),
    )


@router.get("/insights/stale-movies/api/upstream", tags=["dashboard"])
@limiter.limit("120/minute")
async def stale_movies_api_upstream(request: Request) -> dict[str, Any]:
    return stale_movies_upstream_snapshot()


@router.get("/insights/stale-movies/api/export", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_movies_api_export(
    request: Request,
    fmt: ExportFormat = Query(
        alias="format",
        description="Download format: json, csv, txt, or xml (full stale list from snapshot).",
    ),
    sort: StaleMoviesSort = Query(default="asc"),
    force_refresh: bool = Query(default=False),
) -> Response:
    settings = get_settings()
    payload = await get_stale_movies_cached(settings, force=force_refresh)
    if not payload.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=str(payload.get("error") or "Snapshot is not ready; fix configuration or refresh."),
        )
    body, mime, name = build_stale_movies_export(fmt, payload, sort)
    return Response(
        content=body,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


@router.get("/insights/stale-movies/api/data", tags=["dashboard"])
@limiter.limit("120/minute")
async def stale_movies_api_data(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, ge=1, le=60),
    sort: StaleMoviesSort = Query(default="asc"),
    force_refresh: bool = Query(default=False),
) -> dict[str, Any]:
    settings = get_settings()
    payload = await get_stale_movies_cached(settings, force=force_refresh)
    movies: list[dict[str, Any]] = list(payload.get("movies") or [])
    sort_stale_movies(movies, sort)
    total = len(movies)
    start = (page - 1) * per_page
    chunk = movies[start : start + per_page]
    total_pages = max((total + per_page - 1) // per_page, 1)
    return {
        **payload,
        "movies": chunk,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }


@router.post("/insights/stale-movies/api/refresh", tags=["dashboard"])
@limiter.limit("10/minute")
async def stale_movies_api_refresh(request: Request) -> dict[str, Any]:
    settings = get_settings()
    invalidate_stale_movies_cache()
    await kick_stale_movies_rebuild(settings)
    return {
        "ok": True,
        "rebuilding": True,
        "message": "Stale-movies snapshot rebuild started; loading data will wait until it finishes.",
    }
