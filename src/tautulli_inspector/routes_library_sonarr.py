"""Sonarr proxy routes for Library Unwatched actions."""

from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from tautulli_inspector.settings import get_settings, sonarr_is_configured
from tautulli_inspector.sonarr_client import (
    SonarrKind,
    annotate_library_unwatched_row_state,
    sonarr_delete,
    sonarr_remove_files_and_unmonitor,
    sonarr_status_payload,
    sonarr_unmonitor,
)

router = APIRouter()


class SonarrRowBody(BaseModel):
    kind: Literal["show", "season", "episode"]
    tvdb_id: int | None = None
    series_title: str | None = Field(default=None, max_length=500)
    season_number: int | None = None
    episode_number: int | None = None


def _validate_kind(kind: str) -> SonarrKind:
    if kind not in ("show", "season", "episode"):
        raise HTTPException(status_code=400, detail="kind must be show, season, or episode")
    return kind  # type: ignore[return-value]


@router.get("/insights/library-unwatched/sonarr/status", tags=["dashboard"])
async def library_sonarr_status(
    kind: str = Query(..., description="show | season | episode"),
    tvdb_id: int | None = Query(None),
    series_title: str | None = Query(None, max_length=500),
    season_number: int | None = Query(None),
    episode_number: int | None = Query(None),
) -> dict:
    """Return Sonarr monitored state and on-disk paths for hover UI."""
    settings = get_settings()
    if not sonarr_is_configured(settings):
        return {
            "sonarr_configured": False,
            "message": "Configure SONARR_BASE_URL and SONARR_API_KEY to enable Sonarr actions.",
            "media_state": "ok",
            "media_state_detail": None,
            "actions_disabled": False,
        }
    k = _validate_kind(kind)
    if tvdb_id is None and not (series_title and str(series_title).strip()):
        partial = {
            "ok": False,
            "series_found": False,
            "monitored": None,
            "file_count": 0,
            "paths": [],
            "series_id": None,
            "episode_id": None,
            "episode_file_id": None,
            "message": "Provide tvdb_id or series_title to match a Sonarr series.",
        }
        return {"sonarr_configured": True, **annotate_library_unwatched_row_state(k, partial)}
    try:
        async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as client:
            payload = await sonarr_status_payload(
                client,
                settings.sonarr_base_url,
                settings.sonarr_api_key,
                kind=k,
                tvdb_id=tvdb_id,
                series_title=series_title,
                season_number=season_number,
                episode_number=episode_number,
            )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sonarr HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Sonarr request failed: {exc}") from exc

    return {"sonarr_configured": True, **annotate_library_unwatched_row_state(k, payload)}


@router.post("/insights/library-unwatched/sonarr/unmonitor", tags=["dashboard"])
async def library_sonarr_unmonitor(body: SonarrRowBody) -> dict:
    settings = get_settings()
    if not sonarr_is_configured(settings):
        raise HTTPException(status_code=503, detail="Sonarr is not configured (SONARR_BASE_URL / SONARR_API_KEY).")
    if body.tvdb_id is None and not (body.series_title and str(body.series_title).strip()):
        raise HTTPException(status_code=400, detail="tvdb_id or series_title is required.")
    try:
        async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as client:
            result = await sonarr_unmonitor(
                client,
                settings.sonarr_base_url,
                settings.sonarr_api_key,
                kind=body.kind,
                tvdb_id=body.tvdb_id,
                series_title=body.series_title,
                season_number=body.season_number,
                episode_number=body.episode_number,
            )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sonarr HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Sonarr request failed: {exc}") from exc

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Sonarr action failed.")
    return result


@router.post("/insights/library-unwatched/sonarr/delete", tags=["dashboard"])
async def library_sonarr_delete(body: SonarrRowBody) -> dict:
    """
    Delete series, season files, or a single episode file in Sonarr.

    - **show**: removes the series from Sonarr and deletes managed files (`deleteFiles=true`).
    - **season** / **episode**: deletes episode file(s) on disk only; series stays in Sonarr and
      monitored flags are not changed (contrast with **remove-from-plex-and-unmonitor**).
    """
    settings = get_settings()
    if not sonarr_is_configured(settings):
        raise HTTPException(status_code=503, detail="Sonarr is not configured (SONARR_BASE_URL / SONARR_API_KEY).")
    if body.tvdb_id is None and not (body.series_title and str(body.series_title).strip()):
        raise HTTPException(status_code=400, detail="tvdb_id or series_title is required.")
    try:
        async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as client:
            result = await sonarr_delete(
                client,
                settings.sonarr_base_url,
                settings.sonarr_api_key,
                kind=body.kind,
                tvdb_id=body.tvdb_id,
                series_title=body.series_title,
                season_number=body.season_number,
                episode_number=body.episode_number,
            )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sonarr HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Sonarr request failed: {exc}") from exc

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Sonarr action failed.")
    return result


@router.post("/insights/library-unwatched/sonarr/remove-from-plex-and-unmonitor", tags=["dashboard"])
async def library_sonarr_remove_and_unmonitor(body: SonarrRowBody) -> dict:
    """
    Unmonitor in Sonarr, then delete managed episode file(s) on disk.

    On per-server Library Unwatched rows, the browser may call Plex next (`/plex/delete-library-item`)
    when the row carries server id + ratingKey (see template).
    """
    settings = get_settings()
    if not sonarr_is_configured(settings):
        raise HTTPException(status_code=503, detail="Sonarr is not configured (SONARR_BASE_URL / SONARR_API_KEY).")
    if body.tvdb_id is None and not (body.series_title and str(body.series_title).strip()):
        raise HTTPException(status_code=400, detail="tvdb_id or series_title is required.")
    try:
        async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as client:
            result = await sonarr_remove_files_and_unmonitor(
                client,
                settings.sonarr_base_url,
                settings.sonarr_api_key,
                kind=body.kind,
                tvdb_id=body.tvdb_id,
                series_title=body.series_title,
                season_number=body.season_number,
                episode_number=body.episode_number,
            )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sonarr HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Sonarr request failed: {exc}") from exc

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Sonarr action failed.")
    return result
