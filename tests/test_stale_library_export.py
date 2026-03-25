"""Cold Storage stale-list export (JSON, CSV, TXT, XML)."""

from __future__ import annotations

import csv
import io
import json
import unittest
import xml.etree.ElementTree as ET

from scoparr.stale_library_export import build_stale_export


def _sample_payload() -> dict:
    return {
        "ok": True,
        "updated_at_epoch": 1700000000,
        "lookback_days": 730,
        "history_rows_used": 100,
        "tautulli_server_count": 1,
        "sonarr_series_scanned": 50,
        "errors": [],
        "series": [
            {
                "title": "Zebra Show",
                "tvdb_id": 2,
                "sonarr_series_id": 22,
                "series_monitored": True,
                "total_files": 3,
                "series_never_watched_tautulli": False,
                "series_level_stale": True,
                "seasons": [{"season_number": 1, "file_count": 3, "monitored": True, "stale": True}],
            },
            {
                "title": "Alpha Show",
                "tvdb_id": 1,
                "sonarr_series_id": 11,
                "series_monitored": False,
                "total_files": 1,
                "series_never_watched_tautulli": True,
                "series_level_stale": True,
                "seasons": [],
            },
        ],
    }


class StaleLibraryExportTests(unittest.TestCase):
    def test_json_sort_asc_puts_alpha_first(self) -> None:
        body, mime, name = build_stale_export("json", _sample_payload(), "asc")
        self.assertIn("application/json", mime)
        self.assertTrue(name.endswith(".json"))
        data = json.loads(body.decode("utf-8"))
        titles = [s["title"] for s in data["series"]]
        self.assertEqual(titles, ["Alpha Show", "Zebra Show"])
        self.assertEqual(data["series_count"], 2)

    def test_json_sort_desc(self) -> None:
        body, _, _ = build_stale_export("json", _sample_payload(), "desc")
        data = json.loads(body.decode("utf-8"))
        titles = [s["title"] for s in data["series"]]
        self.assertEqual(titles, ["Zebra Show", "Alpha Show"])

    def test_csv_has_header_and_rows(self) -> None:
        body, mime, name = build_stale_export("csv", _sample_payload(), "asc")
        self.assertIn("text/csv", mime)
        self.assertTrue(name.endswith(".csv"))
        r = csv.reader(io.StringIO(body.decode("utf-8")))
        rows = list(r)
        self.assertEqual(rows[0][0], "title")
        self.assertEqual(len(rows), 3)

    def test_txt_contains_titles(self) -> None:
        body, mime, _ = build_stale_export("txt", _sample_payload(), "asc")
        self.assertIn("text/plain", mime)
        text = body.decode("utf-8")
        self.assertIn("Alpha Show", text)
        self.assertIn("Zebra Show", text)

    def test_xml_parses_and_series_count(self) -> None:
        body, mime, name = build_stale_export("xml", _sample_payload(), "asc")
        self.assertIn("xml", mime)
        self.assertTrue(name.endswith(".xml"))
        root = ET.fromstring(body.decode("utf-8"))
        self.assertEqual(root.tag, "staleLibrary")
        self.assertEqual(root.get("seriesCount"), "2")
        series = root.find("seriesList")
        assert series is not None
        shows = series.findall("series")
        self.assertEqual(len(shows), 2)
        self.assertEqual(shows[0].get("title"), "Alpha Show")


if __name__ == "__main__":
    unittest.main()
