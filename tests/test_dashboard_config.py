"""Dashboard JSON config merge tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tautulli_inspector.dashboard_config import save_raw_config
from tautulli_inspector.settings import TautulliServer, _settings_from_env, get_settings


class DashboardConfigMergeTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("DASHBOARD_CONFIG_PATH", None)
        os.environ.pop("PORT", None)
        _settings_from_env.cache_clear()

    def test_overrides_merge_port(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "dash.json"
            cfg.write_text(json.dumps({"overrides": {"port": 9123}}), encoding="utf-8")
            os.environ["DASHBOARD_CONFIG_PATH"] = str(cfg)
            os.environ.pop("PORT", None)
            _settings_from_env.cache_clear()
            self.assertEqual(get_settings().port, 9123)

    def test_presentation_plus_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "dash.json"
            os.environ["DASHBOARD_CONFIG_PATH"] = str(cfg)
            os.environ["TAUTULLI_SERVERS_JSON"] = "[]"
            _settings_from_env.cache_clear()
            base = _settings_from_env()
            save_raw_config(
                base,
                {
                    "presentation": {"theme": "ocean", "site_title": "Z"},
                    "overrides": {"request_timeout_seconds": 99.0},
                },
            )
            self.assertEqual(get_settings().request_timeout_seconds, 99.0)

    def test_settings_save_style_overrides_are_json_serializable(self) -> None:
        """Regression: overrides must use dicts, not Pydantic models (json.dumps on save)."""
        rows = [{"id": "a", "name": "A", "base_url": "http://example.com", "api_key": "k"}]
        servers = [TautulliServer.model_validate(r) for r in rows]
        new_ov: dict = {"tautulli_servers": [s.model_dump() for s in servers]}
        payload = {
            "presentation": {
                "theme": "slate",
                "site_title": "T",
                "logo_file": None,
                "footer_text": "",
                "custom_nav_note": "",
            },
            "overrides": new_ov,
        }
        json.dumps(payload)

    def test_tautulli_servers_from_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "dash.json"
            os.environ["DASHBOARD_CONFIG_PATH"] = str(cfg)
            os.environ["TAUTULLI_SERVERS_JSON"] = "[]"
            _settings_from_env.cache_clear()
            base = _settings_from_env()
            servers = [
                {"id": "a", "name": "A", "base_url": "http://example.com", "api_key": "k"},
            ]
            save_raw_config(base, {"overrides": {"tautulli_servers": servers}})
            merged = get_settings()
            self.assertEqual(1, len(merged.tautulli_servers))
            self.assertIsInstance(merged.tautulli_servers[0], TautulliServer)
