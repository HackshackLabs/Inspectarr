"""Inventory cache tests."""

import tempfile
import unittest
from pathlib import Path

from tautulli_inspector.inventory_cache import InventoryCache


class InventoryCacheTests(unittest.TestCase):
    def test_progress_and_items_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "inventory.sqlite")
            cache = InventoryCache(db_path)

            cache.set_progress("s1", "2", next_start=25, records_total=100, completed=False)
            got_progress = cache.get_server_progress("s1")
            self.assertEqual(1, len(got_progress))
            self.assertEqual(25, got_progress[0]["next_start"])

            cache.upsert_items("s1", "show", [{"rating_key": "a", "title": "Show A"}])
            shows = cache.get_items("s1", "show")
            self.assertEqual(1, len(shows))
            self.assertEqual("Show A", shows[0]["title"])


if __name__ == "__main__":
    unittest.main()
