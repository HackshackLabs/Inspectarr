"""Radarr v3 API helpers for stale-movie library views."""

from __future__ import annotations

import hashlib
import logging
import time
from threading import Lock
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

RadarrExchangeHook = Callable[[str, int, bool], None] | None

_movie_list_cache_lock = Lock()
_movie_list_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_MOVIE_LIST_CACHE_TTL_SECONDS = 90.0


def _base(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _movie_cache_key(base_url: str, api_key: str) -> str:
    h = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:20]
    return f"{_base(base_url)}|{h}"


async def fetch_movie_list(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    on_exchange: RadarrExchangeHook = None,
) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/api/v3/movie"
    response = await client.get(url, headers={"X-Api-Key": api_key})
    if on_exchange:
        try:
            on_exchange("GET /api/v3/movie", response.status_code, response.is_success)
        except Exception:
            logger.debug("Radarr on_exchange failed", exc_info=True)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


async def fetch_movie_list_cached(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    on_exchange: RadarrExchangeHook = None,
) -> list[dict[str, Any]]:
    key = _movie_cache_key(base_url, api_key)
    now = time.monotonic()
    with _movie_list_cache_lock:
        ent = _movie_list_cache.get(key)
        if ent and (now - ent[0]) < _MOVIE_LIST_CACHE_TTL_SECONDS:
            return ent[1]
    data = await fetch_movie_list(client, base_url, api_key, on_exchange=on_exchange)
    with _movie_list_cache_lock:
        _movie_list_cache[key] = (now, data)
    return data


def invalidate_radarr_movie_list_cache(base_url: str, api_key: str) -> None:
    with _movie_list_cache_lock:
        _movie_list_cache.pop(_movie_cache_key(base_url, api_key), None)


async def radarr_get_movie_by_id(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    movie_id: int,
    *,
    on_exchange: RadarrExchangeHook = None,
) -> dict[str, Any] | None:
    url = f"{_base(base_url)}/api/v3/movie/{int(movie_id)}"
    response = await client.get(url, headers={"X-Api-Key": api_key})
    if on_exchange:
        try:
            on_exchange(f"GET /api/v3/movie/{int(movie_id)}", response.status_code, response.is_success)
        except Exception:
            logger.debug("Radarr on_exchange failed", exc_info=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else None


async def radarr_put_movie(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    *,
    on_exchange: RadarrExchangeHook = None,
) -> None:
    url = f"{_base(base_url)}/api/v3/movie"
    response = await client.put(url, headers={"X-Api-Key": api_key}, json=body)
    if on_exchange:
        try:
            on_exchange("PUT /api/v3/movie", response.status_code, response.is_success)
        except Exception:
            logger.debug("Radarr on_exchange failed", exc_info=True)
    response.raise_for_status()


async def radarr_delete_movie(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    movie_id: int,
    *,
    delete_files: bool = True,
    on_exchange: RadarrExchangeHook = None,
) -> None:
    q = "true" if delete_files else "false"
    url = f"{_base(base_url)}/api/v3/movie/{int(movie_id)}?deleteFiles={q}&addImportExclusion=false"
    response = await client.delete(url, headers={"X-Api-Key": api_key})
    if on_exchange:
        try:
            on_exchange(f"DELETE /api/v3/movie/{int(movie_id)}", response.status_code, response.is_success)
        except Exception:
            logger.debug("Radarr on_exchange failed", exc_info=True)
    response.raise_for_status()
