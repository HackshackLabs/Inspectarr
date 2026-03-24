"""Cold Storage upstream trace snapshot."""

import unittest

from inspectarr.stale_library_upstream import (
    begin_stale_library_upstream_trace,
    bump_stale_library_tautulli_history_rows,
    end_stale_library_upstream_trace,
    record_stale_library_tautulli,
    stale_library_upstream_snapshot,
)


class StaleLibraryUpstreamTests(unittest.TestCase):
    def tearDown(self) -> None:
        end_stale_library_upstream_trace()

    def test_snapshot_idle_shape(self) -> None:
        end_stale_library_upstream_trace()
        s = stale_library_upstream_snapshot()
        self.assertFalse(s["busy"])
        self.assertIn("tautulli_servers", s)
        self.assertEqual(s["tautulli_servers"], [])
        self.assertIn("sonarr", s)

    def test_busy_records_tautulli(self) -> None:
        begin_stale_library_upstream_trace()
        record_stale_library_tautulli("srv1", "Alpha", "get_history", 200, True)
        s = stale_library_upstream_snapshot()
        self.assertTrue(s["busy"])
        self.assertEqual(len(s["tautulli_servers"]), 1)
        row = s["tautulli_servers"][0]
        self.assertEqual(row["server_id"], "srv1")
        self.assertEqual(row["last_cmd"], "get_history")
        self.assertEqual(row["last_http_status"], 200)
        self.assertTrue(row["last_ok"])

    def test_history_rows_accumulate(self) -> None:
        begin_stale_library_upstream_trace()
        record_stale_library_tautulli("srv1", "Alpha", "get_history", 200, True)
        bump_stale_library_tautulli_history_rows("srv1", "Alpha", 50)
        bump_stale_library_tautulli_history_rows("srv1", "Alpha", 30)
        s = stale_library_upstream_snapshot()
        row = s["tautulli_servers"][0]
        self.assertEqual(row["history_rows_accumulated"], 80)
        self.assertEqual(row["last_history_page_rows"], 30)

    def test_zero_row_page_updates_last_not_total(self) -> None:
        begin_stale_library_upstream_trace()
        bump_stale_library_tautulli_history_rows("srv1", "Alpha", 0)
        s = stale_library_upstream_snapshot()
        row = s["tautulli_servers"][0]
        self.assertEqual(row["last_history_page_rows"], 0)
        self.assertEqual(row["history_rows_accumulated"], 0)

    def test_placeholders_seed_servers(self) -> None:
        begin_stale_library_upstream_trace([("a", "Server A"), ("b", "Server B")])
        s = stale_library_upstream_snapshot()
        self.assertEqual(len(s["tautulli_servers"]), 2)
        ids = {r["server_id"] for r in s["tautulli_servers"]}
        self.assertEqual(ids, {"a", "b"})
        for r in s["tautulli_servers"]:
            self.assertEqual(r["history_rows_accumulated"], 0)
            self.assertIsNone(r["last_history_page_rows"])


if __name__ == "__main__":
    unittest.main()
