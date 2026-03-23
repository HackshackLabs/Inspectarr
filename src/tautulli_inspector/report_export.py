"""Serialize tabular report rows for txt / csv / json / xml downloads."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

EXPORT_FORMATS = frozenset({"txt", "csv", "json", "xml"})

_MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "xml": "application/xml; charset=utf-8",
}

_FILE_EXTENSIONS = {
    "txt": "txt",
    "csv": "csv",
    "json": "json",
    "xml": "xml",
}


def media_type_for_format(export_format: str) -> str:
    return _MEDIA_TYPES[export_format]


def file_extension_for_format(export_format: str) -> str:
    return _FILE_EXTENSIONS[export_format]


def build_export_filename(slug: str, export_format: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug).strip("-") or "export"
    return f"{safe}-{ts}.{file_extension_for_format(export_format)}"


def _sorted_union_keys(rows: list[dict]) -> list[str]:
    return sorted({str(k) for r in rows for k in r})


def _txt_bytes(rows: list[dict], meta: dict[str, Any] | None) -> bytes:
    lines: list[str] = []
    if meta:
        for k in sorted(meta.keys()):
            lines.append(f"# {k}: {meta[k]}")
        lines.append("")
    if not rows:
        lines.append("(no rows)")
        return ("\n".join(lines) + "\n").encode("utf-8")
    keys = _sorted_union_keys(rows)
    lines.append("\t".join(keys))
    for r in rows:
        lines.append("\t".join(_cell_txt(r.get(k)) for k in keys))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _cell_txt(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    if not rows:
        writer = csv.writer(buf)
        writer.writerow(["(no rows)"])
        return buf.getvalue().encode("utf-8")
    keys = _sorted_union_keys(rows)
    writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _cell_txt(r.get(k)) for k in keys})
    return buf.getvalue().encode("utf-8")


def _json_bytes(rows: list[dict], meta: dict[str, Any] | None) -> bytes:
    payload = {"meta": meta or {}, "rows": rows}
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _xml_tag_name(key: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", str(key))
    s = s.strip("_") or "field"
    if s[0].isdigit():
        s = "f_" + s
    return s


def _xml_bytes(rows: list[dict], meta: dict[str, Any] | None) -> bytes:
    root = ET.Element("export")
    for k, v in sorted((meta or {}).items(), key=lambda item: item[0]):
        root.set(_xml_tag_name(str(k)), str(v))
    rows_el = ET.SubElement(root, "rows")
    for r in rows:
        row_el = ET.SubElement(rows_el, "row")
        for key, value in sorted(r.items(), key=lambda item: str(item[0])):
            child = ET.SubElement(row_el, _xml_tag_name(str(key)))
            child.text = "" if value is None else str(value)
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return raw


def build_export_body(
    rows: list[dict],
    export_format: str,
    *,
    meta: dict[str, Any] | None = None,
) -> bytes:
    if export_format not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported format: {export_format}")
    if export_format == "txt":
        return _txt_bytes(rows, meta)
    if export_format == "csv":
        return _csv_bytes(rows)
    if export_format == "json":
        return _json_bytes(rows, meta)
    return _xml_bytes(rows, meta)
