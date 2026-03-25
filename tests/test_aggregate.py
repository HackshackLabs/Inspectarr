"""Aggregation and normalization tests."""

import unittest

from inspectarr.aggregate import merge_activity, merge_history, tvdb_id_from_guid
from inspectarr.sonarr_client import resolve_series
from inspectarr.models import ActivityFetchResult, HistoryFetchResult


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

    def test_resolve_matches_sonarr_alternate_title(self) -> None:
        rows = [
            {
                "id": 4,
                "tvdbId": 2,
                "title": ".hack",
                "cleanTitle": "hack",
                "alternateTitles": [{"title": ".hack//SIGN"}],
            }
        ]
        found = resolve_series(rows, None, ".hack//SIGN")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(4, found["id"])

    def test_resolve_stem_before_double_slash_matches_shorter_sonarr_title(self) -> None:
        rows = [{"id": 5, "tvdbId": 3, "title": ".hack", "cleanTitle": "hack"}]
        found = resolve_series(rows, None, ".hack//SIGN")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(5, found["id"])


if __name__ == "__main__":
    unittest.main()
