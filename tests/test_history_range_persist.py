"""Broadside Range persisted time-range (cookie + redirect)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from starlette.testclient import TestClient

from scoparr.routes_dashboard import BROADSIDE_RANGE_MODE_COOKIE
from scoparr.settings import _settings_from_env


def _clear_settings_cache() -> None:
    _settings_from_env.cache_clear()


class HistoryRangePersistTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_settings_cache()

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "false",
            "TAUTULLI_SERVERS_JSON": "[]",
            "HISTORY_CACHE_DB_PATH": "",
        },
        clear=False,
    )
    def test_bare_history_redirects_default_week(self) -> None:
        _clear_settings_cache()
        from scoparr.main import create_app

        client = TestClient(create_app())
        r = client.get("/history", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        loc = r.headers.get("location", "")
        self.assertIn("range_mode=week", loc)

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "false",
            "TAUTULLI_SERVERS_JSON": "[]",
            "HISTORY_CACHE_DB_PATH": "",
        },
        clear=False,
    )
    def test_bare_history_redirects_from_cookie_all(self) -> None:
        _clear_settings_cache()
        from scoparr.main import create_app

        client = TestClient(create_app(), cookies={BROADSIDE_RANGE_MODE_COOKIE: "all"})
        r = client.get("/history", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("range_mode=all", r.headers.get("location", ""))

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "false",
            "TAUTULLI_SERVERS_JSON": "[]",
            "HISTORY_CACHE_DB_PATH": "",
        },
        clear=False,
    )
    def test_explicit_range_mode_sets_cookie_then_bare_url_redirects_same(self) -> None:
        _clear_settings_cache()
        from scoparr.main import create_app

        client = TestClient(create_app())
        r1 = client.get("/history?range_mode=all", follow_redirects=False)
        self.assertEqual(r1.status_code, 200)
        r2 = client.get("/history", follow_redirects=False)
        self.assertEqual(r2.status_code, 303)
        self.assertIn("range_mode=all", r2.headers.get("location", ""))


if __name__ == "__main__":
    unittest.main()
