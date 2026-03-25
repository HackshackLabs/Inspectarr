"""After Sonarr file removal, delete matching show/season on every configured Plex server."""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from scoparr.plex_client import (
    plex_delete_library_metadata_optional,
    plex_resolve_show_rating_key,
    plex_season_rating_key_for_show,
)
from scoparr.settings import Settings, plex_token_for_profile

logger = logging.getLogger(__name__)


def plex_any_configured_for_cold_storage(settings: Settings) -> bool:
    """True if client id is set and at least one Plex server has a token."""
    if not str(settings.plex_client_identifier or "").strip():
        return False
    for ps in settings.plex_servers:
        if plex_token_for_profile(settings, ps.token_profile):
            return True
    return False


async def cold_storage_plex_delete_on_all_servers(
    settings: Settings,
    *,
    kind: Literal["show", "season"],
    tvdb_id: int | None,
    series_title: str,
    season_number: int | None,
) -> list[dict[str, Any]]:
    """
    Resolve show (and optionally season) via hub search + children, then DELETE metadata on each PMS.

    Skips servers without token or client id; records ok/detail per Plex config row.
    """
    cid = str(settings.plex_client_identifier or "").strip()
    title = str(series_title or "").strip()
    out: list[dict[str, Any]] = []
    timeout = float(settings.plex_request_timeout_seconds)
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
            show_rk = await plex_resolve_show_rating_key(
                base_url=ps.base_url,
                token=token,
                client_identifier=cid,
                series_title=title,
                tvdb_id=tvdb_id,
                timeout_seconds=timeout,
            )
            if not show_rk:
                out.append(
                    {
                        "plex_id": ps.id,
                        "tautulli_server_id": ps.tautulli_server_id,
                        "ok": False,
                        "detail": "show not found (hub search)",
                    }
                )
                continue
            target_rk = show_rk
            if kind == "season":
                if season_number is None:
                    out.append(
                        {
                            "plex_id": ps.id,
                            "tautulli_server_id": ps.tautulli_server_id,
                            "ok": False,
                            "detail": "season_number required",
                        }
                    )
                    continue
                season_rk = await plex_season_rating_key_for_show(
                    base_url=ps.base_url,
                    token=token,
                    client_identifier=cid,
                    show_rating_key=show_rk,
                    season_number=int(season_number),
                    timeout_seconds=timeout,
                )
                if not season_rk:
                    out.append(
                        {
                            "plex_id": ps.id,
                            "tautulli_server_id": ps.tautulli_server_id,
                            "ok": False,
                            "detail": f"season {season_number} not found under show",
                        }
                    )
                    continue
                target_rk = season_rk
            state = await plex_delete_library_metadata_optional(
                base_url=ps.base_url,
                rating_key=target_rk,
                token=token,
                client_identifier=cid,
                timeout_seconds=timeout,
            )
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": True,
                    "rating_key": target_rk,
                    "delete_state": state,
                }
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("Cold Storage Plex chain HTTP %s", exc.response.status_code)
            out.append(
                {
                    "plex_id": ps.id,
                    "tautulli_server_id": ps.tautulli_server_id,
                    "ok": False,
                    "detail": f"Plex HTTP {exc.response.status_code}",
                }
            )
        except httpx.RequestError as exc:
            logger.warning("Cold Storage Plex chain request failed: %s", exc)
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
