"""Unit tests for stale-library watch indexing."""

import unittest
from datetime import datetime, timezone

from inspectarr.stale_library_service import (
    NEVER_PLAYED_MIN_AGE_SECONDS,
    build_last_watch_index_from_history,
    build_watch_index_from_history,
    pick_last_tautulli_play_for_series,
    series_added_epoch_utc,
    season_is_stale_cold_storage,
    _lookup_key_variants,
    _normalize_title_for_stale_match,
    _series_lookup_key,
)


class StaleLibraryRebuildCriteriaTests(unittest.TestCase):
    def test_season_stale_when_played_before_lookback(self) -> None:
        self.assertTrue(
            season_is_stale_cold_storage(
                watched_in_lookback=False,
                watched_ever=True,
                series_added_epoch=1,
                now_epoch=2_000_000_000,
            )
        )

    def test_season_not_stale_when_watched_in_lookback(self) -> None:
        self.assertFalse(
            season_is_stale_cold_storage(
                watched_in_lookback=True,
                watched_ever=True,
                series_added_epoch=1,
                now_epoch=2_000_000_000,
            )
        )

    def test_never_played_excluded_until_series_old_enough(self) -> None:
        now = 2_000_000_000
        added = now - NEVER_PLAYED_MIN_AGE_SECONDS + 1
        self.assertFalse(
            season_is_stale_cold_storage(
                watched_in_lookback=False,
                watched_ever=False,
                series_added_epoch=added,
                now_epoch=now,
            )
        )
        added_ok = now - NEVER_PLAYED_MIN_AGE_SECONDS
        self.assertTrue(
            season_is_stale_cold_storage(
                watched_in_lookback=False,
                watched_ever=False,
                series_added_epoch=added_ok,
                now_epoch=now,
            )
        )

    def test_series_added_epoch_parses_sonarr_iso(self) -> None:
        self.assertEqual(
            series_added_epoch_utc({"added": "2020-01-02T03:04:05Z"}),
            int(datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()),
        )


class StaleLibraryLastPlayTests(unittest.TestCase):
    def test_last_watch_picks_newest_per_key_and_resolves_sonarr_keys(self) -> None:
        rows = [
            {
                "media_type": "episode",
                "canonical_utc_epoch": 1_600_000_000,
                "grandparent_title": "Alpha Show",
                "parent_media_index": 1,
                "media_index": 2,
                "title": "Pilot",
                "friendly_name": "Alice",
                "server_id": "a",
                "server_name": "T1",
                "grandparent_guid": "com.plexapp.agents.thetvdb://999?lang=en",
            },
            {
                "media_type": "episode",
                "canonical_utc_epoch": 1_700_000_000,
                "grandparent_title": "Alpha Show",
                "parent_media_index": 2,
                "media_index": 1,
                "title": "Later",
                "friendly_name": "Bob",
                "server_id": "b",
                "server_name": "T2",
                "grandparent_guid": "com.plexapp.agents.thetvdb://999?lang=en",
            },
        ]
        idx = build_last_watch_index_from_history(rows)
        self.assertIn("tvdb:999", idx)
        self.assertEqual(idx["tvdb:999"]["user"], "Bob")
        self.assertEqual(idx["tvdb:999"]["season_number"], 2)
        self.assertEqual(idx["tvdb:999"]["episode_number"], 1)
        sonarr_keys = _lookup_key_variants(999, "Alpha Show")
        picked = pick_last_tautulli_play_for_series(idx, sonarr_keys)
        assert picked is not None
        self.assertEqual(picked["played_at_epoch"], 1_700_000_000)
        self.assertIn("S2E1", picked["episode_label"])


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

    def test_title_variants_intersect_for_punctuation_mismatch(self) -> None:
        """Sonarr ``American Dad!`` vs Plex-style ``American Dad`` (no TVDB on one side)."""
        h = _lookup_key_variants(None, "American Dad")
        s = _lookup_key_variants(None, "American Dad!")
        self.assertTrue(h & s, msg=f"h={h!r} s={s!r}")

    def test_year_suffix_removed_for_match(self) -> None:
        h = _lookup_key_variants(None, "Black Sails (2014)")
        s = _lookup_key_variants(None, "Black Sails")
        self.assertTrue(h & s)

    def test_normalize_title_folds_punctuation(self) -> None:
        self.assertEqual(_normalize_title_for_stale_match("American Dad!"), "american dad")
        self.assertEqual(_normalize_title_for_stale_match("Black Sails (2014)"), "black sails")

    def test_watch_index_uses_show_name_fallback(self) -> None:
        rows = [
            {
                "media_type": "episode",
                "canonical_utc_epoch": 1_700_000_000,
                "show_name": "Fallback Show",
                "parent_media_index": 1,
            }
        ]
        sw, ssw = build_watch_index_from_history(rows, 0)
        self.assertIn("t:fallback show", sw)


if __name__ == "__main__":
    unittest.main()
