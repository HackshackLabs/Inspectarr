"""Optional Overseerr API helpers (Cold Storage request metadata)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from scoparr.iso_time import parse_iso8601_utc_epoch
from scoparr.settings import Settings

logger = logging.getLogger(__name__)

OVERSEERR_PAGE_SIZE = 50
OVERSEERR_MAX_PAGES = 400


def overseerr_is_configured(settings: Settings) -> bool:
    return bool(str(settings.overseerr_base_url or "").strip() and str(settings.overseerr_api_key or "").strip())


def _base(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _requested_by_display(user: Any) -> str | None:
    if not isinstance(user, dict):
        return None
    for key in ("displayName", "username", "email"):
        raw = user.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _media_positive_int(media: dict[str, Any], *field_names: str) -> int | None:
    """Overseerr JSON is usually camelCase; accept snake_case fallbacks."""
    for name in field_names:
        raw = media.get(name)
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def _new_overseerr_acc_blob() -> dict[str, Any]:
    return {
        "requested_at_epoch": None,
        "requested_by_names": [],
        "library_available_at_epoch": None,
    }


def _merge_request_into_bucket(
    bucket: dict[int, dict[str, Any]],
    key: int,
    *,
    created_epoch: int | None,
    media_added_epoch: int | None,
    who: str | None,
) -> None:
    cur = bucket.setdefault(key, _new_overseerr_acc_blob())
    if created_epoch is not None:
        prev = cur["requested_at_epoch"]
        if prev is None or created_epoch < prev:
            cur["requested_at_epoch"] = created_epoch
    if who and who not in cur["requested_by_names"]:
        cur["requested_by_names"].append(who)
    if media_added_epoch is not None:
        prev_a = cur["library_available_at_epoch"]
        if prev_a is None or media_added_epoch > prev_a:
            cur["library_available_at_epoch"] = media_added_epoch


def _accumulate_tv_request_row(
    acc_tvdb: dict[int, dict[str, Any]],
    acc_tmdb: dict[int, dict[str, Any]],
    row: dict[str, Any],
) -> None:
    media = row.get("media")
    if not isinstance(media, dict):
        return
    row_type = str(row.get("type") or media.get("mediaType") or media.get("media_type") or "").lower()
    if row_type != "tv":
        return

    tvdb = _media_positive_int(media, "tvdbId", "tvdb_id")
    tmdb = _media_positive_int(media, "tmdbId", "tmdb_id")
    if tvdb is None and tmdb is None:
        return

    created = parse_iso8601_utc_epoch(row.get("createdAt") or row.get("created_at"))
    media_added = parse_iso8601_utc_epoch(media.get("mediaAddedAt") or media.get("media_added_at"))
    who = _requested_by_display(row.get("requestedBy") or row.get("requested_by"))

    for bucket, kid in ((acc_tvdb, tvdb), (acc_tmdb, tmdb)):
        if kid is None:
            continue
        _merge_request_into_bucket(
            bucket,
            kid,
            created_epoch=created,
            media_added_epoch=media_added,
            who=who,
        )


def finalize_overseerr_tv_entry(blob: dict[str, Any]) -> dict[str, Any]:
    names: list[str] = list(blob.get("requested_by_names") or [])
    return {
        "requested_at_epoch": blob.get("requested_at_epoch"),
        "requested_by": ", ".join(names) if names else None,
        "library_available_at_epoch": blob.get("library_available_at_epoch"),
    }


async def fetch_overseerr_tv_request_maps(
    client: httpx.AsyncClient,
    settings: Settings,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Paginate ``GET /api/v1/request``; map TV show requests by TVDB id and by TMDB id (Overseerr's primary key)."""
    base = _base(settings.overseerr_base_url)
    key = str(settings.overseerr_api_key or "").strip()
    acc_tvdb: dict[int, dict[str, Any]] = {}
    acc_tmdb: dict[int, dict[str, Any]] = {}
    url = f"{base}/api/v1/request"
    headers = {"X-Api-Key": key, "Accept": "application/json"}

    for page in range(OVERSEERR_MAX_PAGES):
        skip = page * OVERSEERR_PAGE_SIZE
        response = await client.get(
            url,
            headers=headers,
            params={"take": OVERSEERR_PAGE_SIZE, "skip": skip},
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            break
        for row in results:
            if isinstance(row, dict):
                _accumulate_tv_request_row(acc_tvdb, acc_tmdb, row)
        if len(results) < OVERSEERR_PAGE_SIZE:
            break

    tvdb_out = {tid: finalize_overseerr_tv_entry(v) for tid, v in acc_tvdb.items()}
    tmdb_out = {mid: finalize_overseerr_tv_entry(v) for mid, v in acc_tmdb.items()}
    return tvdb_out, tmdb_out


async def fetch_overseerr_tvdb_request_map(
    client: httpx.AsyncClient,
    settings: Settings,
) -> dict[int, dict[str, Any]]:
    """Backward-compatible: TVDB-keyed map only. Prefer :func:`fetch_overseerr_tv_request_maps`."""
    tvdb_map, _ = await fetch_overseerr_tv_request_maps(client, settings)
    return tvdb_map
