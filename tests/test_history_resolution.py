"""UHD / 4K detection on Tautulli-style history rows."""

import unittest

from scoparr.history_resolution import history_row_is_uhd_playback


class HistoryResolutionTests(unittest.TestCase):
    def test_height_2160(self) -> None:
        self.assertTrue(history_row_is_uhd_playback({"video_height": "2160"}))

    def test_width_3840(self) -> None:
        self.assertTrue(history_row_is_uhd_playback({"video_width": "3840"}))

    def test_1080_not_uhd(self) -> None:
        self.assertFalse(history_row_is_uhd_playback({"video_height": "1080", "video_resolution": "1080"}))

    def test_resolution_string_4k(self) -> None:
        self.assertTrue(history_row_is_uhd_playback({"video_full_resolution": "4K"}))

    def test_resolution_string_2160p(self) -> None:
        self.assertTrue(history_row_is_uhd_playback({"video_resolution": "2160"}))

    def test_empty_row(self) -> None:
        self.assertFalse(history_row_is_uhd_playback({}))


if __name__ == "__main__":
    unittest.main()
