"""Aggregation helpers for merging server activity results."""

import re
from datetime import datetime, timezone

from tautulli_inspector.models import ActivityFetchResult, HistoryFetchResult, InventoryFetchResult


def merge_activity(results: list[ActivityFetchResult]) -> dict:
    """Merge sessions and include per-server status information."""
    merged_sessions: list[dict] = []
    server_statuses: list[dict] = []

    for result in results:
        server_statuses.append(
            {
                "server_id": result.server_id,
                "server_name": result.server_name,
                "status": result.status,
                "error": result.error,
                "stream_count": len(result.sessions),
            }
        )
        for session in result.sessions:
            if not isinstance(session, dict):
                continue
            row = dict(session)
            row["server_id"] = result.server_id
            row["server_name"] = result.server_name
            merged_sessions.append(row)

    merged_sessions.sort(
        key=lambda item: (
            str(item.get("friendly_name") or item.get("user") or "").lower(),
            str(item.get("grandparent_title") or item.get("title") or "").lower(),
        )
    )

    return {
        "server_statuses": server_statuses,
        "sessions": merged_sessions,
        "total_streams": len(merged_sessions),
    }


def merge_history(results: list[HistoryFetchResult], start: int = 0, length: int = 50) -> dict:
    """Merge history rows from all servers with canonical UTC sorting."""
    merged_rows: list[dict] = []
    server_statuses: list[dict] = []

    for result in results:
        server_statuses.append(
            {
                "server_id": result.server_id,
                "server_name": result.server_name,
                "status": result.status,
                "error": result.error,
                "history_count": len(result.rows),
                "records_filtered": result.records_filtered,
                "records_total": result.records_total,
            }
        )
        for row in result.rows:
            if not isinstance(row, dict):
                continue
            normalized = dict(row)
            normalized["server_id"] = result.server_id
            normalized["server_name"] = result.server_name
            normalized["canonical_utc_epoch"] = _extract_canonical_utc_epoch(row)
            merged_rows.append(normalized)

    merged_rows.sort(key=lambda item: item.get("canonical_utc_epoch", 0), reverse=True)
    total_rows = len(merged_rows)
    page_start = max(start, 0)
    page_end = page_start + max(length, 1)
    paged_rows = merged_rows[page_start:page_end]

    server_statuses.sort(
        key=lambda item: (
            str(item.get("server_name") or "").lower(),
            str(item.get("server_id") or "").lower(),
        )
    )

    return {
        "server_statuses": server_statuses,
        "rows": paged_rows,
        "total_rows": total_rows,
        "start": page_start,
        "length": max(length, 1),
        "returned_rows": len(paged_rows),
    }


def build_unwatched_media_report(
    rows: list[dict],
    cutoff_epoch: int,
    media_type: str,
    max_items: int = 200,
) -> dict:
    """Build per-server and cumulative media watch index and stale candidates."""
    normalized_media_type = media_type.strip().lower()
    indexed: dict[str, dict] = {}

    for row in rows:
        row_media_type = str(row.get("media_type") or "").lower()
        if row_media_type != normalized_media_type:
            continue

        key, display_title = _media_key_and_title(row, row_media_type)
        epoch = int(row.get("canonical_utc_epoch", 0))
        server_id = str(row.get("server_id") or "unknown")
        server_name = str(row.get("server_name") or "Unknown")

        item = indexed.setdefault(
            key,
            {
                "key": key,
                "media_type": row_media_type,
                "display_title": display_title,
                "global_play_count": 0,
                "global_last_watched_epoch": 0,
                "servers": {},
            },
        )
        item["global_play_count"] += 1
        item["global_last_watched_epoch"] = max(item["global_last_watched_epoch"], epoch)

        server_entry = item["servers"].setdefault(
            server_id,
            {
                "server_id": server_id,
                "server_name": server_name,
                "play_count": 0,
                "last_watched_epoch": 0,
            },
        )
        server_entry["play_count"] += 1
        server_entry["last_watched_epoch"] = max(server_entry["last_watched_epoch"], epoch)

    all_items = list(indexed.values())
    all_items.sort(key=lambda item: item["global_play_count"], reverse=True)

    cumulative_unwatched = [
        item for item in all_items if item["global_last_watched_epoch"] and item["global_last_watched_epoch"] < cutoff_epoch
    ]
    cumulative_unwatched.sort(key=lambda item: item["global_last_watched_epoch"])

    per_server_unwatched: dict[str, dict] = {}
    for item in all_items:
        for server_id, server_data in item["servers"].items():
            if not server_data["last_watched_epoch"] or server_data["last_watched_epoch"] >= cutoff_epoch:
                continue
            entry = per_server_unwatched.setdefault(
                server_id,
                {
                    "server_id": server_id,
                    "server_name": server_data["server_name"],
                    "items": [],
                },
            )
            entry["items"].append(
                {
                    "display_title": item["display_title"],
                    "last_watched_epoch": server_data["last_watched_epoch"],
                    "play_count": server_data["play_count"],
                }
            )

    for entry in per_server_unwatched.values():
        entry["items"].sort(key=lambda item: item["last_watched_epoch"])
        entry["items"] = entry["items"][: max(max_items, 1)]

    return {
        "media_type": normalized_media_type,
        "indexed_item_count": len(all_items),
        "cumulative_top": all_items[: max(max_items, 1)],
        "cumulative_unwatched": cumulative_unwatched[: max(max_items, 1)],
        "per_server_unwatched": sorted(per_server_unwatched.values(), key=lambda item: item["server_name"].lower()),
    }


def build_library_unwatched_tv_report(
    inventory_results: list[InventoryFetchResult],
    history_rows: list[dict],
    index_start_epoch: int,
    index_end_epoch: int,
    max_items: int = 500,
) -> dict:
    """Identify shows/seasons/episodes not watched in the index window."""
    watched_episode_ids_by_server: dict[str, set[str]] = {}
    watched_episode_ids_global: set[str] = set()
    watched_season_ids_by_server: dict[str, set[str]] = {}
    watched_season_ids_global: set[str] = set()

    for row in history_rows:
        media_type = str(row.get("media_type") or "").lower()
        if media_type != "episode":
            continue
        row_epoch = int(row.get("canonical_utc_epoch") or 0)
        if row_epoch < index_start_epoch or row_epoch > index_end_epoch:
            continue
        server_id = str(row.get("server_id") or "")
        season_id = _season_identity_from_history_row(row)
        if not server_id:
            continue
        for watch_key in _library_episode_watch_keys(row):
            watched_episode_ids_global.add(watch_key)
            watched_episode_ids_by_server.setdefault(server_id, set()).add(watch_key)
        if season_id:
            watched_season_ids_global.add(season_id)
            watched_season_ids_by_server.setdefault(server_id, set()).add(season_id)
        parent_rk = str(row.get("parent_rating_key") or "").strip()
        if parent_rk:
            sk = f"season:rk:{parent_rk}"
            watched_season_ids_global.add(sk)
            watched_season_ids_by_server.setdefault(server_id, set()).add(sk)

    watched_episode_ids_ever_by_server: dict[str, set[str]] = {
        server_id: set(values) for server_id, values in watched_episode_ids_by_server.items()
    }
    watched_episode_ids_ever_global: set[str] = set(watched_episode_ids_global)

    global_episode_entries: dict[str, dict] = {}
    global_season_entries: dict[str, dict] = {}
    global_show_entries: dict[str, dict] = {}
    global_season_episode_ids: dict[str, set[str]] = {}
    global_show_episode_ids: dict[str, set[str]] = {}
    watched_show_ids_ever_global: set[str] = set()
    per_server: list[dict] = []

    for result in inventory_results:
        episodes = [row for row in result.episodes if isinstance(row, dict)]
        shows = [row for row in result.shows if isinstance(row, dict)]
        watched_on_server = watched_episode_ids_by_server.get(result.server_id, set())
        watched_show_ids_ever_server: set[str] = set()

        server_episode_entries: dict[str, dict] = {}
        server_season_entries: dict[str, dict] = {}
        server_show_entries: dict[str, dict] = {}
        server_season_episode_ids: dict[str, set[str]] = {}
        server_show_episode_ids: dict[str, set[str]] = {}

        for episode in episodes:
            episode_id = _episode_identity_from_inventory(episode)
            if not episode_id:
                rk_only = str(episode.get("rating_key") or "").strip()
                if rk_only:
                    episode_id = f"episode:rk:{rk_only}"
                else:
                    continue
            season_id = _season_identity_from_episode(episode)
            show_id = _show_identity_from_episode(episode)

            episode_entry = _episode_entry(episode)
            episode_entry["key"] = episode_id
            episode_entry["tautulli_server_id"] = result.server_id
            if episode_id not in global_episode_entries:
                global_episode_entries[episode_id] = episode_entry
            else:
                _merge_library_action_metadata(global_episode_entries[episode_id], episode_entry)
            server_episode_entries.setdefault(episode_id, episode_entry)

            if season_id:
                season_entry = _season_entry_from_episode(episode)
                season_entry["key"] = season_id
                season_entry["tautulli_server_id"] = result.server_id
                if season_id not in global_season_entries:
                    global_season_entries[season_id] = season_entry
                else:
                    _merge_library_action_metadata(global_season_entries[season_id], season_entry)
                server_season_entries.setdefault(season_id, season_entry)
                global_season_episode_ids.setdefault(season_id, set()).add(episode_id)
                server_season_episode_ids.setdefault(season_id, set()).add(episode_id)

            if show_id:
                show_entry = _show_entry_from_episode(episode)
                show_entry["key"] = show_id
                show_entry["tautulli_server_id"] = result.server_id
                if show_id not in global_show_entries:
                    global_show_entries[show_id] = show_entry
                else:
                    _merge_library_action_metadata(global_show_entries[show_id], show_entry)
                server_show_entries.setdefault(show_id, show_entry)
                global_show_episode_ids.setdefault(show_id, set()).add(episode_id)
                server_show_episode_ids.setdefault(show_id, set()).add(episode_id)

            # For show-level "unwatched" rules, treat any historical watch evidence
            # (play counters / last viewed metadata) as watched-ever.
            if _episode_has_watch_record(episode):
                for k in _library_episode_watch_keys(episode):
                    watched_episode_ids_ever_by_server.setdefault(result.server_id, set()).add(k)
                    watched_episode_ids_ever_global.add(k)

        # Show-level metadata is often more reliable for all-time watch evidence.
        for show in shows:
            show_id = _show_identity_from_show(show)
            if not show_id:
                continue
            if _item_has_watch_record(show):
                watched_show_ids_ever_server.add(show_id)
                watched_show_ids_ever_global.add(show_id)

        episode_unwatched_server = [
            entry
            for ep_id, entry in server_episode_entries.items()
            if _library_episode_watch_keys_from_entry(entry).isdisjoint(watched_on_server)
        ]

        season_unwatched_server = []
        for season_id, entry in server_season_entries.items():
            episode_ids = server_season_episode_ids.get(season_id, set())
            watched_seasons_server = watched_season_ids_by_server.get(result.server_id, set())
            if _library_season_watched_keys(season_id, entry) & watched_seasons_server:
                continue
            if episode_ids and not _any_episode_keys_intersect_watched(
                episode_ids, server_episode_entries, watched_on_server
            ):
                season_copy = dict(entry)
                season_copy["episode_count"] = len(episode_ids)
                season_unwatched_server.append(season_copy)

        show_unwatched_server = []
        for show_id, entry in server_show_entries.items():
            episode_ids = server_show_episode_ids.get(show_id, set())
            watched_ever_on_server = watched_episode_ids_ever_by_server.get(result.server_id, set())
            if show_id in watched_show_ids_ever_server:
                continue
            if episode_ids and not _any_episode_keys_intersect_watched(
                episode_ids, server_episode_entries, watched_ever_on_server
            ):
                show_copy = dict(entry)
                show_copy["episode_count"] = len(episode_ids)
                show_unwatched_server.append(show_copy)

        status_str = str(result.status or "").strip() or "unknown"
        per_server.append(
            {
                "server_id": result.server_id,
                "server_name": result.server_name,
                "status": status_str,
                "error": result.error,
                "index_complete": bool(result.index_complete),
                "section_progress": result.section_progress,
                "inventory_counts": {
                    "shows": len(server_show_entries),
                    "seasons": len(server_season_entries),
                    "episodes": len(server_episode_entries),
                },
                "unwatched": {
                    "shows": sorted(show_unwatched_server, key=lambda x: x["title"].lower())[: max(max_items, 1)],
                    "seasons": sorted(season_unwatched_server, key=lambda x: x["title"].lower())[: max(max_items, 1)],
                    "episodes": sorted(episode_unwatched_server, key=lambda x: x["title"].lower())[: max(max_items, 1)],
                },
            }
        )

    cumulative_unwatched_episodes = [
        entry
        for ep_id, entry in global_episode_entries.items()
        if _library_episode_watch_keys_from_entry(entry).isdisjoint(watched_episode_ids_global)
    ]
    cumulative_unwatched_seasons = []
    for season_id, entry in global_season_entries.items():
        episode_ids = global_season_episode_ids.get(season_id, set())
        if _library_season_watched_keys(season_id, entry) & watched_season_ids_global:
            continue
        if episode_ids and not _any_episode_keys_intersect_watched(
            episode_ids, global_episode_entries, watched_episode_ids_global
        ):
            season_copy = dict(entry)
            season_copy["episode_count"] = len(episode_ids)
            cumulative_unwatched_seasons.append(season_copy)
    cumulative_unwatched_shows = []
    for show_id, entry in global_show_entries.items():
        episode_ids = global_show_episode_ids.get(show_id, set())
        if show_id in watched_show_ids_ever_global:
            continue
        if episode_ids and not _any_episode_keys_intersect_watched(
            episode_ids, global_episode_entries, watched_episode_ids_ever_global
        ):
            show_copy = dict(entry)
            show_copy["episode_count"] = len(episode_ids)
            cumulative_unwatched_shows.append(show_copy)

    return {
        "index_start_epoch": index_start_epoch,
        "index_end_epoch": index_end_epoch,
        "cumulative_unwatched": {
            "shows": sorted(_dedupe_by_key(cumulative_unwatched_shows), key=lambda x: x["title"].lower())[: max(max_items, 1)],
            "seasons": sorted(_dedupe_by_key(cumulative_unwatched_seasons), key=lambda x: x["title"].lower())[: max(max_items, 1)],
            "episodes": sorted(_dedupe_by_key(cumulative_unwatched_episodes), key=lambda x: x["title"].lower())[: max(max_items, 1)],
        },
        "per_server": sorted(per_server, key=lambda item: item["server_name"].lower()),
    }


def canonical_utc_epoch_for_row(row: dict) -> int:
    """Public wrapper for row timestamp normalization."""
    return _extract_canonical_utc_epoch(row)


def epoch_to_utc_display(epoch: int) -> str:
    if epoch <= 0:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _extract_canonical_utc_epoch(row: dict) -> int:
    """Normalize available history timestamp fields into UTC epoch seconds."""
    for key in ("started", "date", "stopped"):
        value = row.get(key)
        parsed = _parse_epoch(value)
        if parsed is not None:
            return parsed

    for key in ("started_at", "date_time"):
        value = row.get(key)
        parsed = _parse_iso_datetime(value)
        if parsed is not None:
            return parsed

    return 0


def _media_key_and_title(row: dict, media_type: str) -> tuple[str, str]:
    rating_key = (
        row.get("rating_key")
        or row.get("parent_rating_key")
        or row.get("grandparent_rating_key")
        or ""
    )
    if media_type == "episode":
        series = str(row.get("grandparent_title") or "Unknown Series")
        season = row.get("parent_media_index")
        episode = row.get("media_index")
        episode_title = str(row.get("title") or "")
        season_episode = ""
        if season is not None and episode is not None:
            season_episode = f"S{season}E{episode} "
        display_title = f"{series} {season_episode}{episode_title}".strip()
    else:
        display_title = str(row.get("full_title") or row.get("title") or "Unknown Title")

    normalized_display = display_title.lower()
    key = f"{media_type}:{rating_key or normalized_display}"
    return key, display_title


def tvdb_id_from_guid(guid: object) -> int | None:
    """Parse TVDB series id from a Plex/Tautulli guid (thetvdb agent URLs)."""
    s = str(guid or "").strip()
    if not s:
        return None
    if "thetvdb" not in s.lower():
        return None
    match = re.search(r"thetvdb://(\d+)", s, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _episode_tvdb_id(episode: dict) -> int | None:
    return tvdb_id_from_guid(episode.get("guid")) or tvdb_id_from_guid(episode.get("grandparent_guid"))


def _merge_library_action_metadata(dst: dict, src: dict) -> None:
    """Fill missing Sonarr/Plex linkage fields on cumulative rows from another server's copy."""
    keys_str = (
        "rating_key",
        "grandparent_rating_key",
        "parent_rating_key",
        "server_show_rating_key",
        "server_season_rating_key",
        "season_rating_key",
        "show_rating_key",
        "guid",
        "tautulli_server_id",
    )
    for key in keys_str:
        if str(dst.get(key) or "").strip():
            continue
        v = src.get(key)
        if v is not None and str(v).strip():
            dst[key] = v
    if not dst.get("tvdb_id") and src.get("tvdb_id"):
        dst["tvdb_id"] = src["tvdb_id"]
    if not str(dst.get("series_title") or "").strip():
        st = src.get("series_title")
        if st is not None and str(st).strip():
            dst["series_title"] = str(st).strip()


def _episode_entry(episode: dict) -> dict:
    episode_no = episode.get("media_index")
    season_no = episode.get("parent_media_index")
    series_title = str(episode.get("grandparent_title") or "Unknown Series")
    title = str(episode.get("title") or "Unknown Episode")
    prefix = f"S{season_no}E{episode_no} " if season_no is not None and episode_no is not None else ""
    return {
        "key": f"episode:{episode.get('rating_key')}",
        "title": f"{series_title} {prefix}{title}".strip(),
        "series_title": series_title,
        "season_number": season_no,
        "episode_number": episode_no,
        "rating_key": str(episode.get("rating_key") or ""),
        "grandparent_rating_key": str(episode.get("grandparent_rating_key") or ""),
        "parent_rating_key": str(episode.get("parent_rating_key") or ""),
        "server_show_rating_key": str(episode.get("server_show_rating_key") or ""),
        "server_season_rating_key": str(episode.get("server_season_rating_key") or ""),
        "show_rating_key": str(episode.get("server_show_rating_key") or ""),
        "season_rating_key": str(episode.get("server_season_rating_key") or ""),
        "guid": str(episode.get("guid") or ""),
        "tvdb_id": _episode_tvdb_id(episode),
    }


def _season_entry(season: dict, episode_count: int) -> dict:
    series_title = str(season.get("parent_title") or season.get("grandparent_title") or "Unknown Series")
    season_number = season.get("media_index") or season.get("parent_media_index")
    title = str(season.get("title") or f"Season {season_number or '?'}")
    return {
        "key": f"season:{season.get('rating_key')}",
        "title": f"{series_title} - {title}",
        "series_title": series_title,
        "season_number": season_number,
        "episode_count": episode_count,
        "rating_key": str(season.get("rating_key") or ""),
    }


def _show_entry(show: dict, episode_count: int) -> dict:
    title = str(show.get("title") or "Unknown Show")
    return {
        "key": f"show:{show.get('rating_key')}",
        "title": title,
        "episode_count": episode_count,
        "rating_key": str(show.get("rating_key") or ""),
    }


def _library_episode_watch_keys(row: dict) -> set[str]:
    """Keys used to join Tautulli history rows to Plex inventory episodes on the same server."""
    keys: set[str] = set()
    ident = _episode_identity_from_row(row)
    if ident:
        keys.add(ident)
    rk = str(row.get("rating_key") or "").strip()
    if rk:
        keys.add(f"episode:rk:{rk}")
    return keys


def _library_episode_watch_keys_from_entry(entry: dict) -> set[str]:
    """Inventory episode entry fields produced by _episode_entry (same logical row as Tautulli)."""
    keys: set[str] = set()
    row = {
        "grandparent_title": entry.get("series_title"),
        "parent_media_index": entry.get("season_number"),
        "media_index": entry.get("episode_number"),
        "title": None,
    }
    ident = _episode_identity_from_row(row)
    if ident:
        keys.add(ident)
    rk = str(entry.get("rating_key") or "").strip()
    if rk:
        keys.add(f"episode:rk:{rk}")
    return keys


def _library_season_watched_keys(season_id: str, entry: dict) -> set[str]:
    keys = {season_id} if season_id else set()
    srk = str(entry.get("season_rating_key") or entry.get("rating_key") or "").strip()
    if srk:
        keys.add(f"season:rk:{srk}")
    return keys


def _any_episode_keys_intersect_watched(
    episode_ids: set[str],
    episode_entries: dict[str, dict],
    watched: set[str],
) -> bool:
    for eid in episode_ids:
        entry = episode_entries.get(eid)
        if not entry:
            continue
        if _library_episode_watch_keys_from_entry(entry) & watched:
            return True
    return False


def _episode_identity_from_row(row: dict) -> str:
    series = _normalized_text(row.get("grandparent_title") or row.get("parent_title"))
    season = _normalized_text(row.get("parent_media_index"))
    episode = _normalized_text(row.get("media_index"))
    title = _normalized_text(row.get("title"))
    if series and season and episode:
        return f"episode:{series}:s{season}:e{episode}"
    if series and title:
        return f"episode:{series}:{title}"
    return ""


def _episode_identity_from_inventory(episode: dict) -> str:
    return _episode_identity_from_row(episode)


def _season_identity_from_episode(episode: dict) -> str:
    series = _normalized_text(episode.get("grandparent_title") or episode.get("parent_title"))
    season = _normalized_text(episode.get("parent_media_index"))
    if series and season:
        return f"season:{series}:s{season}"
    season_title = _normalized_text(episode.get("parent_title"))
    if series and season_title:
        return f"season:{series}:{season_title}"
    return ""


def _season_identity_from_history_row(row: dict) -> str:
    """Best-effort season identity extraction from history row."""
    series = _normalized_text(row.get("grandparent_title") or row.get("parent_title"))
    season = _normalized_text(row.get("parent_media_index"))
    if series and season:
        return f"season:{series}:s{season}"

    full_title = str(row.get("full_title") or row.get("title") or "").strip()
    if not full_title:
        return ""

    # Common formats: "Show - Episode (S4 · E1)" or "Show - ... S4E1"
    match = re.search(r"[Ss]\s*(\d+)\s*(?:[.\u00b7_\-\s]*)[Ee]\s*(\d+)", full_title)
    if not match:
        return ""
    season_no = match.group(1)

    series_name = full_title
    if " - " in full_title:
        series_name = full_title.split(" - ", 1)[0]
    series_norm = _normalized_text(series_name)
    if not series_norm:
        return ""
    return f"season:{series_norm}:s{season_no}"


def _show_identity_from_episode(episode: dict) -> str:
    series = _normalized_text(episode.get("grandparent_title") or episode.get("parent_title"))
    if not series:
        return ""
    return f"show:{series}"


def _show_identity_from_show(show: dict) -> str:
    title = _normalized_text(show.get("title"))
    if not title:
        return ""
    return f"show:{title}"


def _season_entry_from_episode(episode: dict) -> dict:
    series_title = str(episode.get("grandparent_title") or episode.get("parent_title") or "Unknown Series")
    season_number = episode.get("parent_media_index")
    if season_number is not None and str(season_number) != "":
        title = f"{series_title} - Season {season_number}"
    else:
        parent_title = str(episode.get("parent_title") or "Season")
        title = f"{series_title} - {parent_title}"
    return {
        "key": "",
        "title": title,
        "series_title": series_title,
        "season_number": season_number,
        "episode_count": 0,
        "rating_key": str(
            episode.get("server_season_rating_key") or episode.get("parent_rating_key") or ""
        ),
        "show_rating_key": str(episode.get("server_show_rating_key") or ""),
        "season_rating_key": str(episode.get("server_season_rating_key") or ""),
        "tvdb_id": _episode_tvdb_id(episode),
    }


def _show_entry_from_episode(episode: dict) -> dict:
    series_title = str(episode.get("grandparent_title") or episode.get("parent_title") or "Unknown Show")
    return {
        "key": "",
        "title": series_title,
        "series_title": series_title,
        "episode_count": 0,
        "rating_key": str(episode.get("server_show_rating_key") or episode.get("grandparent_rating_key") or ""),
        "show_rating_key": str(episode.get("server_show_rating_key") or ""),
        "tvdb_id": tvdb_id_from_guid(episode.get("grandparent_guid")) or _episode_tvdb_id(episode),
    }


def _normalized_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _episode_has_watch_record(episode: dict) -> bool:
    """Best-effort all-time watch detection from inventory metadata."""
    return _item_has_watch_record(episode)


def _item_has_watch_record(item: dict) -> bool:
    """Best-effort all-time watch detection from metadata fields."""
    for key in ("play_count", "view_count"):
        value = _as_int(item.get(key))
        if value is not None and value > 0:
            return True
    for key in ("last_viewed_at", "last_played"):
        value = _as_int(item.get(key))
        if value is not None and value > 0:
            return True
    return False


def _as_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _dedupe_by_key(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = str(item.get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _parse_epoch(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None
