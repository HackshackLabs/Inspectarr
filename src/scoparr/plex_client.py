"""Plex Media Server HTTP client (library management)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)


def _xml_local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


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
        "X-Plex-Product": "Scoparr",
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
        "X-Plex-Product": "Scoparr",
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


def _plex_xml_headers(token: str, client_identifier: str) -> dict[str, str]:
    return {
        "Accept": "application/xml",
        "X-Plex-Token": str(token or "").strip(),
        "X-Plex-Client-Identifier": str(client_identifier or "").strip(),
        "X-Plex-Product": "Scoparr",
        "X-Plex-Version": "0.1.0",
    }


def _tvdb_guid_match(guid: str, tvdb_id: int) -> bool:
    g = str(guid or "").lower()
    needle = str(int(tvdb_id))
    return f"thetvdb://{needle}" in g or f"thetvdb/{needle}" in g or f"tvdb://{needle}" in g


def _pick_show_rating_key(candidates: list[dict[str, str]], *, tvdb_id: int | None, title: str) -> str | None:
    """Pick best show match from hub search metadata (ratingKey, title, guid)."""
    tnorm = " ".join(str(title or "").strip().lower().split())
    if tvdb_id is not None and int(tvdb_id) > 0:
        for c in candidates:
            if _tvdb_guid_match(c.get("guid") or "", int(tvdb_id)):
                rk = str(c.get("ratingKey") or "").strip()
                if rk.isdigit():
                    return rk
    if tnorm:
        for c in candidates:
            ct = " ".join(str(c.get("title") or "").strip().lower().split())
            if ct == tnorm:
                rk = str(c.get("ratingKey") or "").strip()
                if rk.isdigit():
                    return rk
        for c in candidates:
            ct = str(c.get("title") or "").strip().lower()
            if tnorm in ct or ct in tnorm:
                rk = str(c.get("ratingKey") or "").strip()
                if rk.isdigit():
                    return rk
    if candidates:
        rk = str(candidates[0].get("ratingKey") or "").strip()
        if rk.isdigit():
            return rk
    return None


def _parse_hub_search_show_rows(xml_text: str) -> list[dict[str, str]]:
    """Extract show-like rows (ratingKey, title, guid) from hubs/search XML."""
    rows: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return rows
    for el in root.iter():
        tag = _xml_local_tag(el.tag)
        if tag not in ("Video", "Directory", "Metadata"):
            continue
        mtype = (el.get("type") or "").strip().lower()
        if mtype != "show":
            continue
        rk = (el.get("ratingKey") or "").strip()
        if not rk.isdigit():
            continue
        rows.append(
            {
                "ratingKey": rk,
                "title": el.get("title") or "",
                "guid": el.get("guid") or "",
            }
        )
    return rows


async def plex_hub_search_show_candidates(
    *,
    base_url: str,
    token: str,
    client_identifier: str,
    query: str,
    timeout_seconds: float = 30.0,
) -> list[dict[str, str]]:
    """Return show candidates from GET /hubs/search (local library)."""
    base = normalize_plex_base_url(base_url)
    q = str(query or "").strip()
    if not q:
        return []
    tok = str(token or "").strip()
    cid = str(client_identifier or "").strip()
    if not tok or not cid:
        return []
    url = f"{base}/hubs/search?query={quote(q)}&limit=40&local=1"
    headers = _plex_xml_headers(tok, cid)
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
    if r.status_code == 401:
        raise PermissionError("Plex rejected the token (401)")
    if r.status_code >= 400:
        logger.warning("Plex hubs/search HTTP %s url=%s", r.status_code, redact_plex_url(url))
        r.raise_for_status()
    return _parse_hub_search_show_rows(r.text or "")


async def plex_resolve_show_rating_key(
    *,
    base_url: str,
    token: str,
    client_identifier: str,
    series_title: str,
    tvdb_id: int | None,
    timeout_seconds: float = 30.0,
) -> str | None:
    """Find a TV show ratingKey via hub search (TVDB guid preferred)."""
    title = str(series_title or "").strip()
    candidates: list[dict[str, str]] = []
    if title:
        candidates = await plex_hub_search_show_candidates(
            base_url=base_url,
            token=token,
            client_identifier=client_identifier,
            query=title,
            timeout_seconds=timeout_seconds,
        )
    if not candidates and tvdb_id is not None and int(tvdb_id) > 0:
        candidates = await plex_hub_search_show_candidates(
            base_url=base_url,
            token=token,
            client_identifier=client_identifier,
            query=f"tvdb:{int(tvdb_id)}",
            timeout_seconds=timeout_seconds,
        )
    return _pick_show_rating_key(candidates, tvdb_id=tvdb_id, title=title)


async def plex_season_rating_key_for_show(
    *,
    base_url: str,
    token: str,
    client_identifier: str,
    show_rating_key: str,
    season_number: int,
    timeout_seconds: float = 30.0,
) -> str | None:
    """Return season metadata ratingKey under a show, by season index (incl. 0 specials)."""
    base = normalize_plex_base_url(base_url)
    rk = str(show_rating_key or "").strip()
    if not rk.isdigit():
        raise ValueError("show_rating_key must be numeric")
    tok = str(token or "").strip()
    cid = str(client_identifier or "").strip()
    if not tok or not cid:
        raise ValueError("Plex token and client identifier are required")
    url = f"{base}/library/metadata/{rk}/children"
    headers = _plex_xml_headers(tok, cid)
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
    if r.status_code == 401:
        raise PermissionError("Plex rejected the token (401)")
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        r.raise_for_status()
    want = str(int(season_number))
    try:
        root = ET.fromstring(r.text or "")
    except ET.ParseError:
        return None
    for el in root.iter():
        tag = _xml_local_tag(el.tag)
        if tag not in ("Directory", "Metadata"):
            continue
        mtype = (el.get("type") or "").strip().lower()
        if mtype != "season":
            continue
        idx = (el.get("index") or "").strip()
        if idx == want:
            srk = (el.get("ratingKey") or "").strip()
            if srk.isdigit():
                return srk
    return None


async def plex_delete_library_metadata_optional(
    *,
    base_url: str,
    rating_key: str,
    token: str,
    client_identifier: str,
    timeout_seconds: float = 60.0,
) -> str:
    """
    DELETE /library/metadata/{ratingKey}; treat 404 as not_found (idempotent).

    Returns ``\"deleted\"`` or ``\"not_found\"``.
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
        "X-Plex-Product": "Scoparr",
        "X-Plex-Version": "0.1.0",
    }
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        r = await client.delete(url, headers=headers)
    if r.status_code == 404:
        return "not_found"
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
    return "deleted"
