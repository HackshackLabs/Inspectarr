"""Tests for server-side Sonarr disk filtering of library-unwatched inventory."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tautulli_inspector.models import InventoryFetchResult
from tautulli_inspector.sonarr_client import (
    _sonarr_season_episode_pairs_with_files,
    filter_inventory_episodes_by_sonarr_disk,
    filter_library_inventory_results_by_sonarr_disk,
)


def test_sonarr_season_episode_pairs_with_files() -> None:
    eps = [
        {"seasonNumber": 1, "episodeNumber": 1, "hasFile": True},
        {"seasonNumber": 1, "episodeNumber": 2, "hasFile": False},
        {"seasonNumber": 1, "episodeNumber": 3, "episodeFileId": 5},
    ]
    s = _sonarr_season_episode_pairs_with_files(eps)
    assert s == {(1, 1), (1, 3)}


def test_filter_drops_episode_without_sonarr_file() -> None:
    async def run() -> None:
        series_list = [{"id": 9, "tvdbId": 12345, "title": "Test Show"}]
        inv_eps = [
            {
                "guid": "com.plexapp.agents.thetvdb://12345?lang=en",
                "grandparent_title": "Test Show",
                "parent_media_index": 1,
                "media_index": 1,
                "rating_key": "keep",
            },
            {
                "guid": "com.plexapp.agents.thetvdb://12345?lang=en",
                "grandparent_title": "Test Show",
                "parent_media_index": 1,
                "media_index": 2,
                "rating_key": "drop",
            },
        ]
        sonarr_eps = [
            {"seasonNumber": 1, "episodeNumber": 1, "hasFile": True},
            {"seasonNumber": 1, "episodeNumber": 2, "hasFile": False},
        ]
        client = MagicMock()
        cache: dict[int, set[tuple[int, int]]] = {}
        sem = asyncio.Semaphore(10)
        with patch(
            "tautulli_inspector.sonarr_client._all_series_episodes",
            new=AsyncMock(return_value=sonarr_eps),
        ):
            out = await filter_inventory_episodes_by_sonarr_disk(
                client,
                "http://sonarr",
                "key",
                inv_eps,
                series_list=series_list,
                sid_files_cache=cache,
                fetch_sem=sem,
            )
        assert len(out) == 1
        assert out[0]["rating_key"] == "keep"

    asyncio.run(run())


def test_filter_keeps_when_series_not_in_sonarr() -> None:
    async def run() -> None:
        series_list = [{"id": 9, "tvdbId": 99999, "title": "Other"}]
        inv_eps = [
            {
                "guid": "com.plexapp.agents.thetvdb://12345?lang=en",
                "grandparent_title": "Only In Plex",
                "parent_media_index": 1,
                "media_index": 1,
                "rating_key": "x",
            },
        ]
        client = MagicMock()
        with patch(
            "tautulli_inspector.sonarr_client._all_series_episodes",
            new=AsyncMock(return_value=[]),
        ):
            out = await filter_inventory_episodes_by_sonarr_disk(
                client,
                "http://sonarr",
                "key",
                inv_eps,
                series_list=series_list,
                sid_files_cache={},
                fetch_sem=asyncio.Semaphore(10),
            )
        assert len(out) == 1

    asyncio.run(run())


def test_filter_library_inventory_results_skips_unknown_server() -> None:
    async def run() -> None:
        inv = InventoryFetchResult(
            server_id="unknown",
            server_name="?",
            status="ok",
            episodes=[{"rating_key": "1"}],
        )
        with patch(
            "tautulli_inspector.sonarr_client.fetch_series_list_cached",
            new=AsyncMock(return_value=[]),
        ):
            out = await filter_library_inventory_results_by_sonarr_disk(
                MagicMock(),
                "http://sonarr",
                "key",
                [inv],
            )
        assert len(out) == 1
        assert out[0].episodes == inv.episodes

    asyncio.run(run())
