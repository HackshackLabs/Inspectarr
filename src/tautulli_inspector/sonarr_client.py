"""Sonarr v3 API helpers for library-unwatched actions."""

from __future__ import annotations

import hashlib
import logging
import time
from threading import Lock
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

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
) -> list[dict[str, Any]]:
    """Cached series list to avoid one full download per table row."""
    key = _series_cache_key(base_url, api_key)
    now = time.monotonic()
    with _series_list_cache_lock:
        ent = _series_list_cache.get(key)
        if ent and (now - ent[0]) < _SERIES_LIST_CACHE_TTL_SECONDS:
            return ent[1]
    data = await fetch_series_list(client, base_url, api_key)
    with _series_list_cache_lock:
        _series_list_cache[key] = (now, data)
    return data

SonarrKind = Literal["show", "season", "episode"]


def _base(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


async def fetch_series_list(client: httpx.AsyncClient, base_url: str, api_key: str) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/api/v3/series"
    response = await client.get(url, headers={"X-Api-Key": api_key})
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def resolve_series(
    series_list: list[dict[str, Any]],
    tvdb_id: int | None,
    series_title: str | None,
) -> dict[str, Any] | None:
    if tvdb_id is not None:
        for s in series_list:
            if s.get("tvdbId") == tvdb_id:
                return s
    title_norm = " ".join(str(series_title or "").strip().lower().split())
    if not title_norm:
        return None
    for s in series_list:
        if str(s.get("title") or "").strip().lower() == title_norm:
            return s
        clean = str(s.get("cleanTitle") or "").strip().lower()
        if clean and clean == title_norm.replace(" ", ""):
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
        return {"ok": True, "message": f"Series unmonitored; deleted {deleted} episode file(s) from disk."}

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
        return {"ok": True, "message": "Episode unmonitored and file removed from disk."}
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
) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/api/v3/episode"
    response = await client.get(
        url,
        headers={"X-Api-Key": api_key},
        params={"seriesId": series_id},
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []
