"""Tests for Sonarr disk pruning of library-unwatched show/season rows."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tautulli_inspector.sonarr_client import prune_library_unwatched_report_show_seasons_without_sonarr_files


class LibraryUnwatchedSonarrPruneTests(unittest.TestCase):
    def test_drops_cumulative_show_when_sonarr_has_no_files(self) -> None:
        report: dict = {
            "cumulative_unwatched": {
                "shows": [
                    {
                        "title": "Ghost Show",
                        "series_title": "Ghost Show",
                        "tvdb_id": 99999,
                        "episode_count": 3,
                    },
                ],
                "seasons": [],
            },
            "per_server": [],
        }
        series = [{"id": 7, "tvdbId": 99999, "title": "Ghost Show"}]

        async def run() -> None:
            mock_client = MagicMock()
            with (
                patch(
                    "tautulli_inspector.sonarr_client.fetch_series_list_cached",
                    new_callable=AsyncMock,
                    return_value=series,
                ),
                patch(
                    "tautulli_inspector.sonarr_client._all_series_episodes",
                    new_callable=AsyncMock,
                    return_value=[
                        {"seasonNumber": 1, "episodeNumber": 1, "hasFile": False},
                    ],
                ),
            ):
                await prune_library_unwatched_report_show_seasons_without_sonarr_files(
                    mock_client, "http://sonarr", "key", report
                )

        asyncio.run(run())
        self.assertEqual(report["cumulative_unwatched"]["shows"], [])

    def test_keeps_cumulative_show_when_sonarr_has_file(self) -> None:
        report: dict = {
            "cumulative_unwatched": {
                "shows": [
                    {
                        "title": "Real Show",
                        "series_title": "Real Show",
                        "tvdb_id": 88888,
                        "episode_count": 1,
                    },
                ],
                "seasons": [],
            },
            "per_server": [],
        }
        series = [{"id": 3, "tvdbId": 88888, "title": "Real Show"}]

        async def run() -> None:
            mock_client = MagicMock()
            with (
                patch(
                    "tautulli_inspector.sonarr_client.fetch_series_list_cached",
                    new_callable=AsyncMock,
                    return_value=series,
                ),
                patch(
                    "tautulli_inspector.sonarr_client._all_series_episodes",
                    new_callable=AsyncMock,
                    return_value=[
                        {"seasonNumber": 1, "episodeNumber": 1, "hasFile": True},
                    ],
                ),
            ):
                await prune_library_unwatched_report_show_seasons_without_sonarr_files(
                    mock_client, "http://sonarr", "key", report
                )

        asyncio.run(run())
        self.assertEqual(len(report["cumulative_unwatched"]["shows"]), 1)

    def test_season_dropped_when_that_season_has_no_files(self) -> None:
        report: dict = {
            "cumulative_unwatched": {
                "shows": [],
                "seasons": [
                    {
                        "title": "Real Show - Season 2",
                        "series_title": "Real Show",
                        "season_number": 2,
                        "tvdb_id": 77777,
                        "episode_count": 4,
                    },
                ],
            },
            "per_server": [],
        }
        series = [{"id": 5, "tvdbId": 77777, "title": "Real Show"}]

        async def run() -> None:
            mock_client = MagicMock()
            with (
                patch(
                    "tautulli_inspector.sonarr_client.fetch_series_list_cached",
                    new_callable=AsyncMock,
                    return_value=series,
                ),
                patch(
                    "tautulli_inspector.sonarr_client._all_series_episodes",
                    new_callable=AsyncMock,
                    return_value=[
                        {"seasonNumber": 1, "episodeNumber": 1, "hasFile": True},
                    ],
                ),
            ):
                await prune_library_unwatched_report_show_seasons_without_sonarr_files(
                    mock_client, "http://sonarr", "key", report
                )

        asyncio.run(run())
        self.assertEqual(report["cumulative_unwatched"]["seasons"], [])

    def test_per_server_lists_pruned_same_as_cumulative(self) -> None:
        report: dict = {
            "cumulative_unwatched": {"shows": [], "seasons": []},
            "per_server": [
                {
                    "server_id": "s1",
                    "unwatched": {
                        "shows": [
                            {
                                "title": "Gone",
                                "series_title": "Gone",
                                "tvdb_id": 44444,
                                "episode_count": 1,
                            },
                        ],
                        "seasons": [],
                    },
                }
            ],
        }
        series = [{"id": 9, "tvdbId": 44444, "title": "Gone"}]

        async def run() -> None:
            mock_client = MagicMock()
            with (
                patch(
                    "tautulli_inspector.sonarr_client.fetch_series_list_cached",
                    new_callable=AsyncMock,
                    return_value=series,
                ),
                patch(
                    "tautulli_inspector.sonarr_client._all_series_episodes",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
            ):
                await prune_library_unwatched_report_show_seasons_without_sonarr_files(
                    mock_client, "http://sonarr", "key", report
                )

        asyncio.run(run())
        self.assertEqual(report["per_server"][0]["unwatched"]["shows"], [])


if __name__ == "__main__":
    unittest.main()
