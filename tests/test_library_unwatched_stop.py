"""POST /insights/library-unwatched/stop cancels background snapshot tasks."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from starlette.testclient import TestClient


def _clear_settings_cache() -> None:
    from inspectarr.settings import _settings_from_env

    _settings_from_env.cache_clear()


class LibraryUnwatchedStopTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_settings_cache()

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "false",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_stop_returns_shape(self) -> None:
        _clear_settings_cache()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"INSIGHTS_CACHE_DB_PATH": f"{tmp}/insights.sqlite"}, clear=False):
                _clear_settings_cache()
                import inspectarr.routes_dashboard as routes_dashboard

                routes_dashboard._insights_cache = None
                from inspectarr.main import create_app

                client = TestClient(create_app())
                client.get("/insights/library-unwatched/build-status")
                token = client.cookies.get("csrf_token")
                self.assertTrue(token)
                r = client.post(
                    "/insights/library-unwatched/stop",
                    json={},
                    headers={"X-CSRF-Token": token},
                )
                self.assertEqual(r.status_code, 200)
                data = r.json()
                self.assertIn("stopped", data)
                self.assertIn("had_registered_task", data)
                self.assertIsInstance(data["stopped"], bool)
                self.assertIsInstance(data["had_registered_task"], bool)
                routes_dashboard._insights_cache = None


if __name__ == "__main__":
    unittest.main()
