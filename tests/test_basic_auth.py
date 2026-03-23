"""Tests for HTTP Basic auth middleware."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from starlette.testclient import TestClient


def _clear_settings_cache() -> None:
    from inspectarr.settings import _settings_from_env

    _settings_from_env.cache_clear()


class BasicAuthMiddlewareTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_settings_cache()

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "testuser",
            "BASIC_AUTH_PASSWORD": "testpass",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_protected_route_401_without_credentials(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        client = TestClient(create_app())
        r = client.get("/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("WWW-Authenticate", r.headers)

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "testuser",
            "BASIC_AUTH_PASSWORD": "testpass",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_protected_route_200_with_valid_basic(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        client = TestClient(create_app())
        r = client.get("/", auth=("testuser", "testpass"))
        self.assertEqual(r.status_code, 200)

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "testuser",
            "BASIC_AUTH_PASSWORD": "testpass",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_wrong_password_401(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        client = TestClient(create_app())
        r = client.get("/", auth=("testuser", "wrong"))
        self.assertEqual(r.status_code, 401)

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "false",
            "BASIC_AUTH_USERNAME": "testuser",
            "BASIC_AUTH_PASSWORD": "testpass",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_disabled_allows_anonymous(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        client = TestClient(create_app())
        r = client.get("/")
        self.assertEqual(r.status_code, 200)

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "testuser",
            "BASIC_AUTH_PASSWORD": "testpass",
            "TAUTULLI_SERVERS_JSON": "[]",
        },
        clear=False,
    )
    def test_healthz_bypasses_auth(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        client = TestClient(create_app())
        r = client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")

    @mock.patch.dict(
        os.environ,
        {
            "BASIC_AUTH_ENABLED": "true",
            "BASIC_AUTH_USERNAME": "testuser",
            "BASIC_AUTH_PASSWORD": "testpass",
            "TAUTULLI_SERVERS_JSON": "[]",
            "HEALTHZ_TOKEN": "supersecret",
        },
        clear=False,
    )
    def test_healthz_token_required_when_configured(self) -> None:
        _clear_settings_cache()
        from inspectarr.main import create_app

        client = TestClient(create_app())
        self.assertEqual(client.get("/healthz").status_code, 401)
        self.assertEqual(client.get("/healthz", params={"token": "wrong"}).status_code, 401)
        ok = client.get("/healthz", params={"token": "supersecret"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json().get("status"), "ok")
