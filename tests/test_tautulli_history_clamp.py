"""Tautulli get_history request length clamping."""

import unittest

from scoparr.tautulli_client import (
    TAUTULLI_GET_HISTORY_MAX_ROWS_PER_REQUEST,
    _clamp_get_history_length,
)


class TautulliHistoryClampTests(unittest.TestCase):
    def test_clamp_caps_at_max(self) -> None:
        self.assertEqual(_clamp_get_history_length(500_000), TAUTULLI_GET_HISTORY_MAX_ROWS_PER_REQUEST)
        self.assertEqual(
            _clamp_get_history_length(TAUTULLI_GET_HISTORY_MAX_ROWS_PER_REQUEST),
            TAUTULLI_GET_HISTORY_MAX_ROWS_PER_REQUEST,
        )
        self.assertEqual(_clamp_get_history_length(50_000), 50_000)

    def test_clamp_minimum_one(self) -> None:
        self.assertEqual(_clamp_get_history_length(0), 1)
        self.assertEqual(_clamp_get_history_length(-5), 1)


if __name__ == "__main__":
    unittest.main()
