"""Tautulli ``get_library_media_info`` helpers (library total plays per item)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from scoparr.aggregate import tmdb_id_from_guid
from scoparr.settings import Settings, TautulliServer
from scoparr.stale_library_service import _normalize_title_for_stale_match

logger = logging.getLogger(__name__)

TautulliLibraryPageHook = Callable[[TautulliServer, int], None]
TautulliLibraryTraceHook = Callable[[TautulliServer, str, int | None, bool], None]

_LIBRARY_PAGE_SIZE = 5000


def _row_play_count(row: dict[str, Any]) -> int:
    for key in ("play_count", "plays", "media_play_count"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return 0


def _row_year(row: dict[str, Any]) -> int | None:
    for key in ("year", "media_year", "release_year"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            y = int(raw)
        except (TypeError, ValueError):
            continue
        if 1870 < y < 2100:
            return y
    return None


def _title_year_key(title: str, year: int | None) -> tuple[str, int] | None:
    if year is None:
        return None
    t = _normalize_title_for_stale_match(str(title or "").strip().lower())
    if not t:
        return None
    return (t, int(year))


def ingest_library_media_rows(
    rows: list[dict[str, Any]],
    *,
    into_by_tmdb: dict[int, int],
    into_by_title_year: dict[tuple[str, int], int],
    section_tmdb_ids: set[int],
    section_title_years: set[tuple[str, int]],
) -> None:
    """Merge play totals and section membership from one page of ``get_library_media_info`` rows."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        plays = _row_play_count(row)
        title = str(row.get("title") or row.get("sort_title") or "").strip()
        year = _row_year(row)
        tid = tmdb_id_from_guid(row.get("guid"))
        if tid is not None and tid > 0:
            section_tmdb_ids.add(int(tid))
            into_by_tmdb[int(tid)] = into_by_tmdb.get(int(tid), 0) + plays
            continue
        ty = _title_year_key(title, year)
        if ty is not None:
            section_title_years.add(ty)
            into_by_title_year[ty] = into_by_title_year.get(ty, 0) + plays


async def _fetch_library_media_page(
    client: httpx.AsyncClient,
    server: TautulliServer,
    *,
    section_id: int,
    start: int,
    length: int,
    trace_hook: TautulliLibraryTraceHook | None,
    page_rows_hook: TautulliLibraryPageHook | None,
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "apikey": server.api_key,
        "cmd": "get_library_media_info",
        "section_id": int(section_id),
        "section_type": "movie",
        "start": max(start, 0),
        "length": max(1, min(int(length), 10_000)),
    }
    response = await client.get(server.api_endpoint, params=params)
    ok = response.is_success
    if trace_hook:
        try:
            trace_hook(server, "get_library_media_info", response.status_code, ok)
        except Exception:
            logger.debug("Tautulli library trace_hook failed", exc_info=True)
    response.raise_for_status()
    payload = response.json()
    response_meta = payload.get("response", {})
    if response_meta.get("result") != "success":
        msg = str(response_meta.get("message") or "Unknown Tautulli API error")
        raise RuntimeError(msg)
    data = response_meta.get("data", {})
    rows = data.get("data") or []
    if not isinstance(rows, list):
        rows = []
    out: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r)
    if page_rows_hook:
        try:
            page_rows_hook(server, len(out))
        except Exception:
            logger.debug("Tautulli library page_rows_hook failed", exc_info=True)
    return out


async def fetch_merged_movie_library_play_index(
    settings: Settings,
    *,
    section_id: int,
    timeout_seconds: float,
    trace_hook: TautulliLibraryTraceHook | None = None,
    page_rows_hook: TautulliLibraryPageHook | None = None,
    inter_page_delay_seconds: float = 0.0,
) -> tuple[
    dict[int, int],
    dict[tuple[str, int], int],
    set[int],
    set[tuple[str, int]],
    int,
    list[str],
]:
    """
    Paginate ``get_library_media_info`` on each Tautulli server and merge.

    Returns (plays_by_tmdb, plays_by_title_year, section_tmdb_ids, section_title_years,
    total_row_count_across_servers, errors).
    """
    by_tmdb: dict[int, int] = {}
    by_ty: dict[tuple[str, int], int] = {}
    section_tmdb: set[int] = set()
    section_ty: set[tuple[str, int]] = set()
    errors: list[str] = []
    total_rows = 0
    delay = max(0.0, float(inter_page_delay_seconds))

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for server in settings.tautulli_servers:
            start = 0
            try:
                while True:
                    batch = await _fetch_library_media_page(
                        client,
                        server,
                        section_id=section_id,
                        start=start,
                        length=_LIBRARY_PAGE_SIZE,
                        trace_hook=trace_hook,
                        page_rows_hook=page_rows_hook,
                    )
                    ingest_library_media_rows(
                        batch,
                        into_by_tmdb=by_tmdb,
                        into_by_title_year=by_ty,
                        section_tmdb_ids=section_tmdb,
                        section_title_years=section_ty,
                    )
                    total_rows += len(batch)
                    if len(batch) < _LIBRARY_PAGE_SIZE:
                        break
                    start += len(batch)
                    if delay > 0:
                        await asyncio.sleep(delay)
            except Exception as exc:
                logger.warning(
                    "Tautulli get_library_media_info failed server=%s: %s",
                    server.id,
                    exc,
                    exc_info=True,
                )
                errors.append(f"{server.name or server.id}: {exc}")

    return by_tmdb, by_ty, section_tmdb, section_ty, total_rows, errors


def library_plays_for_radarr_movie(
    m: dict[str, Any],
    *,
    plays_by_tmdb: dict[int, int],
    plays_by_title_year: dict[tuple[str, int], int],
    section_tmdb_ids: set[int],
    section_title_years: set[tuple[str, int]],
) -> tuple[int, str] | None:
    """
    Return (total_plays, match_kind) if this Radarr movie appears in the merged Tautulli section.

    ``match_kind`` is ``tmdb``, ``title_year``, or ``none`` (plays 0 with weak match — unused).
    """
    title = str(m.get("title") or "")
    raw_year = m.get("year")
    try:
        year = int(raw_year) if raw_year is not None else None
    except (TypeError, ValueError):
        year = None
    raw_tmdb = m.get("tmdbId")
    try:
        tmdb = int(raw_tmdb) if raw_tmdb is not None else None
    except (TypeError, ValueError):
        tmdb = None

    ty_key = _title_year_key(title, year)

    if tmdb is not None and tmdb > 0 and tmdb in section_tmdb_ids:
        return plays_by_tmdb.get(tmdb, 0), "tmdb"
    if ty_key is not None and ty_key in section_title_years:
        return plays_by_title_year.get(ty_key, 0), "title_year"
    return None
