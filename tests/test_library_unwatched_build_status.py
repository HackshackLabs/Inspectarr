"""JSON build-status endpoint for library-unwatched indexing wait page."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from starlette.testclient import TestClient


def _clear_settings_cache() -> None:
    from inspectarr.settings import _settings_from_env

    _settings_from_env.cache_clear()


class LibraryUnwatchedBuildStatusTests(unittest.TestCase):
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
    def test_build_status_returns_shape(self) -> None:
        _clear_settings_cache()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"INSIGHTS_CACHE_DB_PATH": f"{tmp}/insights.sqlite"}, clear=False):
                _clear_settings_cache()
                import inspectarr.routes_dashboard as routes_dashboard

                routes_dashboard._insights_cache = None
                from inspectarr.main import create_app

                client = TestClient(create_app())
                r = client.get("/insights/library-unwatched/build-status")
                self.assertEqual(r.status_code, 200)
                data = r.json()
                self.assertIn("ready", data)
                self.assertIn("refresh_in_progress", data)
                self.assertIn("build_step", data)
                self.assertIn("build_step_updated_epoch", data)
                self.assertIsInstance(data["ready"], bool)
                self.assertIsInstance(data["refresh_in_progress"], bool)
                self.assertIsInstance(data["build_step"], str)
                self.assertIsInstance(data["build_step_updated_epoch"], int)
                routes_dashboard._insights_cache = None


if __name__ == "__main__":
    unittest.main()
