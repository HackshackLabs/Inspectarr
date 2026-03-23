"""Aggregation and normalization tests."""

import unittest

from tautulli_inspector.aggregate import (
    build_library_unwatched_tv_report,
    build_unwatched_media_report,
    merge_activity,
    merge_history,
    tvdb_id_from_guid,
)
from tautulli_inspector.sonarr_client import resolve_series
from tautulli_inspector.models import ActivityFetchResult, HistoryFetchResult, InventoryFetchResult


class MergeActivityTests(unittest.TestCase):
    def test_merge_activity_tags_server_metadata(self) -> None:
        results = [
            ActivityFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                sessions=[{"user": "alice", "title": "Movie A"}],
            ),
            ActivityFetchResult(
                server_id="s2",
                server_name="Server 2",
                status="ok",
                sessions=[{"user": "bob", "title": "Movie B"}],
            ),
        ]

        merged = merge_activity(results)

        self.assertEqual(2, merged["total_streams"])
        self.assertEqual(2, len(merged["sessions"]))
        self.assertEqual("s1", merged["sessions"][0]["server_id"])
        self.assertEqual("Server 1", merged["sessions"][0]["server_name"])


class MergeHistoryTests(unittest.TestCase):
    def test_merge_history_sorts_by_canonical_utc_epoch_desc(self) -> None:
        results = [
            HistoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                rows=[
                    {"title": "Older", "started": "100"},
                    {"title": "Newest", "started": "300"},
                ],
            ),
            HistoryFetchResult(
                server_id="s2",
                server_name="Server 2",
                status="ok",
                rows=[{"title": "Middle", "date": "200"}],
            ),
        ]

        merged = merge_history(results, start=0, length=10)
        titles = [row["title"] for row in merged["rows"]]
        self.assertEqual(["Newest", "Middle", "Older"], titles)

    def test_merge_history_sorts_server_statuses_by_name(self) -> None:
        results = [
            HistoryFetchResult(
                server_id="z",
                server_name="Zulu",
                status="ok",
                rows=[],
            ),
            HistoryFetchResult(
                server_id="a",
                server_name="Alpha",
                status="timeout",
                rows=[],
            ),
        ]

        merged = merge_history(results, start=0, length=10)
        names = [s["server_name"] for s in merged["server_statuses"]]
        self.assertEqual(["Alpha", "Zulu"], names)

    def test_merge_history_uses_iso_datetime_fallback(self) -> None:
        results = [
            HistoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                rows=[{"title": "ISO", "started_at": "2024-01-01T00:00:00Z"}],
            )
        ]

        merged = merge_history(results, start=0, length=10)
        self.assertGreater(merged["rows"][0]["canonical_utc_epoch"], 0)

    def test_merge_history_applies_global_pagination(self) -> None:
        results = [
            HistoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                rows=[
                    {"title": "t1", "started": "5"},
                    {"title": "t2", "started": "4"},
                    {"title": "t3", "started": "3"},
                    {"title": "t4", "started": "2"},
                ],
            )
        ]

        merged = merge_history(results, start=1, length=2)
        titles = [row["title"] for row in merged["rows"]]
        self.assertEqual(["t2", "t3"], titles)


class UnwatchedReportTests(unittest.TestCase):
    def test_builds_cumulative_and_per_server_unwatched(self) -> None:
        rows = [
            {
                "server_id": "s1",
                "server_name": "Server 1",
                "media_type": "episode",
                "grandparent_title": "Show A",
                "title": "Ep1",
                "parent_media_index": 1,
                "media_index": 1,
                "canonical_utc_epoch": 100,
            },
            {
                "server_id": "s2",
                "server_name": "Server 2",
                "media_type": "episode",
                "grandparent_title": "Show A",
                "title": "Ep1",
                "parent_media_index": 1,
                "media_index": 1,
                "canonical_utc_epoch": 200,
            },
            {
                "server_id": "s1",
                "server_name": "Server 1",
                "media_type": "episode",
                "grandparent_title": "Show B",
                "title": "Ep5",
                "parent_media_index": 1,
                "media_index": 5,
                "canonical_utc_epoch": 50,
            },
        ]
        report = build_unwatched_media_report(
            rows=rows,
            cutoff_epoch=150,
            media_type="episode",
            max_items=50,
        )

        self.assertEqual(2, report["indexed_item_count"])
        # Show B is stale across all servers.
        self.assertEqual(1, len(report["cumulative_unwatched"]))
        self.assertIn("Show B", report["cumulative_unwatched"][0]["display_title"])
        # Server 1 has stale entries; server 2 does not.
        self.assertEqual(1, len(report["per_server_unwatched"]))
        self.assertEqual("s1", report["per_server_unwatched"][0]["server_id"])


class LibraryUnwatchedReportTests(unittest.TestCase):
    def test_watched_when_series_title_differs_but_episode_rating_key_matches(self) -> None:
        """History often uses a longer grandparent_title than Plex inventory (e.g. anime)."""
        inventory = [
            InventoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                shows=[{"rating_key": "show1", "title": "Initial D"}],
                seasons=[],
                episodes=[
                    {
                        "rating_key": "ep99",
                        "title": "The Ultimate Tofu Store Showdown",
                        "grandparent_title": "Initial D",
                        "parent_media_index": 1,
                        "media_index": 1,
                        "server_show_rating_key": "show1",
                        "server_season_rating_key": "season1",
                    },
                ],
            )
        ]
        history_rows = [
            {
                "server_id": "s1",
                "media_type": "episode",
                "rating_key": "ep99",
                "grandparent_title": "Initial D: First Stage",
                "parent_media_index": 1,
                "media_index": 1,
                "canonical_utc_epoch": 150,
            }
        ]
        report = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=history_rows,
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )
        self.assertEqual(0, len(report["cumulative_unwatched"]["episodes"]))
        self.assertEqual(0, len(report["cumulative_unwatched"]["seasons"]))
        self.assertEqual(0, len(report["cumulative_unwatched"]["shows"]))
        self.assertEqual(0, len(report["per_server"][0]["unwatched"]["episodes"]))

    def test_library_unwatched_report_classifies_by_index_window(self) -> None:
        inventory = [
            InventoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                shows=[{"rating_key": "show1", "title": "Show 1"}],
                seasons=[{"rating_key": "season1", "title": "Season 1", "parent_title": "Show 1"}],
                episodes=[
                    {
                        "rating_key": "ep1",
                        "title": "Episode 1",
                        "grandparent_title": "Show 1",
                        "parent_media_index": 1,
                        "media_index": 1,
                        "server_show_rating_key": "show1",
                        "server_season_rating_key": "season1",
                    },
                    {
                        "rating_key": "ep2",
                        "title": "Episode 2",
                        "grandparent_title": "Show 1",
                        "parent_media_index": 1,
                        "media_index": 2,
                        "server_show_rating_key": "show1",
                        "server_season_rating_key": "season1",
                    },
                ],
            )
        ]
        history_rows = [
            {
                "server_id": "s1",
                "media_type": "episode",
                "rating_key": "ep1",
                "grandparent_title": "Show 1",
                "parent_media_index": 1,
                "media_index": 1,
                "canonical_utc_epoch": 150,
            }
        ]

        report = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=history_rows,
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )

        self.assertEqual(1, len(report["cumulative_unwatched"]["episodes"]))
        self.assertIn("Episode 2", report["cumulative_unwatched"]["episodes"][0]["title"])
        self.assertEqual(0, len(report["cumulative_unwatched"]["shows"]))
        self.assertEqual(0, len(report["cumulative_unwatched"]["seasons"]))
        self.assertFalse(report["per_server"][0]["index_complete"])

    def test_cumulative_season_is_unique_and_excluded_if_watched_anywhere(self) -> None:
        inventory = [
            InventoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                episodes=[
                    {
                        "rating_key": "s1e1",
                        "title": "Ep1",
                        "grandparent_title": "Shared Show",
                        "parent_media_index": 1,
                        "media_index": 1,
                    }
                ],
            ),
            InventoryFetchResult(
                server_id="s2",
                server_name="Server 2",
                status="ok",
                episodes=[
                    {
                        "rating_key": "s2e1",
                        "title": "Ep1",
                        "grandparent_title": "Shared Show",
                        "parent_media_index": 1,
                        "media_index": 1,
                    }
                ],
            ),
        ]

        # Watched on one server should exclude globally.
        watched_history = [
            {
                "server_id": "s2",
                "media_type": "episode",
                "rating_key": "random-other-key",
                "grandparent_title": "Shared Show",
                "parent_media_index": 1,
                "media_index": 1,
                "canonical_utc_epoch": 150,
            }
        ]
        report_watched = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=watched_history,
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )
        self.assertEqual(0, len(report_watched["cumulative_unwatched"]["seasons"]))

        # No watch anywhere should return one cumulative season (deduped).
        report_unwatched = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=[],
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )
        self.assertEqual(1, len(report_unwatched["cumulative_unwatched"]["seasons"]))

    def test_season_excluded_when_history_has_sxe_in_full_title(self) -> None:
        inventory = [
            InventoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                episodes=[
                    {
                        "rating_key": "e1",
                        "title": "Boys of Summer",
                        "grandparent_title": "The Wire",
                        "parent_media_index": 4,
                        "media_index": 1,
                    }
                ],
            )
        ]
        history_rows = [
            {
                "server_id": "s1",
                "media_type": "episode",
                "rating_key": "unknown",
                "full_title": "The Wire - Boys of Summer (S4 · E1)",
                "canonical_utc_epoch": 150,
            }
        ]
        report = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=history_rows,
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )
        self.assertEqual(0, len(report["cumulative_unwatched"]["seasons"]))

    def test_show_excluded_if_any_episode_has_ever_watch_metadata(self) -> None:
        inventory = [
            InventoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                episodes=[
                    {
                        "rating_key": "ep1",
                        "title": "Episode 1",
                        "grandparent_title": "Show With History",
                        "parent_media_index": 1,
                        "media_index": 1,
                        "play_count": 1,
                    },
                    {
                        "rating_key": "ep2",
                        "title": "Episode 2",
                        "grandparent_title": "Show With History",
                        "parent_media_index": 1,
                        "media_index": 2,
                    },
                ],
            )
        ]
        report = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=[],
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )
        self.assertEqual(0, len(report["cumulative_unwatched"]["shows"]))
        self.assertEqual(0, len(report["per_server"][0]["unwatched"]["shows"]))

    def test_show_excluded_if_show_row_has_play_count(self) -> None:
        inventory = [
            InventoryFetchResult(
                server_id="s1",
                server_name="Server 1",
                status="ok",
                shows=[{"rating_key": "show1", "title": "Show Row Watched", "play_count": 2}],
                episodes=[
                    {
                        "rating_key": "ep1",
                        "title": "Episode 1",
                        "grandparent_title": "Show Row Watched",
                        "parent_media_index": 1,
                        "media_index": 1,
                    }
                ],
            )
        ]
        report = build_library_unwatched_tv_report(
            inventory_results=inventory,
            history_rows=[],
            index_start_epoch=100,
            index_end_epoch=200,
            max_items=200,
        )
        self.assertEqual(0, len(report["cumulative_unwatched"]["shows"]))


class TvdbGuidTests(unittest.TestCase):
    def test_parses_thetvdb_guid(self) -> None:
        self.assertEqual(
            121361,
            tvdb_id_from_guid("com.plexapp.agents.thetvdb://121361/6/1?lang=en"),
        )

    def test_returns_none_without_thetvdb(self) -> None:
        self.assertIsNone(tvdb_id_from_guid(None))
        self.assertIsNone(tvdb_id_from_guid("imdb://tt0944947"))


class SonarrResolveSeriesTests(unittest.TestCase):
    def test_resolve_prefers_tvdb_id(self) -> None:
        rows = [{"id": 1, "tvdbId": 99, "title": "Alpha"}, {"id": 2, "tvdbId": 100, "title": "Beta"}]
        found = resolve_series(rows, 100, None)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(2, found["id"])

    def test_resolve_falls_back_to_title(self) -> None:
        rows = [{"id": 3, "tvdbId": 1, "title": "Gamma Show", "cleanTitle": "gammashow"}]
        found = resolve_series(rows, None, "Gamma Show")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(3, found["id"])


if __name__ == "__main__":
    unittest.main()
