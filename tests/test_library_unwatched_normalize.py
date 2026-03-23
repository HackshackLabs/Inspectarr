"""Regression tests for library-unwatched report normalization."""

import unittest

from tautulli_inspector.routes_dashboard import _normalize_library_unwatched_report


class LibraryUnwatchedNormalizeTests(unittest.TestCase):
    def test_fills_missing_status_and_inventory_counts(self) -> None:
        report = {
            "per_server": [
                {"server_id": "a", "server_name": "A", "status": ""},
                {"server_id": "b", "server_name": "B", "status": None},
                {
                    "server_id": "c",
                    "server_name": "C",
                    "status": "ok",
                    "inventory_counts": {"shows": 3},
                },
            ]
        }
        _normalize_library_unwatched_report(report)
        servers = report["per_server"]
        self.assertEqual(servers[0]["status"], "unknown")
        self.assertEqual(servers[1]["status"], "unknown")
        self.assertEqual(servers[2]["status"], "ok")
        self.assertEqual(servers[0]["inventory_counts"]["shows"], 0)
        self.assertEqual(servers[2]["inventory_counts"]["shows"], 3)
        self.assertEqual(servers[2]["inventory_counts"]["seasons"], 0)
        self.assertEqual(servers[2]["inventory_counts"]["episodes"], 0)


if __name__ == "__main__":
    unittest.main()
