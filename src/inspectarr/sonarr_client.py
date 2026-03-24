"""Sonarr v3 API helpers for library-unwatched actions."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from threading import Lock
from typing import Any, Callable, Literal

import httpx

from inspectarr.models import InventoryFetchResult

logger = logging.getLogger(__name__)

SonarrExchangeHook = Callable[[str, int, bool], None] | None
SonarrKind = Literal["show", "season", "episode"]

_series_list_cache_lock = Lock()
_series_list_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SERIES_LIST_CACHE_TTL_SECONDS = 90.0


def invalidate_series_list_cache(base_url: str, api_key: str) -> None:
    """Drop cached `/api/v3/series` payload after mutations."""
    key = _series_cache_key(base_url, api_key)
    with _series_list_cache_lock:
        _series_list_cache.pop(key, None)


def _series_cache_key(base_url: str, api_key: str) -> str:
    h = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:20]
    return f"{_base(base_url)}|{h}"


async def fetch_series_list_cached(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    on_exchange: SonarrExchangeHook = None,
) -> list[dict[str, Any]]:
    """Cached series list to avoid one full download per table row."""
    key = _series_cache_key(base_url, api_key)
    now = time.monotonic()
    with _series_list_cache_lock:
        ent = _series_list_cache.get(key)
        if ent and (now - ent[0]) < _SERIES_LIST_CACHE_TTL_SECONDS:
            return ent[1]
    data = await fetch_series_list(client, base_url, api_key, on_exchange=on_exchange)
    with _series_list_cache_lock:
        _series_list_cache[key] = (now, data)
    return data


def _base(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


async def fetch_series_list(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    on_exchange: SonarrExchangeHook = None,
) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/api/v3/series"
    response = await client.get(url, headers={"X-Api-Key": api_key})
    if on_exchange:
        try:
            on_exchange("GET /api/v3/series", response.status_code, response.is_success)
        except Exception:
            logger.debug("Sonarr on_exchange failed", exc_info=True)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def _norm_series_title_for_match(title: str) -> str:
    return " ".join(str(title or "").strip().lower().split())


def _series_title_match_candidates(series_title: str | None) -> list[str]:
    """Normalized Plex/library titles to try against Sonarr (full title + common stems)."""
    raw = str(series_title or "").strip()
    if not raw:
        return []
    norm = _norm_series_title_for_match(raw)
    out: list[str] = [norm]
    if "//" in raw:
        stem = raw.split("//", 1)[0].strip()
        if stem:
            sn = _norm_series_title_for_match(stem)
            if sn and sn not in out:
                out.append(sn)
    return out


def _sonarr_series_title_variants(s: dict[str, Any]) -> set[str]:
    """Normalized titles Sonarr may use (primary, sort, TVDB alternates)."""
    variants: set[str] = set()
    for key in ("title", "sortTitle"):
        t = s.get(key)
        if t is not None and str(t).strip():
            variants.add(_norm_series_title_for_match(str(t)))
    alts = s.get("alternateTitles")
    if isinstance(alts, list):
        for item in alts:
            if not isinstance(item, dict):
                continue
            t = item.get("title")
            if t is not None and str(t).strip():
                variants.add(_norm_series_title_for_match(str(t)))
    variants.discard("")
    return variants


def resolve_series(
    series_list: list[dict[str, Any]],
    tvdb_id: int | None,
    series_title: str | None,
) -> dict[str, Any] | None:
    if tvdb_id is not None:
        for s in series_list:
            if s.get("tvdbId") == tvdb_id:
                return s
    candidates = _series_title_match_candidates(series_title)
    if not candidates:
        return None
    for s in series_list:
        variants = _sonarr_series_title_variants(s)
        if variants & set(candidates):
            return s
        clean = str(s.get("cleanTitle") or "").strip().lower()
        if not clean:
            continue
        for cand in candidates:
            if clean == cand.replace(" ", ""):
                return s
    return None


async def fetch_season_episodes(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
    season_number: int,
) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/api/v3/episode"
    response = await client.get(
        url,
        headers={"X-Api-Key": api_key},
        params={"seriesId": series_id, "seasonNumber": season_number},
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def _episode_season_number(ep: dict[str, Any]) -> int | None:
    try:
        sn = ep.get("seasonNumber")
        if sn is None:
            return None
        return int(sn)
    except (TypeError, ValueError):
        return None


async def fetch_episodes_for_season(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
    season_number: int,
) -> list[dict[str, Any]]:
    """
    Episodes belonging to a season.

    Some Sonarr builds return an empty list for ``GET /episode?seasonNumber=`` even when
    episodes exist; fall back to all-episodes for the series filtered by ``seasonNumber``.
    """
    want = int(season_number)
    direct = await fetch_season_episodes(client, base_url, api_key, series_id, want)
    if direct:
        return direct
    all_eps = await _all_series_episodes(client, base_url, api_key, series_id)
    return [ep for ep in all_eps if _episode_season_number(ep) == want]


def _episode_ids_for_api(episodes: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for ep in episodes:
        raw = ep.get("id")
        if raw is None:
            continue
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


_MONITOR_BATCH_SIZE = 200


async def set_episodes_monitored_batched(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    episode_ids: list[int],
    monitored: bool,
) -> None:
    """Sonarr accepts bulk monitor updates; very large seasons are split to avoid proxy/API limits."""
    if not episode_ids:
        return
    for i in range(0, len(episode_ids), _MONITOR_BATCH_SIZE):
        chunk = episode_ids[i : i + _MONITOR_BATCH_SIZE]
        await set_episodes_monitored(client, base_url, api_key, chunk, monitored)


def _episode_file_path(ep: dict[str, Any]) -> str | None:
    """Best path string for display; Sonarr often omits absolute `path` but sets `relativePath`."""
    ef = ep.get("episodeFile")
    if not isinstance(ef, dict):
        return None
    for key in ("path", "relativePath"):
        raw = ef.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _episode_file_id(ep: dict[str, Any]) -> int | None:
    """
    Episode file id for Sonarr DELETE /api/v3/episodefile/{id}.

    List responses often omit the nested ``episodeFile`` object but still set ``episodeFileId``
    or ``hasFile``. Numeric ``episodeFile`` values appear in some payloads.
    """
    ef = ep.get("episodeFile")
    if isinstance(ef, dict):
        try:
            i = int(ef.get("id"))
            return i if i > 0 else None
        except (TypeError, ValueError):
            return None
    if isinstance(ef, (int, float)):
        try:
            i = int(ef)
            return i if i > 0 else None
        except (TypeError, ValueError):
            return None
    raw = ep.get("episodeFileId")
    if raw is not None:
        try:
            i = int(raw)
            return i if i > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def _episode_has_file_on_disk(ep: dict[str, Any]) -> bool:
    """True if Sonarr considers this episode to have a file (UI disk count / list rows)."""
    if ep.get("hasFile") is True:
        return True
    if _episode_file_id(ep) is not None:
        return True
    return _episode_file_path(ep) is not None


async def set_episodes_monitored(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    episode_ids: list[int],
    monitored: bool,
) -> None:
    if not episode_ids:
        return
    url = f"{_base(base_url)}/api/v3/episode/monitor"
    response = await client.put(
        url,
        headers={"X-Api-Key": api_key},
        json={"episodeIds": episode_ids, "monitored": monitored},
    )
    response.raise_for_status()


async def put_series(client: httpx.AsyncClient, base_url: str, api_key: str, series: dict[str, Any]) -> dict[str, Any]:
    url = f"{_base(base_url)}/api/v3/series"
    response = await client.put(url, headers={"X-Api-Key": api_key}, json=series)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


async def fetch_series_by_id(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
) -> dict[str, Any]:
    """Full series resource (includes ``seasons`` with per-season ``monitored`` like the Sonarr UI)."""
    url = f"{_base(base_url)}/api/v3/series/{int(series_id)}"
    response = await client.get(url, headers={"X-Api-Key": api_key})
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


async def put_season_monitored(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
    season_number: int,
    *,
    monitored: bool,
) -> bool:
    """
    Update ``seasons[].monitored`` on the series. Sonarr's season header uses this; it is not
    reliably driven by ``PUT /episode/monitor`` alone.
    """
    series_obj = await fetch_series_by_id(client, base_url, api_key, series_id)
    seasons = series_obj.get("seasons")
    if not isinstance(seasons, list):
        return False
    want = int(season_number)
    for season in seasons:
        if not isinstance(season, dict):
            continue
        try:
            sn = int(season.get("seasonNumber"))
        except (TypeError, ValueError):
            continue
        if sn == want:
            season["monitored"] = monitored
            await put_series(client, base_url, api_key, series_obj)
            return True
    return False


async def delete_episode_file(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    episode_file_id: int,
) -> None:
    url = f"{_base(base_url)}/api/v3/episodefile/{episode_file_id}"
    response = await client.delete(url, headers={"X-Api-Key": api_key})
    response.raise_for_status()


async def delete_sonarr_series(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
    *,
    delete_files: bool = True,
    add_import_list_exclusion: bool = False,
) -> None:
    """Remove a series from Sonarr (`DELETE /api/v3/series/{id}`)."""
    url = f"{_base(base_url)}/api/v3/series/{series_id}"
    response = await client.delete(
        url,
        headers={"X-Api-Key": api_key},
        params={
            "deleteFiles": "true" if delete_files else "false",
            "addImportListExclusion": "true" if add_import_list_exclusion else "false",
        },
    )
    response.raise_for_status()


async def sonarr_status_payload(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    kind: SonarrKind,
    tvdb_id: int | None,
    series_title: str | None,
    season_number: int | None,
    episode_number: int | None,
) -> dict[str, Any]:
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    series = resolve_series(series_list, tvdb_id, series_title)
    if not series:
        return {
            "ok": True,
            "series_found": False,
            "monitored": None,
            "file_count": 0,
            "paths": [],
            "series_id": None,
            "episode_id": None,
            "episode_file_id": None,
            "message": "Series not found in Sonarr (match TVDB id or add the series).",
        }

    sid = int(series["id"])
    series_monitored = bool(series.get("monitored"))
    root = str(series.get("rootFolderPath") or "").rstrip("/")
    rel = str(series.get("path") or "").strip("/")
    series_folder = "/".join(p for p in (root, rel) if p) if (root or rel) else None

    if kind == "show":
        all_eps = await _all_series_episodes(client, base_url, api_key, sid)
        file_count = sum(1 for ep in all_eps if _episode_has_file_on_disk(ep))
        return {
            "ok": True,
            "series_found": True,
            "monitored": series_monitored,
            "file_count": file_count,
            "paths": [p for p in [series_folder] if p],
            "series_id": sid,
            "episode_id": None,
            "episode_file_id": None,
            "message": None,
        }

    if season_number is None:
        return {
            "ok": True,
            "series_found": True,
            "monitored": None,
            "file_count": 0,
            "paths": [],
            "series_id": sid,
            "episode_id": None,
            "episode_file_id": None,
            "message": "Season number missing for Sonarr lookup.",
        }

    episodes = await fetch_episodes_for_season(client, base_url, api_key, sid, int(season_number))
    if kind == "season":
        paths = sorted({_episode_file_path(ep) for ep in episodes if _episode_file_path(ep)})
        file_count = sum(1 for ep in episodes if _episode_has_file_on_disk(ep))
        mon = [bool(ep.get("monitored")) for ep in episodes]
        monitored_val: bool | None
        if not mon:
            monitored_val = None
        elif all(mon):
            monitored_val = True
        elif not any(mon):
            monitored_val = False
        else:
            monitored_val = None
        return {
            "ok": True,
            "series_found": True,
            "monitored": monitored_val,
            "file_count": file_count,
            "paths": paths,
            "series_id": sid,
            "episode_id": None,
            "episode_file_id": None,
            "message": None if episodes else "No episodes for this season in Sonarr.",
        }

    if episode_number is None:
        return {
            "ok": True,
            "series_found": True,
            "monitored": None,
            "file_count": 0,
            "paths": [],
            "series_id": sid,
            "episode_id": None,
            "episode_file_id": None,
            "message": "Episode number missing for Sonarr lookup.",
        }

    target: dict[str, Any] | None = None
    for ep in episodes:
        try:
            if int(ep.get("episodeNumber")) == int(episode_number):
                target = ep
                break
        except (TypeError, ValueError):
            continue

    if not target:
        return {
            "ok": True,
            "series_found": True,
            "monitored": None,
            "file_count": 0,
            "paths": [],
            "series_id": sid,
            "episode_id": None,
            "episode_file_id": None,
            "message": f"Episode S{season_number}E{episode_number} not found in Sonarr.",
        }

    path = _episode_file_path(target)
    efile_id = _episode_file_id(target)
    has_file = _episode_has_file_on_disk(target)
    return {
        "ok": True,
        "series_found": True,
        "monitored": bool(target.get("monitored")),
        "file_count": 1 if has_file else 0,
        "paths": [path] if path else [],
        "series_id": sid,
        "episode_id": int(target["id"]) if target.get("id") is not None else None,
        "episode_file_id": efile_id,
        "message": None,
    }


def annotate_library_unwatched_row_state(kind: SonarrKind, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Add Library Unwatched UI fields derived from a Sonarr status payload:

    - ``media_state``: ``ok`` | ``missing`` (nothing to act on in Sonarr) | ``no_file`` (scoped
      to Sonarr but no episode files on disk).
    - ``media_state_detail``: short reason for tooltips / row title.
    - ``actions_disabled``: when True, destructive Sonarr buttons should be disabled.
    """
    out = dict(payload)
    if out.get("sonarr_configured") is False:
        out.setdefault("media_state", "ok")
        out.setdefault("media_state_detail", None)
        out.setdefault("actions_disabled", False)
        return out

    sf = out.get("series_found")
    msg = (out.get("message") or "").strip()
    fc_raw = out.get("file_count")
    try:
        fc = int(fc_raw) if fc_raw is not None else 0
    except (TypeError, ValueError):
        fc = 0

    media_state: Literal["ok", "missing", "no_file"] = "ok"
    detail: str | None = None
    actions_disabled = False

    if sf is False:
        media_state = "missing"
        detail = msg or "Not in Sonarr."
        actions_disabled = True
    elif kind == "show":
        if fc == 0:
            media_state = "no_file"
            detail = "No episode files on disk for this series (per Sonarr)."
    elif kind == "season":
        if "season number missing" in msg.lower():
            media_state, detail, actions_disabled = "missing", msg, True
        elif "no episodes" in msg.lower() and "season" in msg.lower():
            media_state, detail, actions_disabled = "missing", msg, True
        elif fc == 0:
            media_state = "no_file"
            detail = "No episode files on disk for this season (per Sonarr)."
    elif kind == "episode":
        if "episode number missing" in msg.lower():
            media_state, detail, actions_disabled = "missing", msg, True
        elif "not found" in msg.lower():
            media_state, detail, actions_disabled = "missing", msg, True
        elif fc == 0:
            media_state = "no_file"
            detail = "No episode file on disk (per Sonarr)."

    out["media_state"] = media_state
    out["media_state_detail"] = detail
    out["actions_disabled"] = actions_disabled
    return out


async def sonarr_unmonitor(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    kind: SonarrKind,
    tvdb_id: int | None,
    series_title: str | None,
    season_number: int | None,
    episode_number: int | None,
) -> dict[str, Any]:
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    series = resolve_series(series_list, tvdb_id, series_title)
    if not series:
        return {"ok": False, "message": "Series not found in Sonarr."}

    sid = int(series["id"])

    if kind == "show":
        series["monitored"] = False
        await put_series(client, base_url, api_key, series)
        invalidate_series_list_cache(base_url, api_key)
        return {"ok": True, "message": "Series unmonitored in Sonarr."}

    if season_number is None:
        return {"ok": False, "message": "Season number required."}

    episodes = await fetch_episodes_for_season(client, base_url, api_key, sid, int(season_number))
    if kind == "season":
        ids = _episode_ids_for_api(episodes)
        if ids:
            await set_episodes_monitored_batched(client, base_url, api_key, ids, False)
        season_toggled = await put_season_monitored(
            client, base_url, api_key, sid, int(season_number), monitored=False
        )
        if not season_toggled and not ids:
            return {
                "ok": False,
                "message": f"No Sonarr season row or episodes for season {season_number} (refresh the series in Sonarr or check Plex vs Sonarr season numbers).",
            }
        invalidate_series_list_cache(base_url, api_key)
        if season_toggled and ids:
            msg = f"Season {season_number} unmonitored (Sonarr season toggle + {len(ids)} episode(s))."
        elif season_toggled:
            msg = f"Season {season_number} marked unmonitored in Sonarr."
        else:
            msg = f"Unmonitored {len(ids)} episode(s) in season {season_number} (if the season header still looks monitored, refresh the series in Sonarr)."
        return {"ok": True, "message": msg}

    if episode_number is None:
        return {"ok": False, "message": "Episode number required."}

    target_id: int | None = None
    for ep in episodes:
        try:
            if int(ep.get("episodeNumber")) == int(episode_number):
                target_id = int(ep["id"])
                break
        except (TypeError, ValueError):
            continue
    if target_id is None:
        return {"ok": False, "message": "Episode not found in Sonarr."}

    await set_episodes_monitored(client, base_url, api_key, [target_id], False)
    invalidate_series_list_cache(base_url, api_key)
    return {"ok": True, "message": "Episode unmonitored in Sonarr."}


async def sonarr_monitor(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    kind: SonarrKind,
    tvdb_id: int | None,
    series_title: str | None,
    season_number: int | None,
    episode_number: int | None,
) -> dict[str, Any]:
    """Mirror of :func:`sonarr_unmonitor` with monitored flags set to True."""
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    series = resolve_series(series_list, tvdb_id, series_title)
    if not series:
        return {"ok": False, "message": "Series not found in Sonarr."}

    sid = int(series["id"])

    if kind == "show":
        series["monitored"] = True
        await put_series(client, base_url, api_key, series)
        invalidate_series_list_cache(base_url, api_key)
        return {"ok": True, "message": "Series monitored in Sonarr."}

    if season_number is None:
        return {"ok": False, "message": "Season number required."}

    episodes = await fetch_episodes_for_season(client, base_url, api_key, sid, int(season_number))
    if kind == "season":
        ids = _episode_ids_for_api(episodes)
        if ids:
            await set_episodes_monitored_batched(client, base_url, api_key, ids, True)
        season_toggled = await put_season_monitored(
            client, base_url, api_key, sid, int(season_number), monitored=True
        )
        if not season_toggled and not ids:
            return {
                "ok": False,
                "message": f"No Sonarr season row or episodes for season {season_number} (refresh the series in Sonarr).",
            }
        invalidate_series_list_cache(base_url, api_key)
        if season_toggled and ids:
            msg = f"Season {season_number} monitored (Sonarr season toggle + {len(ids)} episode(s))."
        elif season_toggled:
            msg = f"Season {season_number} marked monitored in Sonarr."
        else:
            msg = f"Monitored {len(ids)} episode(s) in season {season_number}."
        return {"ok": True, "message": msg}

    if episode_number is None:
        return {"ok": False, "message": "Episode number required."}

    target_id: int | None = None
    for ep in episodes:
        try:
            if int(ep.get("episodeNumber")) == int(episode_number):
                target_id = int(ep["id"])
                break
        except (TypeError, ValueError):
            continue
    if target_id is None:
        return {"ok": False, "message": "Episode not found in Sonarr."}

    await set_episodes_monitored(client, base_url, api_key, [target_id], True)
    invalidate_series_list_cache(base_url, api_key)
    return {"ok": True, "message": "Episode monitored in Sonarr."}


async def sonarr_remove_files_and_unmonitor(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    kind: SonarrKind,
    tvdb_id: int | None,
    series_title: str | None,
    season_number: int | None,
    episode_number: int | None,
) -> dict[str, Any]:
    """
    Unmonitor then delete episode file(s) via Sonarr.

    Files are removed on disk; Plex typically drops the items after the next library refresh.
    """
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    series = resolve_series(series_list, tvdb_id, series_title)
    if not series:
        return {"ok": False, "message": "Series not found in Sonarr."}

    sid = int(series["id"])

    if kind == "show":
        series["monitored"] = False
        await put_series(client, base_url, api_key, series)
        all_eps = await _all_series_episodes(client, base_url, api_key, sid)
        deleted = 0
        for ep in all_eps:
            eid = _episode_file_id(ep)
            if eid is not None:
                try:
                    await delete_episode_file(client, base_url, api_key, eid)
                    deleted += 1
                except httpx.HTTPError as exc:
                    logger.warning("Sonarr delete episode file failed: %s", exc)
        invalidate_series_list_cache(base_url, api_key)
        return {
            "ok": True,
            "message": f"Deleted {deleted} episode file(s) from disk (series unmonitored in Sonarr).",
        }

    if season_number is None:
        return {"ok": False, "message": "Season number required."}

    episodes = await fetch_episodes_for_season(client, base_url, api_key, sid, int(season_number))

    if kind == "season":
        ids = _episode_ids_for_api(episodes)
        if not ids:
            return {
                "ok": False,
                "message": f"No episodes in Sonarr for season {season_number} (cannot unmonitor or delete files).",
            }
        await set_episodes_monitored_batched(client, base_url, api_key, ids, False)
        await put_season_monitored(client, base_url, api_key, sid, int(season_number), monitored=False)
        deleted = 0
        for ep in episodes:
            eid = _episode_file_id(ep)
            if eid is not None:
                try:
                    await delete_episode_file(client, base_url, api_key, eid)
                    deleted += 1
                except httpx.HTTPError as exc:
                    logger.warning("Sonarr delete episode file failed: %s", exc)
        invalidate_series_list_cache(base_url, api_key)
        return {"ok": True, "message": f"Season unmonitored; deleted {deleted} episode file(s) from disk."}

    if episode_number is None:
        return {"ok": False, "message": "Episode number required."}

    target: dict[str, Any] | None = None
    for ep in episodes:
        try:
            if int(ep.get("episodeNumber")) == int(episode_number):
                target = ep
                break
        except (TypeError, ValueError):
            continue
    if not target or target.get("id") is None:
        return {"ok": False, "message": "Episode not found in Sonarr."}

    await set_episodes_monitored(client, base_url, api_key, [int(target["id"])], False)
    eid = _episode_file_id(target)
    if eid is not None:
        await delete_episode_file(client, base_url, api_key, eid)
        invalidate_series_list_cache(base_url, api_key)
        return {"ok": True, "message": "Episode file removed from disk (episode unmonitored in Sonarr)."}
    invalidate_series_list_cache(base_url, api_key)
    return {"ok": True, "message": "Episode unmonitored (no episode file in Sonarr to delete)."}


async def sonarr_delete(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    kind: SonarrKind,
    tvdb_id: int | None,
    series_title: str | None,
    season_number: int | None,
    episode_number: int | None,
) -> dict[str, Any]:
    """
    Delete at Sonarr scope: remove the entire series (show), or delete managed episode file(s)
    only for a season or single episode (monitored flags are left unchanged for season/episode).
    """
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    series = resolve_series(series_list, tvdb_id, series_title)
    if not series:
        return {"ok": False, "message": "Series not found in Sonarr."}

    sid = int(series["id"])

    if kind == "show":
        await delete_sonarr_series(
            client,
            base_url,
            api_key,
            sid,
            delete_files=True,
            add_import_list_exclusion=False,
        )
        invalidate_series_list_cache(base_url, api_key)
        return {
            "ok": True,
            "message": "Series removed from Sonarr; managed episode files were deleted per Sonarr.",
        }

    if season_number is None:
        return {"ok": False, "message": "Season number required."}

    episodes = await fetch_episodes_for_season(client, base_url, api_key, sid, int(season_number))

    if kind == "season":
        deleted = 0
        for ep in episodes:
            eid = _episode_file_id(ep)
            if eid is not None:
                try:
                    await delete_episode_file(client, base_url, api_key, eid)
                    deleted += 1
                except httpx.HTTPError as exc:
                    logger.warning("Sonarr delete episode file failed: %s", exc)
        invalidate_series_list_cache(base_url, api_key)
        return {
            "ok": True,
            "message": f"Deleted {deleted} episode file(s) for this season from disk (series remains in Sonarr).",
        }

    if episode_number is None:
        return {"ok": False, "message": "Episode number required."}

    target: dict[str, Any] | None = None
    for ep in episodes:
        try:
            if int(ep.get("episodeNumber")) == int(episode_number):
                target = ep
                break
        except (TypeError, ValueError):
            continue
    if not target:
        return {"ok": False, "message": "Episode not found in Sonarr."}

    eid = _episode_file_id(target)
    if eid is None:
        invalidate_series_list_cache(base_url, api_key)
        return {"ok": True, "message": "No episode file on disk to delete (episode still in Sonarr)."}
    await delete_episode_file(client, base_url, api_key, eid)
    invalidate_series_list_cache(base_url, api_key)
    return {
        "ok": True,
        "message": "Episode file deleted from disk (episode still listed in Sonarr; monitored unchanged).",
    }


async def _all_series_episodes(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
    *,
    on_exchange: SonarrExchangeHook = None,
) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/api/v3/episode"
    response = await client.get(
        url,
        headers={"X-Api-Key": api_key},
        params={"seriesId": series_id},
    )
    label = f"GET /api/v3/episode?seriesId={series_id}"
    if on_exchange:
        try:
            on_exchange(label, response.status_code, response.is_success)
        except Exception:
            logger.debug("Sonarr on_exchange failed", exc_info=True)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def _inventory_episode_tvdb_id(ep: dict[str, Any]) -> int | None:
    """TVDB series id from Tautulli/Plex inventory episode metadata."""
    from inspectarr.aggregate import tvdb_id_from_guid

    g = tvdb_id_from_guid(ep.get("grandparent_guid"))
    if g is not None:
        return g
    return tvdb_id_from_guid(ep.get("guid"))


def _inventory_episode_season_episode_numbers(ep: dict[str, Any]) -> tuple[int | None, int | None]:
    """Plex ``parent_media_index`` / ``media_index`` as ints when present."""
    try:
        sn = ep.get("parent_media_index")
        en = ep.get("media_index")
        if sn is None or en is None:
            return None, None
        return int(sn), int(en)
    except (TypeError, ValueError):
        return None, None


def _sonarr_season_episode_pairs_with_files(all_eps: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """``(seasonNumber, episodeNumber)`` for Sonarr episodes that currently have a file on disk."""
    pairs: set[tuple[int, int]] = set()
    for sep in all_eps:
        if not _episode_has_file_on_disk(sep):
            continue
        try:
            sn = int(sep["seasonNumber"])
            en = int(sep["episodeNumber"])
        except (KeyError, TypeError, ValueError):
            continue
        pairs.add((sn, en))
    return pairs


async def _ensure_sonarr_pairs_with_files_cached(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_id: int,
    sid_files_cache: dict[int, set[tuple[int, int]]],
    fetch_sem: asyncio.Semaphore,
) -> None:
    if series_id in sid_files_cache:
        return
    async with fetch_sem:
        if series_id in sid_files_cache:
            return
        all_eps = await _all_series_episodes(client, base_url, api_key, series_id)
        sid_files_cache[series_id] = _sonarr_season_episode_pairs_with_files(all_eps)


async def filter_inventory_episodes_by_sonarr_disk(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    episodes: list[dict[str, Any]],
    *,
    series_list: list[dict[str, Any]],
    sid_files_cache: dict[int, set[tuple[int, int]]],
    fetch_sem: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """
    Remove Tautulli inventory episodes whose series exists in Sonarr but that SxxEyy has no file.

    Rows are kept when the series is not in Sonarr, when S/E numbers are missing, or when Sonarr
    reports a file for that episode. This trims Plex metadata ghosts after disk deletes.
    """
    kept: list[dict[str, Any]] = []
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        tvdb_id = _inventory_episode_tvdb_id(ep)
        title = str(ep.get("grandparent_title") or "").strip()
        if tvdb_id is None and not title:
            kept.append(ep)
            continue
        series = resolve_series(series_list, tvdb_id, title if title else None)
        if not series:
            kept.append(ep)
            continue
        sid = int(series["id"])
        await _ensure_sonarr_pairs_with_files_cached(
            client, base_url, api_key, sid, sid_files_cache, fetch_sem
        )
        pairs = sid_files_cache.get(sid, set())
        sn, en = _inventory_episode_season_episode_numbers(ep)
        if sn is None or en is None:
            kept.append(ep)
            continue
        if (sn, en) in pairs:
            kept.append(ep)
    return kept


async def filter_library_inventory_results_by_sonarr_disk(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    inventory_results: list[InventoryFetchResult],
    *,
    max_parallel_series_fetches: int = 10,
) -> list[InventoryFetchResult]:
    """
    Apply :func:`filter_inventory_episodes_by_sonarr_disk` to each result's ``episodes`` list.

    Entries with ``server_id == "unknown"`` are passed through unchanged.
    """
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    sid_files_cache: dict[int, set[tuple[int, int]]] = {}
    fetch_sem = asyncio.Semaphore(max(1, int(max_parallel_series_fetches)))
    out: list[InventoryFetchResult] = []
    for inv in inventory_results:
        if inv.server_id == "unknown":
            out.append(inv)
            continue
        filtered = await filter_inventory_episodes_by_sonarr_disk(
            client,
            base_url,
            api_key,
            inv.episodes,
            series_list=series_list,
            sid_files_cache=sid_files_cache,
            fetch_sem=fetch_sem,
        )
        out.append(
            InventoryFetchResult(
                server_id=inv.server_id,
                server_name=inv.server_name,
                status=inv.status,
                shows=inv.shows,
                seasons=inv.seasons,
                episodes=filtered,
                section_progress=inv.section_progress,
                index_complete=inv.index_complete,
                error=inv.error,
            )
        )
    return out


def _library_unwatched_row_tvdb_id(item: dict[str, Any]) -> int | None:
    raw = item.get("tvdb_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _library_unwatched_row_series_title(item: dict[str, Any]) -> str | None:
    st = item.get("series_title")
    if st is not None and str(st).strip():
        return str(st).strip()
    t2 = item.get("title")
    if t2 is not None and str(t2).strip():
        return str(t2).strip()
    return None


def _sonarr_episodes_on_disk_show_scope(eps: list[dict[str, Any]]) -> int:
    return sum(1 for ep in eps if _episode_has_file_on_disk(ep))


def _sonarr_episodes_on_disk_season_scope(eps: list[dict[str, Any]], season_number: int) -> int:
    want = int(season_number)
    return sum(
        1
        for ep in eps
        if _episode_season_number(ep) == want and _episode_has_file_on_disk(ep)
    )


async def prune_library_unwatched_report_show_seasons_without_sonarr_files(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    report: dict[str, Any],
    *,
    max_parallel_series_fetches: int = 10,
) -> dict[str, Any]:
    """
    Remove cumulative and per-server unwatched *shows* and *seasons* when Sonarr has no episode
    files on disk for that scope (or the series is missing from Sonarr). Keeps Plex-only ghosts
    out of those lists while leaving rows that still have disk files (Tautulli watch logic
    unchanged). Uses the same cached ``/api/v3/series`` list as other Sonarr helpers.

    Season rows without a usable ``season_number`` fall back to the same series-wide file check
    as shows so they cannot stay listed when the parent series has no Sonarr files.
    """
    series_list = await fetch_series_list_cached(client, base_url, api_key)
    sid_cache: dict[int, list[dict[str, Any]]] = {}
    sem = asyncio.Semaphore(max(1, int(max_parallel_series_fetches)))

    async def ensure_eps(sid: int) -> None:
        if sid in sid_cache:
            return
        async with sem:
            if sid in sid_cache:
                return
            sid_cache[sid] = await _all_series_episodes(client, base_url, api_key, sid)

    def resolve_sid(item: dict[str, Any]) -> int | None:
        tvdb = _library_unwatched_row_tvdb_id(item)
        title = _library_unwatched_row_series_title(item)
        ser = resolve_series(series_list, tvdb, title)
        if not ser:
            return None
        try:
            return int(ser["id"])
        except (TypeError, ValueError, KeyError):
            return None

    needed: set[int] = set()
    cu = report.get("cumulative_unwatched") if isinstance(report.get("cumulative_unwatched"), dict) else {}
    for item in cu.get("shows") or []:
        if isinstance(item, dict) and (sid := resolve_sid(item)) is not None:
            needed.add(sid)
    for item in cu.get("seasons") or []:
        if isinstance(item, dict) and (sid := resolve_sid(item)) is not None:
            needed.add(sid)
    for card in report.get("per_server") or []:
        if not isinstance(card, dict):
            continue
        uw = card.get("unwatched")
        if not isinstance(uw, dict):
            continue
        for item in uw.get("shows") or []:
            if isinstance(item, dict) and (sid := resolve_sid(item)) is not None:
                needed.add(sid)
        for item in uw.get("seasons") or []:
            if isinstance(item, dict) and (sid := resolve_sid(item)) is not None:
                needed.add(sid)

    await asyncio.gather(*(ensure_eps(s) for s in sorted(needed)))

    def keep_show(item: dict[str, Any]) -> bool:
        sid = resolve_sid(item)
        if sid is None:
            return False
        eps = sid_cache.get(sid, [])
        return _sonarr_episodes_on_disk_show_scope(eps) > 0

    def keep_season(item: dict[str, Any]) -> bool:
        sid = resolve_sid(item)
        if sid is None:
            return False
        eps = sid_cache.get(sid, [])
        sn = item.get("season_number")
        if sn is None:
            return _sonarr_episodes_on_disk_show_scope(eps) > 0
        try:
            sn_int = int(sn)
        except (TypeError, ValueError):
            return _sonarr_episodes_on_disk_show_scope(eps) > 0
        return _sonarr_episodes_on_disk_season_scope(eps, sn_int) > 0

    if isinstance(cu.get("shows"), list):
        cu["shows"] = [x for x in cu["shows"] if isinstance(x, dict) and keep_show(x)]
    if isinstance(cu.get("seasons"), list):
        cu["seasons"] = [x for x in cu["seasons"] if isinstance(x, dict) and keep_season(x)]

    for card in report.get("per_server") or []:
        if not isinstance(card, dict):
            continue
        uw = card.get("unwatched")
        if not isinstance(uw, dict):
            continue
        if isinstance(uw.get("shows"), list):
            uw["shows"] = [x for x in uw["shows"] if isinstance(x, dict) and keep_show(x)]
        if isinstance(uw.get("seasons"), list):
            uw["seasons"] = [x for x in uw["seasons"] if isinstance(x, dict) and keep_season(x)]

    return report
