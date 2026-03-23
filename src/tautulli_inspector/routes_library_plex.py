"""Plex Media Server delete actions for Library Unwatched (per-server rows)."""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tautulli_inspector.limiter import limiter
from tautulli_inspector.plex_client import plex_delete_library_metadata
from tautulli_inspector.settings import get_settings, resolve_plex_for_tautulli

logger = logging.getLogger(__name__)

router = APIRouter()


class PlexLibraryDeleteBody(BaseModel):
    kind: Literal["show", "season", "episode"]
    tautulli_server_id: str = Field(..., min_length=1, max_length=128)
    rating_key: str = Field(..., min_length=1, max_length=32)


@router.post("/insights/library-unwatched/plex/delete-library-item", tags=["dashboard"])
@limiter.limit("60/minute")
async def plex_delete_library_item(request: Request, body: PlexLibraryDeleteBody) -> dict:
    """
    DELETE metadata on the Plex server mapped to this Tautulli server id.

    rating_key must be the Plex key for the row's scope (show / season / episode).
    """
    settings = get_settings()
    resolved = resolve_plex_for_tautulli(settings, body.tautulli_server_id)
    if resolved is None:
        raise HTTPException(
            status_code=503,
            detail="Plex is not configured for this server (mapping, token, or plex_client_identifier missing). "
            "Use /settings to map Plex URLs and sign in.",
        )
    plex_server, token, client_id = resolved
    try:
        await plex_delete_library_metadata(
            base_url=plex_server.base_url,
            rating_key=body.rating_key,
            token=token,
            client_identifier=client_id,
            timeout_seconds=settings.plex_request_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Plex delete HTTP %s: %s",
            exc.response.status_code,
            (exc.response.text or "")[:800],
        )
        raise HTTPException(
            status_code=502,
            detail="Plex Media Server returned an error for this delete request.",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Plex delete request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Plex Media Server.") from exc

    return {
        "ok": True,
        "message": f"Deleted Plex library item {body.rating_key} ({body.kind}) on {plex_server.id}.",
    }
