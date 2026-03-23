"""CSRF middleware tests."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from starlette.testclient import TestClient


def _clear_settings_cache() -> None:
    from inspectarr.settings import _settings_from_env

    _settings_from_env.cache_clear()


class CsrfMiddlewareTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_settings_cache()

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "u",
            "BASIC_AUTH_PASSWORD": "p",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_json_post_rejects_without_csrf_header(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        auth = ("u", "p")
        client = TestClient(create_app())
        r = client.get("/settings", auth=auth)
        self.assertEqual(r.status_code, 200)
        self.assertIn("csrf_token", client.cookies)

        r2 = client.post(
            "/settings/plex-auth/start",
            json={"profile": "primary"},
            auth=auth,
        )
        self.assertEqual(r2.status_code, 403)

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "u",
            "BASIC_AUTH_PASSWORD": "p",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_json_post_accepts_matching_csrf_header(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        auth = ("u", "p")
        client = TestClient(create_app())
        client.get("/settings", auth=auth)
        token = client.cookies.get("csrf_token")
        self.assertTrue(token)

        r = client.post(
            "/settings/plex-auth/start",
            json={"profile": "primary"},
            auth=auth,
            headers={"X-CSRF-Token": token},
        )
        self.assertNotEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
