"""Stale-movies upstream trace (Tautulli / Radarr progress)."""

import unittest

from scoparr.stale_movies_upstream import (
    begin_stale_movies_upstream_trace,
    end_stale_movies_upstream_trace,
    record_stale_movies_radarr,
    set_stale_movies_radarr_movie_list_count,
    stale_movies_upstream_snapshot,
)


class StaleMoviesUpstreamTests(unittest.TestCase):
    def tearDown(self) -> None:
        end_stale_movies_upstream_trace()

    def test_radarr_movie_list_count_in_snapshot(self) -> None:
        begin_stale_movies_upstream_trace()
        record_stale_movies_radarr("GET /api/v3/movie", 200, True)
        set_stale_movies_radarr_movie_list_count(42)
        s = stale_movies_upstream_snapshot()
        self.assertTrue(s.get("busy"))
        rd = s.get("radarr") or {}
        self.assertEqual(rd.get("movie_list_count"), 42)
        self.assertEqual(rd.get("last", {}).get("http_status"), 200)


if __name__ == "__main__":
    unittest.main()
