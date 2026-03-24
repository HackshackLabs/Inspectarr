"""Serialize Cold Storage stale-library snapshot for download (JSON, CSV, TXT, XML)."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal
from xml.etree import ElementTree as ET

ExportFormat = Literal["json", "csv", "txt", "xml"]


def _sorted_series(payload: dict[str, Any], sort: Literal["asc", "desc"]) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = list(payload.get("series") or [])
    reverse = sort == "desc"
    series.sort(key=lambda x: str(x.get("title") or "").lower(), reverse=reverse)
    return series


def _export_meta(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ok",
        "updated_at_epoch",
        "lookback_days",
        "history_cutoff_epoch",
        "history_rows_used",
        "history_crawl_mode",
        "history_full_max_rows_per_server",
        "tautulli_server_count",
        "sonarr_series_scanned",
        "errors",
    )
    return {k: payload.get(k) for k in keys if k in payload}


def render_stale_export_json(payload: dict[str, Any], sort: Literal["asc", "desc"]) -> bytes:
    series = _sorted_series(payload, sort)
    out: dict[str, Any] = {
        **_export_meta(payload),
        "series_count": len(series),
        "series": series,
    }
    return (json.dumps(out, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render_stale_export_txt(payload: dict[str, Any], sort: Literal["asc", "desc"]) -> bytes:
    lines: list[str] = []
    for s in _sorted_series(payload, sort):
        title = str(s.get("title") or "")
        tvdb = s.get("tvdb_id")
        sid = s.get("sonarr_series_id")
        monitored = s.get("series_monitored")
        total = s.get("total_files")
        nev = s.get("series_never_watched_tautulli")
        sls = s.get("series_level_stale")
        seasons = s.get("seasons") or []
        parts = [f"{title}"]
        parts.append(f"TVDB={tvdb}")
        parts.append(f"Sonarr={sid}")
        parts.append(f"monitored={monitored}")
        parts.append(f"files={total}")
        parts.append(f"never_watched_series={nev}")
        parts.append(f"stale_in_2y_at_series={sls}")
        if isinstance(seasons, list) and seasons:
            snips = []
            for se in seasons:
                if not isinstance(se, dict):
                    continue
                try:
                    sn = int(se.get("season_number", -1))
                except (TypeError, ValueError):
                    sn = se.get("season_number")
                fc = se.get("file_count")
                sm = se.get("monitored")
                snips.append(f"S{sn}:{fc}files mon={sm}")
            parts.append("seasons[" + "; ".join(snips) + "]")
        lines.append(" · ".join(str(p) for p in parts))
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def render_stale_export_csv(payload: dict[str, Any], sort: Literal["asc", "desc"]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "title",
            "tvdb_id",
            "sonarr_series_id",
            "series_monitored",
            "total_files",
            "series_never_watched_tautulli",
            "series_level_stale",
            "stale_season_numbers",
            "seasons_json",
        ]
    )
    for s in _sorted_series(payload, sort):
        seasons = s.get("seasons") if isinstance(s.get("seasons"), list) else []
        snums: list[str] = []
        clean_seasons: list[dict[str, Any]] = []
        for se in seasons:
            if not isinstance(se, dict):
                continue
            clean_seasons.append(se)
            sn = se.get("season_number")
            if sn is not None:
                snums.append(str(sn))
        w.writerow(
            [
                str(s.get("title") or ""),
                s.get("tvdb_id"),
                s.get("sonarr_series_id"),
                s.get("series_monitored"),
                s.get("total_files"),
                s.get("series_never_watched_tautulli"),
                s.get("series_level_stale"),
                ",".join(snums),
                json.dumps(clean_seasons, ensure_ascii=False),
            ]
        )
    return buf.getvalue().encode("utf-8")


def render_stale_export_xml(payload: dict[str, Any], sort: Literal["asc", "desc"]) -> bytes:
    root = ET.Element("staleLibrary")
    series_sorted = _sorted_series(payload, sort)
    root.set("seriesCount", str(len(series_sorted)))
    if payload.get("updated_at_epoch") is not None:
        root.set("updatedAtEpoch", str(int(payload["updated_at_epoch"])))
    if payload.get("lookback_days") is not None:
        root.set("lookbackDays", str(payload["lookback_days"]))
    if payload.get("history_rows_used") is not None:
        root.set("historyRowsUsed", str(payload["history_rows_used"]))
    if payload.get("tautulli_server_count") is not None:
        root.set("tautulliServerCount", str(payload["tautulli_server_count"]))
    if payload.get("sonarr_series_scanned") is not None:
        root.set("sonarrSeriesScanned", str(payload["sonarr_series_scanned"]))
    errs = payload.get("errors")
    if isinstance(errs, list) and errs:
        err_el = ET.SubElement(root, "warnings")
        for item in errs:
            w = ET.SubElement(err_el, "warning")
            w.text = str(item)
    series_el = ET.SubElement(root, "seriesList")
    for s in series_sorted:
        show = ET.SubElement(series_el, "series")
        for attr in (
            "title",
            "tvdb_id",
            "sonarr_series_id",
            "series_monitored",
            "total_files",
            "series_never_watched_tautulli",
            "series_level_stale",
            "series_watched_in_2y",
            "series_watched_ever_tautulli",
        ):
            val = s.get(attr) if isinstance(s, dict) else None
            if val is None:
                continue
            show.set(attr, str(val).replace("\n", " "))
        seasons = s.get("seasons") if isinstance(s.get("seasons"), list) else []
        seas_el = ET.SubElement(show, "seasons")
        for se in seasons:
            if not isinstance(se, dict):
                continue
            child = ET.SubElement(seas_el, "season")
            for sk in (
                "season_number",
                "file_count",
                "monitored",
                "watched_in_2y",
                "never_watched_tautulli",
                "stale",
            ):
                if sk not in se:
                    continue
                sv = se.get(sk)
                if sv is None:
                    continue
                child.set(sk, str(sv).replace("\n", " "))
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
    decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return (decl + raw + "\n").encode("utf-8")


def build_stale_export(
    export_format: ExportFormat,
    payload: dict[str, Any],
    sort: Literal["asc", "desc"],
) -> tuple[bytes, str, str]:
    """Return (body, media_type, filename_suffix)."""
    ext = export_format
    if export_format == "json":
        body = render_stale_export_json(payload, sort)
        mime = "application/json; charset=utf-8"
    elif export_format == "csv":
        body = render_stale_export_csv(payload, sort)
        mime = "text/csv; charset=utf-8"
    elif export_format == "txt":
        body = render_stale_export_txt(payload, sort)
        mime = "text/plain; charset=utf-8"
    else:
        body = render_stale_export_xml(payload, sort)
        mime = "application/xml; charset=utf-8"
    return body, mime, f"cold-storage-stale-{sort}.{ext}"
