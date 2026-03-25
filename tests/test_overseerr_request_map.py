"""Overseerr TV request aggregation for Cold Storage."""

import unittest
from datetime import datetime, timezone

from scoparr.overseerr_client import _accumulate_tv_request_row, finalize_overseerr_tv_entry


class OverseerrRequestMapTests(unittest.TestCase):
    def test_accumulate_merges_earliest_request_and_names_tvdb(self) -> None:
        acc_tvdb: dict[int, dict] = {}
        acc_tmdb: dict[int, dict] = {}
        _accumulate_tv_request_row(
            acc_tvdb,
            acc_tmdb,
            {
                "type": "tv",
                "createdAt": "2024-06-01T12:00:00.000Z",
                "requestedBy": {"displayName": "Alice"},
                "media": {
                    "mediaType": "tv",
                    "tvdbId": 42,
                    "tmdbId": 100,
                    "mediaAddedAt": "2024-06-15T08:00:00.000Z",
                },
            },
        )
        _accumulate_tv_request_row(
            acc_tvdb,
            acc_tmdb,
            {
                "type": "tv",
                "createdAt": "2023-01-01T00:00:00.000Z",
                "requestedBy": {"username": "bob"},
                "media": {"mediaType": "tv", "tvdbId": 42, "tmdbId": 100},
            },
        )
        self.assertEqual(list(acc_tvdb.keys()), [42])
        self.assertEqual(list(acc_tmdb.keys()), [100])
        fin = finalize_overseerr_tv_entry(acc_tvdb[42])
        self.assertEqual(
            fin["requested_at_epoch"],
            int(datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
        )
        self.assertEqual(fin["requested_by"], "Alice, bob")
        self.assertIsNotNone(fin["library_available_at_epoch"])

    def test_tmdb_only_populates_tmdb_map(self) -> None:
        acc_tvdb: dict[int, dict] = {}
        acc_tmdb: dict[int, dict] = {}
        _accumulate_tv_request_row(
            acc_tvdb,
            acc_tmdb,
            {
                "type": "tv",
                "createdAt": "2024-01-01T00:00:00.000Z",
                "requestedBy": {"displayName": "Zed"},
                "media": {"mediaType": "tv", "tmdbId": 555},
            },
        )
        self.assertEqual(acc_tvdb, {})
        self.assertIn(555, acc_tmdb)
        self.assertEqual(finalize_overseerr_tv_entry(acc_tmdb[555])["requested_by"], "Zed")

    def test_snake_case_media_fields(self) -> None:
        acc_tvdb: dict[int, dict] = {}
        acc_tmdb: dict[int, dict] = {}
        _accumulate_tv_request_row(
            acc_tvdb,
            acc_tmdb,
            {
                "type": "tv",
                "created_at": "2024-02-01T00:00:00.000Z",
                "media": {"media_type": "tv", "tvdb_id": 77},
            },
        )
        self.assertIn(77, acc_tvdb)

    def test_skips_movies(self) -> None:
        acc_tvdb: dict[int, dict] = {}
        acc_tmdb: dict[int, dict] = {}
        _accumulate_tv_request_row(
            acc_tvdb,
            acc_tmdb,
            {
                "type": "movie",
                "media": {"mediaType": "movie", "tvdbId": 99, "tmdbId": 1},
            },
        )
        self.assertEqual(acc_tvdb, {})
        self.assertEqual(acc_tmdb, {})


if __name__ == "__main__":
    unittest.main()
