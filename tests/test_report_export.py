"""Report export serialization."""

import unittest
import xml.etree.ElementTree as ET

from tautulli_inspector.report_export import build_export_body, build_export_filename


class ReportExportTests(unittest.TestCase):
    def test_csv_roundtrip_headers(self) -> None:
        rows = [{"a": 1, "b": "x,y"}, {"a": 2, "b": "z"}]
        raw = build_export_body(rows, "csv")
        text = raw.decode("utf-8")
        self.assertIn("a", text.splitlines()[0])
        self.assertIn("x,y", text)

    def test_json_wraps_meta(self) -> None:
        raw = build_export_body([{"k": "v"}], "json", meta={"group": "test"})
        data = __import__("json").loads(raw.decode("utf-8"))
        self.assertEqual("test", data["meta"]["group"])
        self.assertEqual(1, len(data["rows"]))

    def test_xml_parseable(self) -> None:
        raw = build_export_body([{"title": "A"}], "xml", meta={"group": "g"})
        ET.fromstring(raw.decode("utf-8"))

    def test_filename_safe(self) -> None:
        name = build_export_filename("a-b", "csv")
        self.assertTrue(name.endswith(".csv"))
        self.assertRegex(name, r"^a-b-\d{8}T\d{6}Z\.csv$")


if __name__ == "__main__":
    unittest.main()
