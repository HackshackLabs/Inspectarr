"""Cold Storage snapshot cache: single-flight and shielded compute."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import scoparr.stale_library_service as sls
from scoparr.settings import Settings


def _minimal_payload() -> dict:
    return {
        "ok": True,
        "series": [],
        "updated_at_epoch": int(time.time()),
        "lookback_days": 730,
        "history_cutoff_epoch": 0,
        "history_rows_used": 0,
        "history_oldest_epoch": None,
        "tautulli_server_count": 0,
        "sonarr_series_scanned": 0,
        "sonarr_series_with_files": 0,
        "errors": [],
    }


class StaleLibraryCacheBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        sls._cache_payload = None
        sls._cache_monotonic = 0.0
        sls._stale_compute_task = None
        sls._stale_compute_lock = None

    async def test_concurrent_gets_single_compute(self) -> None:
        calls = 0

        async def slow_compute(settings: Settings) -> dict:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.12)
            return _minimal_payload()

        settings = Settings(stale_library_cache_path="")
        with patch.object(sls, "compute_stale_library_payload", side_effect=slow_compute):
            a, b = await asyncio.gather(
                sls.get_stale_library_cached(settings, ttl_seconds=120.0, force=False),
                sls.get_stale_library_cached(settings, ttl_seconds=120.0, force=False),
            )
        self.assertEqual(calls, 1)
        self.assertTrue(a.get("ok"))
        self.assertTrue(b.get("ok"))

    async def test_second_call_uses_ttl_cache(self) -> None:
        calls = 0

        async def compute(settings: Settings) -> dict:
            nonlocal calls
            calls += 1
            return _minimal_payload()

        settings = Settings(stale_library_cache_path="")
        with patch.object(sls, "compute_stale_library_payload", side_effect=compute):
            await sls.get_stale_library_cached(settings, ttl_seconds=120.0, force=False)
            await sls.get_stale_library_cached(settings, ttl_seconds=120.0, force=False)
        self.assertEqual(calls, 1)

    async def test_invalidate_then_force_recomputes(self) -> None:
        calls = 0

        async def compute(settings: Settings) -> dict:
            nonlocal calls
            calls += 1
            return _minimal_payload()

        settings = Settings(stale_library_cache_path="")
        with (
            patch.object(sls, "compute_stale_library_payload", side_effect=compute),
            patch("scoparr.settings.get_settings", return_value=settings),
        ):
            await sls.get_stale_library_cached(settings, ttl_seconds=120.0, force=False)
            sls.invalidate_stale_library_cache()
            await sls.get_stale_library_cached(settings, ttl_seconds=120.0, force=True)
        self.assertEqual(calls, 2)

    async def test_fresh_disk_cache_skips_compute(self) -> None:
        calls = 0

        async def compute(settings: Settings) -> dict:
            nonlocal calls
            calls += 1
            return _minimal_payload()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.json"
            path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
            settings = Settings(stale_library_cache_path=str(path))
            sls._cache_payload = None
            with patch.object(sls, "compute_stale_library_payload", side_effect=compute):
                out = await sls.get_stale_library_cached(settings, ttl_seconds=3600.0, force=False)
        self.assertEqual(calls, 0)
        self.assertTrue(out.get("ok"))

    async def test_stale_disk_cache_triggers_compute(self) -> None:
        calls = 0

        async def compute(settings: Settings) -> dict:
            nonlocal calls
            calls += 1
            return _minimal_payload()

        old = dict(_minimal_payload())
        old["updated_at_epoch"] = int(time.time()) - 99999
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            settings = Settings(stale_library_cache_path=str(path))
            sls._cache_payload = None
            with patch.object(sls, "compute_stale_library_payload", side_effect=compute):
                await sls.get_stale_library_cached(settings, ttl_seconds=3600.0, force=False)
        self.assertEqual(calls, 1)

    async def test_invalidate_removes_disk_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.json"
            path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
            settings = Settings(stale_library_cache_path=str(path))
            with patch("scoparr.settings.get_settings", return_value=settings):
                sls.invalidate_stale_library_cache()
            self.assertFalse(path.is_file())

    async def test_delete_eviction_drops_series_preserves_epoch_and_disk(self) -> None:
        pl = _minimal_payload()
        pl["series"] = [
            {"tvdb_id": 1, "title": "A", "seasons": [{"season_number": 1, "file_count": 2}]},
            {"tvdb_id": 2, "title": "B", "seasons": [{"season_number": 1, "file_count": 1}]},
        ]
        epoch = pl["updated_at_epoch"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.json"
            path.write_text(json.dumps(pl), encoding="utf-8")
            settings = Settings(stale_library_cache_path=str(path))
            sls._cache_payload = None
            await sls.apply_stale_library_cache_after_delete(
                settings,
                kind="show",
                tvdb_id=1,
                series_title="A",
                season_number=None,
            )
            reread = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(reread["series"]), 1)
        self.assertIsNotNone(sls._cache_payload)
        self.assertEqual(len(sls._cache_payload.get("series")), 1)
        self.assertEqual(sls._cache_payload["series"][0]["tvdb_id"], 2)
        self.assertEqual(sls._cache_payload["updated_at_epoch"], epoch)

    async def test_monitor_toggle_updates_series_flag_without_full_invalidate(self) -> None:
        pl = _minimal_payload()
        pl["series"] = [{"tvdb_id": 9, "title": "X", "series_monitored": True, "seasons": []}]
        settings = Settings(stale_library_cache_path="")
        sls._cache_payload = dict(pl)
        await sls.apply_stale_library_cache_after_monitor_toggle(
            settings,
            kind="show",
            tvdb_id=9,
            series_title=None,
            season_number=None,
            monitored=False,
        )
        self.assertIsNotNone(sls._cache_payload)
        self.assertFalse(sls._cache_payload["series"][0]["series_monitored"])

    async def test_delete_eviction_matches_sonarr_series_id_without_tvdb(self) -> None:
        pl = _minimal_payload()
        pl["series"] = [
            {"sonarr_series_id": 42, "tvdb_id": None, "title": "Odd Show", "seasons": [{"season_number": 1}]},
            {"sonarr_series_id": 99, "tvdb_id": 1, "title": "Other", "seasons": []},
        ]
        settings = Settings(stale_library_cache_path="")
        sls._cache_payload = dict(pl)
        await sls.apply_stale_library_cache_after_delete(
            settings,
            kind="show",
            tvdb_id=None,
            sonarr_series_id=42,
            series_title="Wrong Title",
            season_number=None,
        )
        self.assertIsNotNone(sls._cache_payload)
        self.assertEqual(len(sls._cache_payload["series"]), 1)
        self.assertEqual(sls._cache_payload["series"][0]["sonarr_series_id"], 99)


if __name__ == "__main__":
    unittest.main()
