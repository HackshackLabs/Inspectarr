"""Tests for optional upstream URL blocking."""

import os
import unittest
from unittest import mock

from pydantic import ValidationError

from inspectarr.url_safety import validate_upstream_base_url


def _clear_settings_cache() -> None:
    from inspectarr.settings import _settings_from_env

    _settings_from_env.cache_clear()


class UrlSafetyTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_settings_cache()

    def test_private_blocking_off_allows_loopback(self) -> None:
        validate_upstream_base_url("http://127.0.0.1:8181", block_private_hosts=False)

    def test_private_blocking_on_rejects_loopback_ip(self) -> None:
        with self.assertRaises(ValueError):
            validate_upstream_base_url("http://127.0.0.1:8181", block_private_hosts=True)

    def test_private_blocking_on_rejects_localhost_host(self) -> None:
        with self.assertRaises(ValueError):
            validate_upstream_base_url("http://localhost:8181", block_private_hosts=True)

    def test_private_blocking_on_allows_public_host(self) -> None:
        validate_upstream_base_url("https://tautulli.example.com", block_private_hosts=True)

    def test_private_blocking_on_rejects_rfc1918(self) -> None:
        with self.assertRaises(ValueError):
            validate_upstream_base_url("http://192.168.1.10:8989", block_private_hosts=True)

    def test_invalid_scheme(self) -> None:
        with self.assertRaises(ValueError):
            validate_upstream_base_url("ftp://example.com", block_private_hosts=False)

    @mock.patch.dict(
        os.environ,
        {
            "TAUTULLI_SERVERS_JSON": (
                '[{"id":"a","name":"A","base_url":"http://127.0.0.1:8181","api_key":"k"}]'
            ),
            "BLOCK_PRIVATE_UPSTREAM_URLS": "true",
        },
        clear=False,
    )
    def test_settings_validation_rejects_private_upstream_when_enabled(self) -> None:
        _clear_settings_cache()
        from inspectarr.settings import Settings

        with self.assertRaises(ValidationError):
            Settings()


if __name__ == "__main__":
    unittest.main()
