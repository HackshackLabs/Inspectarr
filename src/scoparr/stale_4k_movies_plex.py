"""After Radarr 4K removes a movie, delete matching library metadata on every configured Plex server."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from scoparr.plex_client import plex_delete_library_metadata_optional, plex_resolve_movie_rating_key
from scoparr.settings import Settings, plex_token_for_profile

logger = logging.getLogger(__name__)


async def harbor_watch_4k_plex_delete_on_all_servers(
    settings: Settings,
    *,
    tmdb_id: int | None,
    title: str,
    year: int | None,
) -> list[dict[str, Any]]:
    cid = str(settings.plex_client_identifier or "").strip()
    timeout = float(settings.plex_request_timeout_seconds)
    out: list[dict[str, Any]] = []
    for ps in settings.plex_servers:
        token = plex_token_for_profile(settings, ps.token_profile)
        if not cid or not token:
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": False,
                    "detail": "missing Plex token or plex_client_identifier",
                }
            )
            continue
        try:
            rk = await plex_resolve_movie_rating_key(
                base_url=ps.base_url,
                token=token,
                client_identifier=cid,
                title=str(title or ""),
                year=year,
                tmdb_id=tmdb_id,
                timeout_seconds=timeout,
            )
            if not rk:
                out.append(
                    {
                        "plex_id": ps.id,
                        "tautulli_server_id": ps.tautulli_server_id,
                        "ok": False,
                        "detail": "movie not found (hub search)",
                    }
                )
                continue
            state = await plex_delete_library_metadata_optional(
                base_url=ps.base_url,
                rating_key=rk,
                token=token,
                client_identifier=cid,
                timeout_seconds=timeout,
            )
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": True,
                    "rating_key": rk,
                    "delete_state": state,
                }
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("Harbor Watch 4K Plex chain HTTP %s", exc.response.status_code)
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": False,
                    "detail": f"Plex HTTP {exc.response.status_code}",
                }
            )
        except httpx.RequestError as exc:
            logger.warning("Harbor Watch 4K Plex chain request failed: %s", exc)
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": False,
                    "detail": "could not reach Plex",
                }
            )
        except (ValueError, PermissionError) as exc:
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": False,
                    "detail": str(exc),
                }
            )
    return out
