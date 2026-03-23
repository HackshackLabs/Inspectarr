"""Regression tests for library-unwatched report normalization."""

import unittest

from inspectarr.routes_dashboard import (
    _library_unwatched_server_card_rows,
    _normalize_library_unwatched_report,
)
from inspectarr.settings import TautulliServer


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
        self.assertEqual("pending", cards[0]["activity_status"])
        self.assertEqual(0, cards[0]["activity_stream_count"])

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
        cards = _library_unwatched_server_card_rows(
            settings,
            report,
            loading=False,
            activity_server_statuses=[
                {
                    "server_id": "a",
                    "server_name": "Alpha",
                    "status": "ok",
                    "stream_count": 2,
                    "error": None,
                }
            ],
        )
        self.assertEqual(1, len(cards))
        self.assertEqual("ok", cards[0]["status"])
        self.assertEqual(3, cards[0]["inventory_counts"]["shows"])
        self.assertEqual("ok", cards[0]["activity_status"])
        self.assertEqual(2, cards[0]["activity_stream_count"])
        self.assertIsNone(cards[0]["activity_error"])

    def test_merges_live_activity_fields_when_api_degraded(self) -> None:
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
                    "inventory_counts": {"shows": 1, "seasons": 0, "episodes": 0},
                }
            ]
        }
        cards = _library_unwatched_server_card_rows(
            settings,
            report,
            loading=False,
            activity_server_statuses=[
                {
                    "server_id": "a",
                    "server_name": "Alpha",
                    "status": "timeout",
                    "stream_count": 0,
                    "error": "Timed out",
                }
            ],
        )
        self.assertEqual("timeout", cards[0]["activity_status"])
        self.assertEqual("Timed out", cards[0]["activity_error"])

    def test_server_cards_sorted_by_display_name(self) -> None:
        class _Cfg:
            tautulli_servers = [
                TautulliServer(id="z", name="Zed", base_url="http://z", api_key="k"),
                TautulliServer(id="a", name="Alpha", base_url="http://a", api_key="k"),
            ]

        cards = _library_unwatched_server_card_rows(_Cfg(), {"per_server": []}, loading=True)
        self.assertEqual(["Alpha", "Zed"], [c["server_name"] for c in cards])


if __name__ == "__main__":
    unittest.main()
