"""Plex Media Server HTTP client (library management)."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def redact_plex_url(url: str) -> str:
    """Strip query string (may contain X-Plex-Token) for logs."""
    s = str(url or "").strip()
    if not s:
        return ""
    cut = s.find("?")
    if cut >= 0:
        return s[:cut] + "?…"
    return s


def normalize_plex_base_url(base_url: str) -> str:
    u = str(base_url or "").strip().rstrip("/")
    if not u:
        return ""
    parsed = urlparse(u)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Plex base URL must include scheme and host (e.g. https://plex.example.com)")
    return u


async def plex_create_pin(
    *,
    client_identifier: str,
    timeout_seconds: float = 30.0,
) -> dict:
    """POST plex.tv pin; returns JSON including id and code."""
    headers = {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": client_identifier,
        "X-Plex-Product": "Tautulli Inspector",
        "X-Plex-Version": "0.1.0",
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        r = await client.post("https://plex.tv/api/v2/pins?strong=true", headers=headers)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected pin response")
    return data


async def plex_check_pin(
    *,
    pin_id: int,
    client_identifier: str,
    timeout_seconds: float = 30.0,
) -> dict:
    headers = {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": client_identifier,
        "X-Plex-Product": "Tautulli Inspector",
        "X-Plex-Version": "0.1.0",
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        r = await client.get(f"https://plex.tv/api/v2/pins/{pin_id}", headers=headers)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected pin check response")
    return data


def plex_auth_app_url(*, client_identifier: str, pin_code: str) -> str:
    from urllib.parse import quote

    cid = quote(str(client_identifier), safe="")
    code = quote(str(pin_code), safe="")
    return f"https://app.plex.tv/auth/#!?clientID={cid}&code={code}"


async def plex_delete_library_metadata(
    *,
    base_url: str,
    rating_key: str,
    token: str,
    client_identifier: str,
    timeout_seconds: float = 60.0,
) -> None:
    """
    DELETE /library/metadata/{ratingKey} on PMS.

    Typically removes the item from the library and deletes associated media files (server/settings dependent).
    """
    base = normalize_plex_base_url(base_url)
    rk = str(rating_key or "").strip()
    if not rk or not re.match(r"^[0-9]+$", rk):
        raise ValueError("rating_key must be a numeric Plex ratingKey")
    tok = str(token or "").strip()
    if not tok:
        raise ValueError("Plex token is required")
    cid = str(client_identifier or "").strip()
    if not cid:
        raise ValueError("plex_client_identifier is required")
    url = f"{base}/library/metadata/{rk}"
    headers = {
        "Accept": "application/json",
        "X-Plex-Token": tok,
        "X-Plex-Client-Identifier": cid,
        "X-Plex-Product": "Tautulli Inspector",
        "X-Plex-Version": "0.1.0",
    }
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        r = await client.delete(url, headers=headers)
    if r.status_code == 404:
        raise FileNotFoundError("Plex metadata item not found (wrong server or ratingKey)")
    if r.status_code == 401:
        raise PermissionError("Plex rejected the token (401)")
    if r.status_code >= 400:
        logger.warning(
            "Plex DELETE failed status=%s url=%s body=%s",
            r.status_code,
            redact_plex_url(url),
            (r.text or "")[:500],
        )
        r.raise_for_status()
