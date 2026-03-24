"""Unit tests for stale-library watch indexing."""

import unittest

from inspectarr.stale_library_service import (
    build_watch_index_from_history,
    _lookup_key_variants,
    _series_lookup_key,
)


class StaleLibraryWatchIndexTests(unittest.TestCase):
    def test_series_lookup_key_prefers_tvdb(self) -> None:
        self.assertEqual(_series_lookup_key(123, "Ignore Me"), "tvdb:123")

    def test_watch_index_marks_series_and_season(self) -> None:
        rows = [
            {
                "media_type": "episode",
                "canonical_utc_epoch": 1_700_000_000,
                "grandparent_title": "Alpha Show",
                "parent_media_index": 2,
                "grandparent_guid": "com.plexapp.agents.thetvdb://12345?lang=en",
            }
        ]
        sw, ssw = build_watch_index_from_history(rows, 1_600_000_000)
        self.assertIn("tvdb:12345", sw)
        self.assertIn(("tvdb:12345", 2), ssw)

    def test_old_plays_ignored(self) -> None:
        rows = [
            {
                "media_type": "episode",
                "canonical_utc_epoch": 1_000_000_000,
                "grandparent_title": "Old",
                "parent_media_index": 1,
            }
        ]
        sw, ssw = build_watch_index_from_history(rows, 1_600_000_000)
        self.assertEqual(0, len(sw))
        self.assertEqual(0, len(ssw))

    def test_cutoff_zero_includes_all_epochs(self) -> None:
        rows = [
            {
                "media_type": "episode",
                "canonical_utc_epoch": 100,
                "grandparent_title": "Ancient",
                "parent_media_index": 0,
            }
        ]
        sw, ssw = build_watch_index_from_history(rows, 0)
        self.assertIn("t:ancient", sw)
        self.assertIn(("t:ancient", 0), ssw)

    def test_colon_title_base_matches_sonarr_short_title(self) -> None:
        """Plex-style ``Show: Subtitle`` in history vs Sonarr ``Show`` (e.g. Initial D)."""
        rows = [
            {
                "media_type": "episode",
                "canonical_utc_epoch": 1_700_000_000,
                "grandparent_title": "Initial D: First Stage",
                "parent_media_index": 1,
            }
        ]
        sw, ssw = build_watch_index_from_history(rows, 0)
        self.assertIn("t:initial d: first stage", sw)
        self.assertIn("t:initial d", sw)
        self.assertIn(("t:initial d", 1), ssw)
        sonarr_keys = _lookup_key_variants(73903, "Initial D")
        self.assertTrue(any(k in sw for k in sonarr_keys))
        self.assertTrue(any((k, 1) in ssw for k in sonarr_keys))


if __name__ == "__main__":
    unittest.main()
