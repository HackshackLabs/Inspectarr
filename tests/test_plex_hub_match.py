"""Plex hub search show matching (unit, no network)."""

import unittest

from inspectarr.plex_client import _pick_show_rating_key


class PlexHubMatchTests(unittest.TestCase):
    def test_prefers_tvdb_guid(self) -> None:
        cands = [
            {"ratingKey": "1", "title": "Wrong", "guid": "com.plexapp.agents.imdb://xx"},
            {"ratingKey": "99", "title": "Right", "guid": "com.plexapp.agents.thetvdb://12345?lang=en"},
        ]
        rk = _pick_show_rating_key(cands, tvdb_id=12345, title="Anything")
        self.assertEqual(rk, "99")

    def test_title_exact_fallback(self) -> None:
        cands = [
            {"ratingKey": "7", "title": "My Show Name", "guid": ""},
        ]
        rk = _pick_show_rating_key(cands, tvdb_id=None, title="My Show Name")
        self.assertEqual(rk, "7")


if __name__ == "__main__":
    unittest.main()
