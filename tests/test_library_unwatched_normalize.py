"""Regression tests for library-unwatched report normalization."""

import unittest

from tautulli_inspector.routes_dashboard import (
    _library_unwatched_server_card_rows,
    _normalize_library_unwatched_report,
)
from tautulli_inspector.settings import TautulliServer


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


class LibraryUnwatchedServerCardsTests(unittest.TestCase):
    def test_loading_fills_one_card_per_configured_server(self) -> None:
        class _Cfg:
            tautulli_servers = [
                TautulliServer(id="a", name="Alpha", base_url="http://x", api_key="k"),
                TautulliServer(id="b", name="Bravo", base_url="http://y", api_key="k"),
            ]

        cards = _library_unwatched_server_card_rows(_Cfg(), {"per_server": []}, loading=True)
        self.assertEqual(2, len(cards))
        self.assertEqual("indexing", cards[0]["status"])
        self.assertEqual("Alpha", cards[0]["server_name"])
        self.assertEqual("b", cards[1]["server_id"])

    def test_merges_report_rows_with_config(self) -> None:
        class _Cfg:
            tautulli_servers = [
                TautulliServer(id="a", name="Alpha", base_url="http://x", api_key="k"),
            ]

        settings = _Cfg()
        report = {
            "per_server": [
                {
                    "server_id": "a",
                    "server_name": "Alpha",
                    "status": "ok",
                    "index_complete": True,
                    "inventory_counts": {"shows": 3, "seasons": 0, "episodes": 0},
                }
            ]
        }
        cards = _library_unwatched_server_card_rows(settings, report, loading=False)
        self.assertEqual(1, len(cards))
        self.assertEqual("ok", cards[0]["status"])
        self.assertEqual(3, cards[0]["inventory_counts"]["shows"])


if __name__ == "__main__":
    unittest.main()
