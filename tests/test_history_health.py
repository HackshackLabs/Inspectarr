"""History server card enrichment (last OK fetch)."""

import unittest

from tautulli_inspector.history_health import enrich_history_server_statuses, format_last_ok_display


class HistoryHealthTests(unittest.TestCase):
    def test_ok_updates_timestamp(self) -> None:
        rows = enrich_history_server_statuses(
            [{"server_id": "a", "status": "ok", "history_count": 1}],
            snapshot_epoch=1700000000,
        )
        self.assertEqual(1700000000, rows[0]["last_ok_at_epoch"])
        self.assertTrue(rows[0]["last_ok_at_display"].endswith("UTC"))

    def test_degraded_keeps_previous_ok(self) -> None:
        enrich_history_server_statuses([{"server_id": "b", "status": "ok", "history_count": 1}], 100)
        rows = enrich_history_server_statuses(
            [{"server_id": "b", "status": "timeout", "history_count": 0}],
            snapshot_epoch=200,
        )
        self.assertEqual(100, rows[0]["last_ok_at_epoch"])
        self.assertEqual("timeout", rows[0]["status"])

    def test_format_never(self) -> None:
        self.assertIn("no successful", format_last_ok_display(None).lower())


if __name__ == "__main__":
    unittest.main()
