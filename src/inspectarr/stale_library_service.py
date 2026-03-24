"""Sonarr-backed stale series/season detection vs merged Tautulli episode history."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx

from inspectarr.aggregate import merge_history_rows_all, tvdb_id_from_guid
from inspectarr.routes_dashboard import cancel_library_unwatched_insights_refresh
from inspectarr.settings import Settings, TautulliServer
from inspectarr.sonarr_client import _all_series_episodes, fetch_series_list_cached
from inspectarr.stale_library_upstream import (
    begin_stale_library_upstream_trace,
    bump_stale_library_tautulli_history_rows,
    end_stale_library_upstream_trace,
    record_stale_library_sonarr,
    record_stale_library_tautulli,
    set_stale_library_sonarr_series_list_count,
    set_stale_library_upstream_phase,
)
from inspectarr.tautulli_client import TautulliClient, TautulliTraceHook

logger = logging.getLogger(__name__)

LOOKBACK_DAYS_DEFAULT = 730

_cache_payload: dict[str, Any] | None = None
_stale_compute_lock: asyncio.Lock | None = None
_stale_compute_task: asyncio.Task[dict[str, Any]] | None = None

_MAX_STALE_COMPUTE_RETRIES = 16


def _stale_library_lock() -> asyncio.Lock:
    global _stale_compute_lock
    if _stale_compute_lock is None:
        _stale_compute_lock = asyncio.Lock()
    return _stale_compute_lock


def _stale_snapshot_fresh(payload: dict[str, Any] | None, ttl_seconds: float) -> bool:
    """True if ``updated_at_epoch`` is within ``ttl_seconds`` of wall clock."""
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
    # Allow small negative age if clock moved backward.
    return -120.0 < age < limit


def _persist_stale_library_cache(settings: Settings, payload: dict[str, Any]) -> None:
    path = str(settings.stale_library_cache_path or "").strip()
    if not path:
        return
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        logger.warning("stale-library: could not persist cache to %s", path, exc_info=True)


def _try_load_stale_library_disk_cache(settings: Settings, ttl_seconds: float) -> dict[str, Any] | None:
    path = str(settings.stale_library_cache_path or "").strip()
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        logger.warning("stale-library: could not read disk cache %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    if not _stale_snapshot_fresh(data, ttl_seconds):
        return None
    return data


def _unlink_stale_library_disk_cache() -> None:
    try:
        from inspectarr.settings import get_settings

        path = str(get_settings().stale_library_cache_path or "").strip()
        if not path:
            return
        p = Path(path)
        if p.is_file():
            p.unlink()
    except OSError:
        logger.debug("stale-library: disk cache unlink failed", exc_info=True)


def _read_stale_library_disk_cache_raw(settings: Settings) -> dict[str, Any] | None:
    """Load snapshot JSON from disk if present (ignores TTL). Used to patch cache after Sonarr edits."""
    path = str(settings.stale_library_cache_path or "").strip()
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("stale-library: could not read disk cache for patch %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _find_stale_series_index(
    series: list[Any],
    *,
    tvdb_id: int | None,
    sonarr_series_id: int | None = None,
    series_title: str | None,
) -> int | None:
    tnorm = str(series_title or "").strip().lower()
    for i, s in enumerate(series):
        if not isinstance(s, dict):
            continue
        if sonarr_series_id is not None and sonarr_series_id > 0:
            raw = s.get("sonarr_series_id")
            try:
                if raw is not None and int(raw) == int(sonarr_series_id):
                    return i
            except (TypeError, ValueError):
                pass
        if tvdb_id is not None and tvdb_id > 0:
            raw = s.get("tvdb_id")
            try:
                if raw is not None and int(raw) == int(tvdb_id):
                    return i
            except (TypeError, ValueError):
                pass
        st = str(s.get("title") or "").strip().lower()
        if tnorm and st == tnorm:
            return i
    return None


async def _mutate_stale_library_cache(settings: Settings, mutator: Callable[[dict[str, Any]], bool]) -> None:
    """Apply an in-place change to the cached snapshot; persist when ``mutator`` returns True.

    Cancels an in-flight full rebuild so it cannot overwrite the patched snapshot.
    """
    lock = _stale_library_lock()
    async with lock:
        global _cache_payload, _stale_compute_task
        if _stale_compute_task is not None and not _stale_compute_task.done():
            _stale_compute_task.cancel()
        payload = _cache_payload
        if payload is None:
            payload = _read_stale_library_disk_cache_raw(settings)
        if not isinstance(payload, dict) or not payload.get("ok"):
            return
        if not isinstance(payload.get("series"), list):
            return
        changed = mutator(payload)
        _cache_payload = payload
        if changed:
            _persist_stale_library_cache(settings, payload)


async def apply_stale_library_cache_after_delete(
    settings: Settings,
    *,
    kind: Literal["show", "season"],
    tvdb_id: int | None,
    sonarr_series_id: int | None = None,
    series_title: str | None,
    season_number: int | None,
) -> None:
    """Remove the deleted show or season row from the snapshot without a full Tautulli/Sonarr rebuild."""

    def mutator(payload: dict[str, Any]) -> bool:
        series = payload.get("series")
        if not isinstance(series, list):
            return False
        idx = _find_stale_series_index(
            series,
            tvdb_id=tvdb_id,
            sonarr_series_id=sonarr_series_id,
            series_title=series_title,
        )
        if idx is None:
            return False
        if kind == "show":
            series.pop(idx)
            return True
        if season_number is None:
            return False
        try:
            sn = int(season_number)
        except (TypeError, ValueError):
            return False
        card = series[idx]
        if not isinstance(card, dict):
            return False
        seasons = card.get("seasons")
        if not isinstance(seasons, list):
            return False
        new_seasons = []
        for x in seasons:
            if not isinstance(x, dict):
                continue
            try:
                sn_x = int(x.get("season_number", -99999))
            except (TypeError, ValueError):
                new_seasons.append(x)
                continue
            if sn_x == sn:
                continue
            new_seasons.append(x)
        if not new_seasons:
            series.pop(idx)
        else:
            card["seasons"] = new_seasons
            card["total_files"] = sum(int(x.get("file_count") or 0) for x in new_seasons if isinstance(x, dict))
        return True

    await _mutate_stale_library_cache(settings, mutator)


async def apply_stale_library_cache_after_monitor_toggle(
    settings: Settings,
    *,
    kind: Literal["show", "season"],
    tvdb_id: int | None,
    sonarr_series_id: int | None = None,
    series_title: str | None,
    season_number: int | None,
    monitored: bool,
) -> None:
    """Update monitored flags on the matching card so the UI matches Sonarr without a full rebuild."""

    def mutator(payload: dict[str, Any]) -> bool:
        series = payload.get("series")
        if not isinstance(series, list):
            return False
        idx = _find_stale_series_index(
            series,
            tvdb_id=tvdb_id,
            sonarr_series_id=sonarr_series_id,
            series_title=series_title,
        )
        if idx is None:
            return False
        card = series[idx]
        if not isinstance(card, dict):
            return False
        if kind == "show":
            card["series_monitored"] = monitored
            return True
        if season_number is None:
            return False
        try:
            sn = int(season_number)
        except (TypeError, ValueError):
            return False
        for se in card.get("seasons") or []:
            if not isinstance(se, dict):
                continue
            try:
                if int(se.get("season_number", -99999)) != sn:
                    continue
            except (TypeError, ValueError):
                continue
            se["monitored"] = monitored
            return True
        return False

    await _mutate_stale_library_cache(settings, mutator)


def _series_lookup_key(tvdb_id: int | None, title: str) -> str:
    t = (title or "").strip().lower()
    if tvdb_id is not None and tvdb_id > 0:
        return f"tvdb:{int(tvdb_id)}"
    return f"t:{t}" if t else "t:__unknown__"


def _normalize_title_for_stale_match(fragment: str) -> str:
    """Fold titles so Sonarr and Plex/Tautulli variants match (punctuation, quotes, year suffix).

    Examples: ``American Dad!`` vs ``American Dad``; ``Black Sails (2014)`` vs ``Black Sails``.
    Keeps ``:`` so ``Initial D: First Stage`` still splits for the colon-base variant elsewhere.
    """
    t = unicodedata.normalize("NFKC", (fragment or "").strip().lower())
    if not t:
        return ""
    t = re.sub(r"\s*\([12][0-9]{3}\)\s*$", "", t).strip()
    for u in ("\u2019", "\u2018", "\u201c", "\u201d", "'", '"'):
        t = t.replace(u, "")
    out: list[str] = []
    for c in t:
        if c.isalnum() or c.isspace() or c == ":":
            out.append(c)
        elif c in "!?.,":
            continue
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def _lookup_key_variants(tvdb_id: int | None, title: str) -> set[str]:
    """Keys used to align Sonarr rows with Tautulli history.

    Plex often exposes anime (and similar) as ``Show: Subtitle`` in ``grandparent_title`` while
    Sonarr uses the short series title. We always record the primary key (TVDB when available)
    plus normalized full title and, when the title contains ``:``, the segment before the first
    colon so ``Initial D`` matches history for ``Initial D: First Stage``.

    We also add a punctuation-folded title key so metadata that differs only by ``!``, commas,
    or smart quotes (e.g. ``American Dad!`` in Sonarr vs ``American Dad`` in Plex) still matches.
    """
    keys: set[str] = set()
    keys.add(_series_lookup_key(tvdb_id, title))
    t = (title or "").strip().lower()
    if not t:
        return keys
    fragments: list[str] = [t]
    if ":" in t:
        base = t.split(":", 1)[0].strip()
        if base and base != t:
            fragments.append(base)
    for frag in fragments:
        keys.add(f"t:{frag}")
        norm = _normalize_title_for_stale_match(frag)
        if norm and norm != frag:
            keys.add(f"t:{norm}")
    return keys


def build_watch_index_from_history(rows: list[dict], cutoff_epoch: int) -> tuple[set[str], set[tuple[str, int]]]:
    """Series and (series_key, season) pairs with at least one play at or after cutoff_epoch."""
    series_watched: set[str] = set()
    season_watched: set[tuple[str, int]] = set()
    for row in rows:
        if str(row.get("media_type") or "").lower() != "episode":
            continue
        try:
            ep = int(row.get("canonical_utc_epoch") or 0)
        except (TypeError, ValueError):
            ep = 0
        if ep < cutoff_epoch:
            continue
        tvdb = tvdb_id_from_guid(row.get("grandparent_guid")) or tvdb_id_from_guid(row.get("guid"))
        title = str(
            row.get("grandparent_title")
            or row.get("show_name")
            or row.get("series_title")
            or ""
        ).strip()
        row_keys = _lookup_key_variants(tvdb, title)
        for sk in row_keys:
            series_watched.add(sk)
        sn = row.get("parent_media_index")
        if sn is None:
            continue
        try:
            sn_int = int(sn)
        except (TypeError, ValueError):
            continue
        for sk in row_keys:
            season_watched.add((sk, sn_int))
    return series_watched, season_watched


def _episode_has_file(ep: dict[str, Any]) -> bool:
    return bool(ep.get("hasFile"))


def _stale_tautulli_trace_hook(server: TautulliServer, cmd: str, http_status: int | None, ok: bool) -> None:
    record_stale_library_tautulli(server.id, server.name, cmd, http_status, ok)


def _stale_history_rows_hook(server: TautulliServer, row_count: int) -> None:
    bump_stale_library_tautulli_history_rows(server.id, server.name, row_count)


def _stale_sonarr_exchange(label: str, status: int, ok: bool) -> None:
    is_ep = "seriesId=" in label and "/api/v3/episode" in label
    record_stale_library_sonarr(label, status, ok, is_episode_list=is_ep)


async def _history_rows_alltime_capped(
    settings: Settings,
    *,
    trace_hook: TautulliTraceHook | None = None,
    history_rows_hook: Callable[[TautulliServer, int], None] | None = None,
) -> list[dict]:
    """Full episode history crawl (no ``after`` filter) capped per server — basis for ever + 2y indices."""
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
        history_rows_hook=history_rows_hook,
    )
    results = await client.fetch_all_history_crawled(
        settings.tautulli_servers,
        media_type="episode",
        after=None,
        page_size=settings.history_full_page_size,
        inter_page_delay_seconds=settings.history_full_inter_page_delay_seconds,
        max_rows_per_server=settings.history_full_max_rows_per_server,
        stop_before_epoch=None,
    )
    return merge_history_rows_all(results)


async def compute_stale_library_payload(
    settings: Settings,
    *,
    lookback_days: int = LOOKBACK_DAYS_DEFAULT,
    max_series_parallel: int = 6,
) -> dict[str, Any]:
    """Build JSON snapshot: Sonarr series with on-disk files and no Tautulli plays in the window (per season)."""
    errors: list[str] = []
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=max(lookback_days, 1))).timestamp())
    # Unshelved Mysteries uses the same Tautulli APIs; stop that background job before this heavy crawl.
    cancel_library_unwatched_insights_refresh(settings)

    tc = len(settings.tautulli_servers)

    if not settings.tautulli_servers:
        return {
            "ok": False,
            "error": "No Tautulli servers configured.",
            "series": [],
            "updated_at_epoch": int(time.time()),
            "lookback_days": lookback_days,
            "history_cutoff_epoch": cutoff,
            "history_rows_used": 0,
            "tautulli_server_count": tc,
            "sonarr_series_scanned": 0,
            "errors": errors,
        }

    if not str(settings.sonarr_base_url or "").strip() or not str(settings.sonarr_api_key or "").strip():
        return {
            "ok": False,
            "error": "Sonarr is not configured (SONARR_BASE_URL / SONARR_API_KEY).",
            "series": [],
            "updated_at_epoch": int(time.time()),
            "lookback_days": lookback_days,
            "history_cutoff_epoch": cutoff,
            "history_rows_used": 0,
            "tautulli_server_count": tc,
            "sonarr_series_scanned": 0,
            "errors": errors,
        }

    begin_stale_library_upstream_trace(
        tautulli_placeholders=[(s.id, s.name) for s in settings.tautulli_servers],
    )
    try:
        set_stale_library_upstream_phase(
            "tautulli",
            "Tautulli: all-time episode history (capped per server) for never + 2y windows",
        )
        try:
            hist_rows = await _history_rows_alltime_capped(
                settings,
                trace_hook=_stale_tautulli_trace_hook,
                history_rows_hook=_stale_history_rows_hook,
            )
        except Exception as exc:
            logger.exception("stale library: Tautulli history failed")
            errors.append(str(exc))
            hist_rows = []

        series_watched_2y, season_watched_2y = build_watch_index_from_history(hist_rows, cutoff)
        series_watched_ever, season_watched_ever = build_watch_index_from_history(hist_rows, 0)

        sem = asyncio.Semaphore(max(1, int(max_series_parallel)))

        async def load_one_series(client: httpx.AsyncClient, ser: dict[str, Any]) -> dict[str, Any] | None:
            async with sem:
                try:
                    sid = int(ser["id"])
                    tvdb = None
                    raw_tvdb = ser.get("tvdbId")
                    if raw_tvdb is not None:
                        try:
                            tvdb = int(raw_tvdb)
                        except (TypeError, ValueError):
                            tvdb = None
                    title = str(ser.get("title") or "")
                    sk = _series_lookup_key(tvdb, title)
                    sonarr_keys = _lookup_key_variants(tvdb, title)
                    eps = await _all_series_episodes(
                        client,
                        settings.sonarr_base_url,
                        settings.sonarr_api_key,
                        sid,
                        on_exchange=_stale_sonarr_exchange,
                    )
                    per_season: dict[int, int] = {}
                    for ep in eps:
                        if not isinstance(ep, dict):
                            continue
                        if not _episode_has_file(ep):
                            continue
                        try:
                            sn = int(ep.get("seasonNumber", 0))
                        except (TypeError, ValueError):
                            continue
                        per_season[sn] = per_season.get(sn, 0) + 1
                    total_files = sum(per_season.values())
                    if total_files <= 0:
                        return None

                    series_monitored = bool(ser.get("monitored"))
                    stale_series = not any(k in series_watched_2y for k in sonarr_keys)

                    seasons_out: list[dict[str, Any]] = []
                    for sn in sorted(per_season.keys()):
                        fc = per_season[sn]
                        if fc <= 0:
                            continue
                        watched_2y = any((k, sn) in season_watched_2y for k in sonarr_keys)
                        watched_ever = any((k, sn) in season_watched_ever for k in sonarr_keys)
                        stale_season = not watched_2y
                        sample = next(
                            (
                                e
                                for e in eps
                                if isinstance(e, dict)
                                and _episode_has_file(e)
                                and int(e.get("seasonNumber") or -999) == sn
                            ),
                            None,
                        )
                        season_monitored = bool(sample.get("monitored")) if isinstance(sample, dict) else True
                        seasons_out.append(
                            {
                                "season_number": sn,
                                "file_count": fc,
                                "monitored": season_monitored,
                                "watched_in_2y": watched_2y,
                                "watched_ever_tautulli": watched_ever,
                                "never_watched_tautulli": not watched_ever,
                                "watched_in_window": watched_2y,
                                "stale": stale_season,
                            }
                        )

                    seasons_visible = [s for s in seasons_out if s["stale"]]
                    if not seasons_visible:
                        return None

                    return {
                        "sonarr_series_id": sid,
                        "tvdb_id": tvdb,
                        "title": title,
                        "series_monitored": series_monitored,
                        "total_files": total_files,
                        "lookup_key": sk,
                        "series_level_stale": stale_series,
                        "series_watched_in_2y": any(k in series_watched_2y for k in sonarr_keys),
                        "series_watched_ever_tautulli": any(k in series_watched_ever for k in sonarr_keys),
                        "series_never_watched_tautulli": not any(k in series_watched_ever for k in sonarr_keys),
                        "seasons": seasons_visible,
                    }
                except Exception as exc:
                    logger.warning("stale library: series %s failed: %s", ser.get("id"), exc)
                    errors.append(f"{ser.get('title')}: {exc}")
                    return None

        set_stale_library_upstream_phase("sonarr", "Sonarr: series list + per-show episode files")
        son_series: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=settings.sonarr_request_timeout_seconds) as client:
            slist = await fetch_series_list_cached(
                client,
                settings.sonarr_base_url,
                settings.sonarr_api_key,
                on_exchange=_stale_sonarr_exchange,
            )
            set_stale_library_sonarr_series_list_count(len(slist))
            tasks = [load_one_series(client, s) for s in slist if isinstance(s, dict)]
            results = await asyncio.gather(*tasks)

        for r in results:
            if r is not None:
                son_series.append(r)

        son_series.sort(key=lambda x: str(x.get("title") or "").lower())

        return {
            "ok": True,
            "series": son_series,
            "updated_at_epoch": int(time.time()),
            "lookback_days": lookback_days,
            "history_cutoff_epoch": cutoff,
            "history_rows_used": len(hist_rows),
            "history_crawl_mode": "alltime_capped",
            "history_full_max_rows_per_server": settings.history_full_max_rows_per_server,
            "tautulli_server_count": tc,
            "sonarr_series_scanned": len(slist),
            "errors": errors,
        }
    finally:
        end_stale_library_upstream_trace()


async def _compute_stale_and_cache(settings: Settings) -> dict[str, Any]:
    """Run full snapshot build and store; shared by concurrent waiters."""
    payload = await compute_stale_library_payload(settings)
    global _cache_payload
    _cache_payload = payload
    _persist_stale_library_cache(settings, payload)
    return dict(payload)


def _schedule_stale_compute(settings: Settings) -> asyncio.Task[dict[str, Any]]:
    """Create the shared snapshot task (caller must hold ``_stale_library_lock``)."""

    def _on_done(t: asyncio.Task[dict[str, Any]]) -> None:
        try:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("stale-library snapshot build failed", exc_info=exc)
        except Exception:
            logger.debug("stale-library task done-callback", exc_info=True)

    task = asyncio.create_task(_compute_stale_and_cache(settings))
    task.add_done_callback(_on_done)
    return task


async def get_stale_library_cached(
    settings: Settings,
    *,
    ttl_seconds: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Return last snapshot if fresh; otherwise recompute.

    Freshness uses wall time from ``updated_at_epoch`` in the payload so a snapshot survives process
    restart when persisted under ``settings.stale_library_cache_path`` (see
    ``settings.stale_library_cache_ttl_seconds``, default six hours).

    Snapshot builds are **single-flight**: concurrent callers share one in-process task. The task is
    ``asyncio.shield``-awaited so a client disconnect (e.g. navigating away) does not cancel the build,
    allowing the cache to finish populating for the next visit.

    ``invalidate_stale_library_cache`` cancels an in-flight build so Sonarr edits are not overwritten
    by a stale completion.
    """
    global _stale_compute_task, _cache_payload
    lock = _stale_library_lock()
    ttl_limit = ttl_seconds if ttl_seconds is not None else float(settings.stale_library_cache_ttl_seconds)

    for attempt in range(_MAX_STALE_COMPUTE_RETRIES):
        if _cache_payload is None:
            loaded = _try_load_stale_library_disk_cache(settings, ttl_limit)
            if loaded is not None:
                _cache_payload = loaded

        if not force and _stale_snapshot_fresh(_cache_payload, ttl_limit):
            return dict(_cache_payload or {})

        draining: asyncio.Task[dict[str, Any]] | None = None
        async with lock:
            if _cache_payload is None:
                loaded = _try_load_stale_library_disk_cache(settings, ttl_limit)
                if loaded is not None:
                    _cache_payload = loaded

            if not force and _stale_snapshot_fresh(_cache_payload, ttl_limit):
                return dict(_cache_payload or {})

            if force and _stale_compute_task is not None and not _stale_compute_task.done():
                draining = _stale_compute_task
                _stale_compute_task.cancel()

            if draining is None:
                if _stale_compute_task is None or _stale_compute_task.done():
                    _stale_compute_task = _schedule_stale_compute(settings)
                task = _stale_compute_task
            else:
                task = None

        if draining is not None:
            try:
                await draining
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("stale-library prior compute failed", exc_info=True)
            continue

        assert task is not None
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            logger.debug(
                "stale-library compute cancelled while waiting (attempt %s/%s)",
                attempt + 1,
                _MAX_STALE_COMPUTE_RETRIES,
            )
            await asyncio.sleep(0)
            continue

    raise RuntimeError("stale-library: snapshot compute could not complete after repeated cancellations")


def invalidate_stale_library_cache() -> None:
    """Drop snapshot memory and disk file; cancel in-flight rebuild. Used by manual Refresh, not Sonarr row actions."""
    global _cache_payload, _stale_compute_task
    _cache_payload = None
    _unlink_stale_library_disk_cache()
    if _stale_compute_task is not None and not _stale_compute_task.done():
        _stale_compute_task.cancel()


async def kick_stale_library_rebuild(settings: Settings) -> None:
    """Start a snapshot rebuild without waiting for it (used by POST /refresh).

    Callers typically ``invalidate_stale_library_cache()`` first. Concurrent
    ``get_stale_library_cached`` requests join the same in-flight task.
    """
    lock = _stale_library_lock()
    async with lock:
        global _stale_compute_task
        if _stale_compute_task is None or _stale_compute_task.done():
            _stale_compute_task = _schedule_stale_compute(settings)
