"""Stale 4K movies (Radarr 4K + Tautulli) UI and JSON API."""

import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from scoparr.limiter import limiter
from scoparr.overseerr_client import overseerr_is_configured
from scoparr.radarr_client import (
    invalidate_radarr_movie_list_cache,
    radarr_delete_movie,
    radarr_get_movie_by_id,
    radarr_put_movie,
)
from scoparr.routes_dashboard import _template_ctx
from scoparr.settings import get_settings, radarr_4k_is_configured
from scoparr.stale_4k_movies_export import ExportFormat, Stale4kMoviesSort, build_stale_4k_movies_export, sort_stale_4k_movies
from scoparr.stale_4k_movies_plex import harbor_watch_4k_plex_delete_on_all_servers
from scoparr.stale_4k_movies_service import (
    apply_stale_4k_movies_cache_after_monitor_toggle,
    apply_stale_4k_movies_cache_after_movie_removed,
    get_stale_4k_movies_cached,
    invalidate_stale_4k_movies_cache,
    kick_stale_4k_movies_rebuild,
)
from scoparr.stale_4k_movies_upstream import record_stale_4k_movies_radarr, stale_4k_movies_upstream_snapshot
from scoparr.stale_library_plex import plex_any_configured_for_cold_storage

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class Stale4kRadarrBody(BaseModel):
    radarr_movie_id: int = Field(..., ge=1)
    chain_plex_delete: bool = Field(
        default=True,
        description="After Radarr removes the movie (delete only), delete matching movie metadata on every configured Plex server.",
    )


def _stale_4k_radarr_exchange(label: str, status: int, ok: bool) -> None:
    record_stale_4k_movies_radarr(label, status, ok)


def _radarr_movie_title_year_tmdb(movie: dict[str, Any]) -> tuple[str, int | None, int | None]:
    title = str(movie.get("title") or "")
    raw_y = movie.get("year")
    try:
        year = int(raw_y) if raw_y is not None else None
    except (TypeError, ValueError):
        year = None
    raw_t = movie.get("tmdbId")
    try:
        tmdb = int(raw_t) if raw_t is not None else None
    except (TypeError, ValueError):
        tmdb = None
    return title, year, tmdb


@router.get("/insights/stale-4k-movies", response_class=HTMLResponse, tags=["dashboard"])
async def stale_4k_movies_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="stale_4k_movies.html",
        context=_template_ctx(
            request,
            "Harbor Watch 4K",
            nav_current="stale_4k_movies",
            radarr_enabled=radarr_4k_is_configured(settings),
            lookback_days=730,
            overseerr_enabled=overseerr_is_configured(settings),
            harbor_watch_4k_section_id=int(settings.harbor_watch_4k_tautulli_section_id or 0),
            plex_chain_enabled=plex_any_configured_for_cold_storage(settings),
        ),
    )


@router.get("/insights/stale-4k-movies/api/upstream", tags=["dashboard"])
@limiter.limit("120/minute")
async def stale_4k_movies_api_upstream(request: Request) -> dict[str, Any]:
    return stale_4k_movies_upstream_snapshot()


@router.get("/insights/stale-4k-movies/api/export", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_4k_movies_api_export(
    request: Request,
    fmt: ExportFormat = Query(
        alias="format",
        description="Download format: json, csv, txt, or xml (full stale list from snapshot).",
    ),
    sort: Stale4kMoviesSort = Query(default="asc"),
    force_refresh: bool = Query(default=False),
) -> Response:
    settings = get_settings()
    payload = await get_stale_4k_movies_cached(settings, force=force_refresh)
    if not payload.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=str(payload.get("error") or "Snapshot is not ready; fix configuration or refresh."),
        )
    body, mime, name = build_stale_4k_movies_export(fmt, payload, sort)
    return Response(
        content=body,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


@router.get("/insights/stale-4k-movies/api/data", tags=["dashboard"])
@limiter.limit("120/minute")
async def stale_4k_movies_api_data(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, ge=1, le=60),
    sort: Stale4kMoviesSort = Query(default="asc"),
    force_refresh: bool = Query(default=False),
) -> dict[str, Any]:
    settings = get_settings()
    payload = await get_stale_4k_movies_cached(settings, force=force_refresh)
    movies: list[dict[str, Any]] = list(payload.get("movies") or [])
    sort_stale_4k_movies(movies, sort)
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


@router.post("/insights/stale-4k-movies/api/refresh", tags=["dashboard"])
@limiter.limit("10/minute")
async def stale_4k_movies_api_refresh(request: Request) -> dict[str, Any]:
    settings = get_settings()
    invalidate_stale_4k_movies_cache()
    await kick_stale_4k_movies_rebuild(settings)
    return {
        "ok": True,
        "rebuilding": True,
        "message": "Harbor Watch 4K snapshot rebuild started; loading data will wait until it finishes.",
    }


@router.post("/insights/stale-4k-movies/radarr/unmonitor", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_4k_movies_radarr_unmonitor(request: Request, body: Stale4kRadarrBody) -> dict[str, Any]:
    settings = get_settings()
    if not radarr_4k_is_configured(settings):
        raise HTTPException(status_code=503, detail="Radarr 4K is not configured.")
    try:
        async with httpx.AsyncClient(timeout=settings.radarr_4k_request_timeout_seconds) as client:
            movie = await radarr_get_movie_by_id(
                client,
                settings.radarr_4k_base_url,
                settings.radarr_4k_api_key,
                body.radarr_movie_id,
                on_exchange=_stale_4k_radarr_exchange,
            )
            if not movie:
                raise HTTPException(status_code=404, detail="Movie not found in Radarr 4K.")
            movie["monitored"] = False
            await radarr_put_movie(
                client,
                settings.radarr_4k_base_url,
                settings.radarr_4k_api_key,
                movie,
                on_exchange=_stale_4k_radarr_exchange,
            )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning("Harbor Watch 4K Radarr HTTP %s", exc.response.status_code)
        raise HTTPException(status_code=502, detail="Radarr 4K returned an error.") from exc
    except httpx.RequestError as exc:
        logger.warning("Harbor Watch 4K Radarr request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Radarr 4K.") from exc

    invalidate_radarr_movie_list_cache(settings.radarr_4k_base_url, settings.radarr_4k_api_key)
    await apply_stale_4k_movies_cache_after_monitor_toggle(
        settings, body.radarr_movie_id, monitored=False
    )
    return {"ok": True, "radarr_movie_id": body.radarr_movie_id, "monitored": False}


@router.post("/insights/stale-4k-movies/radarr/monitor", tags=["dashboard"])
@limiter.limit("60/minute")
async def stale_4k_movies_radarr_monitor(request: Request, body: Stale4kRadarrBody) -> dict[str, Any]:
    settings = get_settings()
    if not radarr_4k_is_configured(settings):
        raise HTTPException(status_code=503, detail="Radarr 4K is not configured.")
    try:
        async with httpx.AsyncClient(timeout=settings.radarr_4k_request_timeout_seconds) as client:
            movie = await radarr_get_movie_by_id(
                client,
                settings.radarr_4k_base_url,
                settings.radarr_4k_api_key,
                body.radarr_movie_id,
                on_exchange=_stale_4k_radarr_exchange,
            )
            if not movie:
                raise HTTPException(status_code=404, detail="Movie not found in Radarr 4K.")
            movie["monitored"] = True
            await radarr_put_movie(
                client,
                settings.radarr_4k_base_url,
                settings.radarr_4k_api_key,
                movie,
                on_exchange=_stale_4k_radarr_exchange,
            )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning("Harbor Watch 4K Radarr HTTP %s", exc.response.status_code)
        raise HTTPException(status_code=502, detail="Radarr 4K returned an error.") from exc
    except httpx.RequestError as exc:
        logger.warning("Harbor Watch 4K Radarr request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Radarr 4K.") from exc

    invalidate_radarr_movie_list_cache(settings.radarr_4k_base_url, settings.radarr_4k_api_key)
    await apply_stale_4k_movies_cache_after_monitor_toggle(
        settings, body.radarr_movie_id, monitored=True
    )
    return {"ok": True, "radarr_movie_id": body.radarr_movie_id, "monitored": True}


@router.post("/insights/stale-4k-movies/radarr/delete", tags=["dashboard"])
@limiter.limit("30/minute")
async def stale_4k_movies_radarr_delete(request: Request, body: Stale4kRadarrBody) -> dict[str, Any]:
    settings = get_settings()
    if not radarr_4k_is_configured(settings):
        raise HTTPException(status_code=503, detail="Radarr 4K is not configured.")
    title: str = ""
    year: int | None = None
    tmdb_id: int | None = None
    try:
        async with httpx.AsyncClient(timeout=settings.radarr_4k_request_timeout_seconds) as client:
            movie = await radarr_get_movie_by_id(
                client,
                settings.radarr_4k_base_url,
                settings.radarr_4k_api_key,
                body.radarr_movie_id,
                on_exchange=_stale_4k_radarr_exchange,
            )
            if not movie:
                raise HTTPException(status_code=404, detail="Movie not found in Radarr 4K.")
            title, year, tmdb_id = _radarr_movie_title_year_tmdb(movie)
            await radarr_delete_movie(
                client,
                settings.radarr_4k_base_url,
                settings.radarr_4k_api_key,
                body.radarr_movie_id,
                delete_files=True,
                on_exchange=_stale_4k_radarr_exchange,
            )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning("Harbor Watch 4K Radarr HTTP %s", exc.response.status_code)
        raise HTTPException(status_code=502, detail="Radarr 4K returned an error.") from exc
    except httpx.RequestError as exc:
        logger.warning("Harbor Watch 4K Radarr request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Radarr 4K.") from exc

    invalidate_radarr_movie_list_cache(settings.radarr_4k_base_url, settings.radarr_4k_api_key)
    plex_results: list[dict[str, Any]] = []
    if body.chain_plex_delete and plex_any_configured_for_cold_storage(settings):
        plex_results = await harbor_watch_4k_plex_delete_on_all_servers(
            settings,
            tmdb_id=tmdb_id,
            title=title,
            year=year,
        )
    await apply_stale_4k_movies_cache_after_movie_removed(settings, body.radarr_movie_id)
    return {
        "ok": True,
        "radarr_movie_id": body.radarr_movie_id,
        "plex_delete_results": plex_results,
    }
