"""Radarr 4K-backed stale movie detection vs merged Tautulli movie history."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from scoparr.aggregate import imdb_tt_from_guid, merge_history_rows_all, tmdb_id_from_guid
from scoparr.iso_time import parse_iso8601_utc_epoch
from scoparr.overseerr_client import (
    fetch_overseerr_movie_request_map,
    overseerr_is_configured,
)
from scoparr.radarr_client import fetch_movie_list_cached
from scoparr.settings import Settings, TautulliServer
from scoparr.tautulli_library_client import (
    fetch_merged_movie_library_play_index,
    library_plays_for_radarr_movie,
)
from scoparr.stale_library_service import (
    LOOKBACK_DAYS_DEFAULT,
    NEVER_PLAYED_MIN_AGE_SECONDS,
    _history_user_display,
    _normalize_title_for_stale_match,
    pick_last_tautulli_play_for_series,
    season_is_stale_cold_storage,
)
from scoparr.stale_4k_movies_upstream import (
    begin_stale_4k_movies_upstream_trace,
    bump_stale_4k_movies_tautulli_history_rows,
    end_stale_4k_movies_upstream_trace,
    record_stale_4k_movies_radarr,
    record_stale_4k_movies_tautulli,
    set_stale_4k_movies_radarr_movie_list_count,
    set_stale_4k_movies_upstream_phase,
)
from scoparr.tautulli_client import TautulliClient, TautulliTraceHook

logger = logging.getLogger(__name__)

_cache_4k_payload: dict[str, Any] | None = None
_stale_4k_movies_compute_lock: asyncio.Lock | None = None
_stale_4k_movies_compute_task: asyncio.Task[dict[str, Any]] | None = None
_MAX_STALE_COMPUTE_RETRIES = 16


def _stale_4k_movies_lock() -> asyncio.Lock:
    global _stale_4k_movies_compute_lock
    if _stale_4k_movies_compute_lock is None:
        _stale_4k_movies_compute_lock = asyncio.Lock()
    return _stale_4k_movies_compute_lock


def _stale_snapshot_fresh(payload: dict[str, Any] | None, ttl_seconds: float) -> bool:
    if not payload:
        return False
    ts = payload.get("updated_at_epoch")
    if ts is None:
        return False
    try:
        age = time.time() - float(ts)
    except (TypeError, ValueError):
        return False
    limit = max(float(ttl_seconds), 5.0)
    return -120.0 < age < limit


def _persist_stale_4k_movies_cache(settings: Settings, payload: dict[str, Any]) -> None:
    path = str(settings.stale_4k_movies_cache_path or "").strip()
    if not path:
        return
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        logger.warning("stale-4k-movies: could not persist cache to %s", path, exc_info=True)


def _try_load_stale_4k_movies_disk_cache(settings: Settings, ttl_seconds: float) -> dict[str, Any] | None:
    path = str(settings.stale_4k_movies_cache_path or "").strip()
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("stale-4k-movies: could not read disk cache %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    if not _stale_snapshot_fresh(data, ttl_seconds):
        return None
    return data


def _unlink_stale_4k_movies_disk_cache() -> None:
    try:
        from scoparr.settings import get_settings

        path = str(get_settings().stale_4k_movies_cache_path or "").strip()
        if not path:
            return
        p = Path(path)
        if p.is_file():
            p.unlink()
    except OSError:
        logger.debug("stale-4k-movies: disk cache unlink failed", exc_info=True)


def _normalize_imdb_tt(raw: Any) -> str | None:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if s.startswith("tt"):
        return s
    if s.isdigit():
        return "tt" + s
    return None


def _movie_lookup_key_variants(tmdb_id: int | None, imdb_tt: str | None, title: str) -> set[str]:
    keys: set[str] = set()
    t = (title or "").strip().lower()
    if tmdb_id is not None and tmdb_id > 0:
        keys.add(f"tmdb:{int(tmdb_id)}")
    if imdb_tt:
        keys.add(f"imdb:{imdb_tt}")
    if t:
        keys.add(f"t:{t}")
        if ":" in t:
            base = t.split(":", 1)[0].strip()
            if base and base != t:
                keys.add(f"t:{base}")
        norm = _normalize_title_for_stale_match(t)
        if norm and norm != t:
            keys.add(f"t:{norm}")
    if not keys:
        keys.add("t:__unknown__")
    return keys


def _radarr_statistics_size_on_disk(statistics: Any) -> int | None:
    if not isinstance(statistics, dict):
        return None
    raw = statistics.get("sizeOnDisk")
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _history_oldest_epoch_from_hist_rows(hist_rows: list[dict[str, Any]]) -> int | None:
    oldest: int | None = None
    for r in hist_rows:
        if not isinstance(r, dict):
            continue
        try:
            e = int(r.get("canonical_utc_epoch") or 0)
        except (TypeError, ValueError):
            continue
        if e <= 0:
            continue
        if oldest is None or e < oldest:
            oldest = e
    return oldest


def build_movie_watch_keys_from_history(rows: list[dict], cutoff_epoch: int) -> set[str]:
    """Lookup keys with at least one movie play at or after ``cutoff_epoch``."""
    out: set[str] = set()
    for row in rows:
        if str(row.get("media_type") or "").lower() != "movie":
            continue
        try:
            ep = int(row.get("canonical_utc_epoch") or 0)
        except (TypeError, ValueError):
            ep = 0
        if ep < cutoff_epoch:
            continue
        tmdb = tmdb_id_from_guid(row.get("guid"))
        imdb = imdb_tt_from_guid(row.get("guid"))
        title = str(
            row.get("title") or row.get("full_title") or row.get("grandparent_title") or ""
        ).strip()
        out.update(_movie_lookup_key_variants(tmdb, imdb, title))
    return out


def build_last_movie_watch_index_from_history(rows: list[dict]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("media_type") or "").lower() != "movie":
            continue
        try:
            ep = int(row.get("canonical_utc_epoch") or 0)
        except (TypeError, ValueError):
            ep = 0
        if ep <= 0:
            continue
        tmdb = tmdb_id_from_guid(row.get("guid"))
        imdb = imdb_tt_from_guid(row.get("guid"))
        title = str(
            row.get("title") or row.get("full_title") or row.get("grandparent_title") or ""
        ).strip()
        row_keys = _movie_lookup_key_variants(tmdb, imdb, title)
        user = _history_user_display(row)
        raw_title = row.get("title")
        movie_title = str(raw_title).strip() if raw_title is not None and str(raw_title).strip() else None
        label = movie_title or "Movie"
        blob: dict[str, Any] = {
            "played_at_epoch": ep,
            "user": user,
            "episode_title": movie_title,
            "season_number": None,
            "episode_number": None,
            "episode_label": label,
            "tautulli_server_id": str(row.get("server_id") or ""),
            "tautulli_server_name": str(row.get("server_name") or ""),
        }
        for sk in row_keys:
            prev = best.get(sk)
            if prev is None or ep > int(prev.get("played_at_epoch") or 0):
                best[sk] = dict(blob)
    return best


def _stale_4k_movies_tautulli_trace_hook(server: TautulliServer, cmd: str, http_status: int | None, ok: bool) -> None:
    record_stale_4k_movies_tautulli(server.id, server.name, cmd, http_status, ok)


def _stale_4k_movies_history_rows_hook(server: TautulliServer, row_count: int) -> None:
    bump_stale_4k_movies_tautulli_history_rows(server.id, server.name, row_count)


def _stale_4k_radarr_exchange(label: str, status: int, ok: bool) -> None:
    record_stale_4k_movies_radarr(label, status, ok)


async def _movie_history_rows_alltime_capped(
    settings: Settings,
    *,
    trace_hook: TautulliTraceHook | None = None,
) -> list[dict]:
    crawl_parallel = min(settings.upstream_max_parallel_servers, settings.history_full_max_parallel_servers)
    timeout_s = max(
        float(settings.history_request_timeout_seconds),
        float(settings.tv_inventory_request_timeout_seconds),
    )
    client = TautulliClient(
        timeout_seconds=timeout_s,
        max_parallel_servers=max(crawl_parallel, 1),
        per_request_delay_seconds=settings.upstream_per_request_delay_seconds
        + settings.library_unwatched_history_extra_delay_seconds
        + settings.history_additional_per_request_delay_seconds,
        trace_hook=trace_hook,
        history_rows_hook=_stale_4k_movies_history_rows_hook,
    )
    results = await client.fetch_all_history_crawled(
        settings.tautulli_servers,
        media_type="movie",
        after=None,
        page_size=settings.history_full_page_size,
        inter_page_delay_seconds=settings.history_full_inter_page_delay_seconds,
        max_rows_per_server=settings.history_full_max_rows_per_server,
        stop_before_epoch=None,
    )
    return merge_history_rows_all(results)


def radarr_movie_added_epoch(m: dict[str, Any]) -> int | None:
    return parse_iso8601_utc_epoch(m.get("added"))


def radarr_movie_file_added_epoch(m: dict[str, Any]) -> int | None:
    mf = m.get("movieFile")
    if isinstance(mf, dict):
        t = parse_iso8601_utc_epoch(mf.get("dateAdded"))
        if t is not None:
            return t
    return None


async def compute_stale_4k_movies_payload(
    settings: Settings,
    *,
    lookback_days: int = LOOKBACK_DAYS_DEFAULT,
) -> dict[str, Any]:
    errors: list[str] = []
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=max(lookback_days, 1))).timestamp())
    now_epoch = int(time.time())
    tc = len(settings.tautulli_servers)

    base_err: dict[str, Any] = {
        "ok": False,
        "error": "",
        "movies": [],
        "updated_at_epoch": int(time.time()),
        "lookback_days": lookback_days,
        "staleness_mode": "tautulli_history_merged",
        "tautulli_library_section_id": int(settings.harbor_watch_4k_tautulli_section_id or 0),
        "tautulli_library_rows_total": 0,
        "history_cutoff_epoch": cutoff,
        "history_rows_used": 0,
        "history_oldest_epoch": None,
        "tautulli_server_count": tc,
        "radarr_movies_scanned": 0,
        "radarr_movies_with_files": 0,
        "overseerr_configured": overseerr_is_configured(settings),
        "overseerr_movie_tmdb_keys": 0,
        "overseerr_fetch_error": None,
        "errors": errors,
    }

    if not settings.tautulli_servers:
        base_err["error"] = "No Tautulli servers configured."
        return base_err

    if not str(settings.radarr_4k_base_url or "").strip() or not str(settings.radarr_4k_api_key or "").strip():
        base_err["error"] = "Radarr 4K is not configured (RADARR_4K_BASE_URL / RADARR_4K_API_KEY)."
        return base_err

    section_id_cfg = int(settings.harbor_watch_4k_tautulli_section_id or 0)
    use_library_plays = section_id_cfg > 0
    lib_timeout = max(
        float(settings.history_request_timeout_seconds),
        float(settings.tv_inventory_request_timeout_seconds),
    )

    begin_stale_4k_movies_upstream_trace(
        tautulli_placeholders=[(s.id, s.name) for s in settings.tautulli_servers],
    )
    try:
        hist_rows: list[dict[str, Any]] = []
        last_watch_index: dict[str, dict[str, Any]] = {}
        movies_watched_2y: set[str] = set()
        movies_watched_ever: set[str] = set()
        plays_by_tmdb: dict[int, int] = {}
        plays_by_title_year: dict[tuple[str, int], int] = {}
        section_tmdb_ids: set[int] = set()
        section_title_years: set[tuple[str, int]] = set()
        tautulli_library_rows_total = 0
        library_fetch_errors: list[str] = []

        if use_library_plays:
            set_stale_4k_movies_upstream_phase(
                "tautulli",
                f"Tautulli: get_library_media_info (section_id={section_id_cfg}, movie) for total plays",
            )

            def _lib_page_rows_hook(server: TautulliServer, row_count: int) -> None:
                bump_stale_4k_movies_tautulli_history_rows(server.id, server.name, row_count)

            try:
                (
                    plays_by_tmdb,
                    plays_by_title_year,
                    section_tmdb_ids,
                    section_title_years,
                    tautulli_library_rows_total,
                    library_fetch_errors,
                ) = await fetch_merged_movie_library_play_index(
                    settings,
                    section_id=section_id_cfg,
                    timeout_seconds=lib_timeout,
                    trace_hook=_stale_4k_movies_tautulli_trace_hook,
                    page_rows_hook=_lib_page_rows_hook,
                    inter_page_delay_seconds=float(settings.upstream_per_request_delay_seconds),
                )
            except Exception as exc:
                logger.exception("stale 4k movies: Tautulli library media info failed")
                errors.append(str(exc))
                return {
                    **base_err,
                    "error": f"Tautulli library media: {exc}",
                    "staleness_mode": "tautulli_library_media_info",
                    "tautulli_library_section_id": section_id_cfg,
                    "tautulli_library_rows_total": 0,
                    "history_crawl_mode": "skipped_library_section",
                    "history_full_max_rows_per_server": settings.history_full_max_rows_per_server,
                }

            errors.extend(library_fetch_errors)
            if library_fetch_errors and len(library_fetch_errors) >= len(settings.tautulli_servers):
                msg = "Tautulli get_library_media_info failed on every configured server."
                return {
                    **base_err,
                    "error": msg,
                    "staleness_mode": "tautulli_library_media_info",
                    "tautulli_library_section_id": section_id_cfg,
                    "tautulli_library_rows_total": tautulli_library_rows_total,
                    "history_crawl_mode": "skipped_library_section",
                    "history_full_max_rows_per_server": settings.history_full_max_rows_per_server,
                    "errors": errors,
                }
        else:
            set_stale_4k_movies_upstream_phase(
                "tautulli",
                "Tautulli: all-time movie history (capped per server) for never + 2y windows",
            )
            try:
                hist_rows = await _movie_history_rows_alltime_capped(
                    settings,
                    trace_hook=_stale_4k_movies_tautulli_trace_hook,
                )
            except Exception as exc:
                logger.exception("stale 4k movies: Tautulli history failed")
                errors.append(str(exc))
                hist_rows = []

            movies_watched_2y = build_movie_watch_keys_from_history(hist_rows, cutoff)
            movies_watched_ever = build_movie_watch_keys_from_history(hist_rows, 0)
            last_watch_index = build_last_movie_watch_index_from_history(hist_rows)

        overseerr_by_tmdb: dict[int, dict[str, Any]] = {}
        overseerr_fetch_error: str | None = None
        if overseerr_is_configured(settings):
            set_stale_4k_movies_upstream_phase(
                "overseerr",
                "Overseerr: paginate requests (movie rows by TMDB id)",
            )
            try:
                async with httpx.AsyncClient(timeout=settings.overseerr_request_timeout_seconds) as ov_client:
                    overseerr_by_tmdb = await fetch_overseerr_movie_request_map(ov_client, settings)
            except Exception as exc:
                logger.warning("stale 4k movies: Overseerr request fetch failed: %s", exc)
                overseerr_fetch_error = str(exc)
                errors.append(f"Overseerr: {exc}")

        set_stale_4k_movies_upstream_phase("radarr", "Radarr 4K: movie library list")
        out_movies: list[dict[str, Any]] = []
        radarr_with_files = 0
        mlist: list[Any] = []
        try:
            async with httpx.AsyncClient(timeout=settings.radarr_4k_request_timeout_seconds) as client:
                mlist = await fetch_movie_list_cached(
                    client,
                    settings.radarr_4k_base_url,
                    settings.radarr_4k_api_key,
                    on_exchange=_stale_4k_radarr_exchange,
                )
            set_stale_4k_movies_radarr_movie_list_count(
                len([x for x in mlist if isinstance(x, dict)]),
            )
        except Exception as exc:
            logger.exception("stale 4k movies: Radarr 4K movie list failed")
            errors.append(str(exc))
            return {
                "ok": False,
                "error": f"Radarr 4K: {exc}",
                "movies": [],
                "updated_at_epoch": int(time.time()),
                "lookback_days": lookback_days,
                "staleness_mode": "tautulli_library_media_info" if use_library_plays else "tautulli_history_merged",
                "tautulli_library_section_id": section_id_cfg,
                "tautulli_library_rows_total": tautulli_library_rows_total if use_library_plays else 0,
                "history_cutoff_epoch": cutoff,
                "history_rows_used": len(hist_rows),
                "history_oldest_epoch": _history_oldest_epoch_from_hist_rows(hist_rows),
                "history_crawl_mode": "skipped_library_section" if use_library_plays else "alltime_capped",
                "history_full_max_rows_per_server": settings.history_full_max_rows_per_server,
                "tautulli_server_count": tc,
                "radarr_movies_scanned": 0,
                "radarr_movies_with_files": 0,
                "overseerr_configured": overseerr_is_configured(settings),
                "overseerr_movie_tmdb_keys": len(overseerr_by_tmdb),
                "overseerr_fetch_error": overseerr_fetch_error,
                "errors": errors,
            }

        for m in mlist:
            if not isinstance(m, dict):
                continue
            if not bool(m.get("hasFile")):
                continue
            mf = m.get("movieFile")
            if not isinstance(mf, dict):
                continue
            try:
                mid = int(m["id"])
            except (TypeError, ValueError, KeyError):
                continue
            radarr_with_files += 1
            tmdb = None
            raw_tmdb = m.get("tmdbId")
            if raw_tmdb is not None:
                try:
                    tmdb = int(raw_tmdb)
                except (TypeError, ValueError):
                    tmdb = None
            imdb_tt = _normalize_imdb_tt(m.get("imdbId"))
            title = str(m.get("title") or "")
            added_epoch = radarr_movie_added_epoch(m)
            file_epoch = radarr_movie_file_added_epoch(m)
            disk = _radarr_statistics_size_on_disk(m.get("statistics"))

            if use_library_plays:
                resolved = library_plays_for_radarr_movie(
                    m,
                    plays_by_tmdb=plays_by_tmdb,
                    plays_by_title_year=plays_by_title_year,
                    section_tmdb_ids=section_tmdb_ids,
                    section_title_years=section_title_years,
                )
                if resolved is None:
                    continue
                total_plays, match_kind = resolved
                if total_plays != 0:
                    continue

                overseerr_info_lib: dict[str, Any] | None = None
                if tmdb is not None:
                    hit = overseerr_by_tmdb.get(tmdb)
                    if hit is not None:
                        overseerr_info_lib = {**hit, "matched_via": "tmdb"}

                out_movies.append(
                    {
                        "radarr_movie_id": mid,
                        "tmdb_id": tmdb,
                        "imdb_id": m.get("imdbId"),
                        "title": title,
                        "movie_monitored": bool(m.get("monitored")),
                        "size_on_disk_bytes": disk,
                        "movie_level_stale": True,
                        "movie_watched_in_2y": False,
                        "movie_watched_ever_tautulli": False,
                        "movie_never_watched_tautulli": True,
                        "overseerr": overseerr_info_lib,
                        "last_tautulli_play": None,
                        "first_file_added_epoch": file_epoch,
                        "last_file_added_epoch": file_epoch,
                        "radarr_added_epoch": added_epoch,
                        "tautulli_library_play_count": int(total_plays),
                        "tautulli_library_match": match_kind,
                        "staleness_reason": "library_zero_plays",
                    }
                )
                continue

            keys = _movie_lookup_key_variants(tmdb, imdb_tt, title)
            age_epoch = added_epoch
            if age_epoch is None:
                age_epoch = file_epoch

            watched_2y = any(k in movies_watched_2y for k in keys)
            watched_ever = any(k in movies_watched_ever for k in keys)
            stale_movie = season_is_stale_cold_storage(
                watched_in_lookback=watched_2y,
                watched_ever=watched_ever,
                series_added_epoch=age_epoch,
                now_epoch=now_epoch,
                never_played_min_age_seconds=NEVER_PLAYED_MIN_AGE_SECONDS,
            )
            if not stale_movie:
                continue

            overseerr_info: dict[str, Any] | None = None
            if tmdb is not None:
                hit = overseerr_by_tmdb.get(tmdb)
                if hit is not None:
                    overseerr_info = {**hit, "matched_via": "tmdb"}

            last_play = pick_last_tautulli_play_for_series(last_watch_index, keys)
            movie_level_stale = not watched_2y

            out_movies.append(
                {
                    "radarr_movie_id": mid,
                    "tmdb_id": tmdb,
                    "imdb_id": m.get("imdbId"),
                    "title": title,
                    "movie_monitored": bool(m.get("monitored")),
                    "size_on_disk_bytes": disk,
                    "movie_level_stale": movie_level_stale,
                    "movie_watched_in_2y": watched_2y,
                    "movie_watched_ever_tautulli": watched_ever,
                    "movie_never_watched_tautulli": not watched_ever,
                    "overseerr": overseerr_info,
                    "last_tautulli_play": last_play,
                    "first_file_added_epoch": file_epoch,
                    "last_file_added_epoch": file_epoch,
                    "radarr_added_epoch": added_epoch,
                }
            )

        out_movies.sort(key=lambda x: str(x.get("title") or "").lower())

        return {
            "ok": True,
            "movies": out_movies,
            "updated_at_epoch": int(time.time()),
            "lookback_days": lookback_days,
            "never_played_min_age_days": 180,
            "staleness_mode": "tautulli_library_media_info" if use_library_plays else "tautulli_history_merged",
            "tautulli_library_section_id": section_id_cfg,
            "tautulli_library_rows_total": tautulli_library_rows_total if use_library_plays else 0,
            "history_cutoff_epoch": cutoff,
            "history_rows_used": len(hist_rows),
            "history_oldest_epoch": _history_oldest_epoch_from_hist_rows(hist_rows),
            "history_crawl_mode": "skipped_library_section" if use_library_plays else "alltime_capped",
            "history_full_max_rows_per_server": settings.history_full_max_rows_per_server,
            "tautulli_server_count": tc,
            "radarr_movies_scanned": len([x for x in mlist if isinstance(x, dict)]),
            "radarr_movies_with_files": radarr_with_files,
            "overseerr_configured": overseerr_is_configured(settings),
            "overseerr_movie_tmdb_keys": len(overseerr_by_tmdb),
            "overseerr_fetch_error": overseerr_fetch_error,
            "errors": errors,
        }
    finally:
        end_stale_4k_movies_upstream_trace()


async def _compute_stale_4k_movies_and_cache(settings: Settings) -> dict[str, Any]:
    payload = await compute_stale_4k_movies_payload(settings)
    global _cache_4k_payload
    _cache_4k_payload = payload
    _persist_stale_4k_movies_cache(settings, payload)
    return dict(payload)


def _schedule_stale_4k_movies_compute(settings: Settings) -> asyncio.Task[dict[str, Any]]:
    def _on_done(t: asyncio.Task[dict[str, Any]]) -> None:
        try:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("stale-4k-movies snapshot build failed", exc_info=exc)
        except Exception:
            logger.debug("stale-movies task done-callback", exc_info=True)

    task = asyncio.create_task(_compute_stale_4k_movies_and_cache(settings))
    task.add_done_callback(_on_done)
    return task


async def get_stale_4k_movies_cached(
    settings: Settings,
    *,
    ttl_seconds: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    global _stale_4k_movies_compute_task, _cache_4k_payload
    lock = _stale_4k_movies_lock()
    ttl_limit = ttl_seconds if ttl_seconds is not None else float(settings.stale_4k_movies_cache_ttl_seconds)

    for attempt in range(_MAX_STALE_COMPUTE_RETRIES):
        if _cache_4k_payload is None:
            loaded = _try_load_stale_4k_movies_disk_cache(settings, ttl_limit)
            if loaded is not None:
                _cache_4k_payload = loaded

        if not force and _stale_snapshot_fresh(_cache_4k_payload, ttl_limit):
            return dict(_cache_4k_payload or {})

        draining: asyncio.Task[dict[str, Any]] | None = None
        async with lock:
            if _cache_4k_payload is None:
                loaded = _try_load_stale_4k_movies_disk_cache(settings, ttl_limit)
                if loaded is not None:
                    _cache_4k_payload = loaded

            if not force and _stale_snapshot_fresh(_cache_4k_payload, ttl_limit):
                return dict(_cache_4k_payload or {})

            if force and _stale_4k_movies_compute_task is not None and not _stale_4k_movies_compute_task.done():
                draining = _stale_4k_movies_compute_task
                _stale_4k_movies_compute_task.cancel()

            if draining is None:
                if _stale_4k_movies_compute_task is None or _stale_4k_movies_compute_task.done():
                    _stale_4k_movies_compute_task = _schedule_stale_4k_movies_compute(settings)
                task = _stale_4k_movies_compute_task
            else:
                task = None

        if draining is not None:
            try:
                await draining
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("stale-movies prior compute failed", exc_info=True)
            continue

        assert task is not None
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            logger.debug(
                "stale-movies compute cancelled while waiting (attempt %s/%s)",
                attempt + 1,
                _MAX_STALE_COMPUTE_RETRIES,
            )
            await asyncio.sleep(0)
            continue

    raise RuntimeError("stale-4k-movies: snapshot compute could not complete after repeated cancellations")


async def apply_stale_4k_movies_cache_after_movie_removed(settings: Settings, radarr_movie_id: int) -> None:
    lock = _stale_4k_movies_lock()
    async with lock:
        global _cache_4k_payload
        p = _cache_4k_payload
        if not p or not isinstance(p.get("movies"), list):
            return
        rid = int(radarr_movie_id)
        movies = p["movies"]
        new_movies = [m for m in movies if int(m.get("radarr_movie_id") or 0) != rid]
        if len(new_movies) == len(movies):
            return
        _cache_4k_payload = {**p, "movies": new_movies}
        _persist_stale_4k_movies_cache(settings, _cache_4k_payload)


async def apply_stale_4k_movies_cache_after_monitor_toggle(
    settings: Settings,
    radarr_movie_id: int,
    *,
    monitored: bool,
) -> None:
    lock = _stale_4k_movies_lock()
    async with lock:
        global _cache_4k_payload
        p = _cache_4k_payload
        if not p or not isinstance(p.get("movies"), list):
            return
        rid = int(radarr_movie_id)
        changed = False
        new_movies: list[dict[str, Any]] = []
        for m in p["movies"]:
            if not isinstance(m, dict):
                new_movies.append(m)
                continue
            if int(m.get("radarr_movie_id") or 0) == rid:
                row = dict(m)
                row["movie_monitored"] = monitored
                new_movies.append(row)
                changed = True
            else:
                new_movies.append(m)
        if changed:
            _cache_4k_payload = {**p, "movies": new_movies}
            _persist_stale_4k_movies_cache(settings, _cache_4k_payload)


def invalidate_stale_4k_movies_cache() -> None:
    global _cache_4k_payload, _stale_4k_movies_compute_task
    _cache_4k_payload = None
    _unlink_stale_4k_movies_disk_cache()
    if _stale_4k_movies_compute_task is not None and not _stale_4k_movies_compute_task.done():
        _stale_4k_movies_compute_task.cancel()


async def kick_stale_4k_movies_rebuild(settings: Settings) -> None:
    lock = _stale_4k_movies_lock()
    async with lock:
        global _stale_4k_movies_compute_task
        if _stale_4k_movies_compute_task is None or _stale_4k_movies_compute_task.done():
            _stale_4k_movies_compute_task = _schedule_stale_4k_movies_compute(settings)
