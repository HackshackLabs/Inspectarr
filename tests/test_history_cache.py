"""SQLite history cache tests."""

import tempfile
import unittest
from pathlib import Path

from tautulli_inspector.history_cache import HistoryPageCache


class HistoryPageCacheTests(unittest.TestCase):
    def test_get_returns_cached_payload_within_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "history_cache.sqlite")
            cache = HistoryPageCache(db_path=db_path)
            key = cache.make_key("abc")
            payload = {"rows": [1, 2, 3]}

            cache.set(key, payload)
            got = cache.get(key, ttl_seconds=10.0)

            self.assertEqual(payload, got)

    def test_get_expires_payload_past_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "history_cache.sqlite")
            cache = HistoryPageCache(db_path=db_path)
            key = cache.make_key("abc")
            cache.set(key, {"rows": []})

            got = cache.get(key, ttl_seconds=0.0)
            self.assertIsNone(got)

    def test_delete_removes_cached_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "history_cache.sqlite")
            cache = HistoryPageCache(db_path=db_path)
            key = cache.make_key("abc")
            cache.set(key, {"rows": [1]})
            cache.delete(key)
            got = cache.get(key, ttl_seconds=9999.0)
            self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
