"""Unit tests for library-unwatched inventory index loop helpers."""

import unittest

from inspectarr.models import InventoryFetchResult
from inspectarr.routes_dashboard import _library_unwatched_should_stop_inventory_loop
from inspectarr.settings import TautulliServer


def _srv() -> TautulliServer:
    return TautulliServer(id="s1", name="One", base_url="http://127.0.0.1:8181", api_key="k")


class LibraryUnwatchedInventoryLoopTests(unittest.TestCase):
    def test_stops_on_internal_unknown_result(self) -> None:
        stop, reason = _library_unwatched_should_stop_inventory_loop(
            [
                InventoryFetchResult(
                    server_id="unknown",
                    server_name="Unknown",
                    status="internal_error",
                )
            ],
            [_srv()],
        )
        self.assertTrue(stop)
        self.assertEqual(reason, "internal_error")

    def test_stops_when_all_servers_index_complete(self) -> None:
        stop, reason = _library_unwatched_should_stop_inventory_loop(
            [
                InventoryFetchResult(
                    server_id="s1",
                    server_name="One",
                    status="ok",
                    index_complete=True,
                )
            ],
            [_srv()],
        )
        self.assertTrue(stop)
        self.assertEqual(reason, "complete")

    def test_continues_when_index_incomplete(self) -> None:
        stop, reason = _library_unwatched_should_stop_inventory_loop(
            [
                InventoryFetchResult(
                    server_id="s1",
                    server_name="One",
                    status="ok",
                    index_complete=False,
                )
            ],
            [_srv()],
        )
        self.assertFalse(stop)
        self.assertEqual(reason, "continue")

    def test_stops_on_server_error_status(self) -> None:
        stop, reason = _library_unwatched_should_stop_inventory_loop(
            [
                InventoryFetchResult(
                    server_id="s1",
                    server_name="One",
                    status="timeout",
                    index_complete=False,
                )
            ],
            [_srv()],
        )
        self.assertTrue(stop)
        self.assertTrue(reason.startswith("server_status:"))

