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
        "never_played_min_age_days",
        "history_cutoff_epoch",
        "history_rows_used",
        "history_crawl_mode",
        "history_full_max_rows_per_server",
        "tautulli_server_count",
        "sonarr_series_scanned",
        "overseerr_configured",
        "overseerr_tvdb_keys",
        "overseerr_tmdb_keys",
        "overseerr_fetch_error",
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
        run = s.get("series_run_state")
        f1 = s.get("first_file_added_epoch")
        f2 = s.get("last_file_added_epoch")
        ov = s.get("overseerr") if isinstance(s.get("overseerr"), dict) else {}
        seasons = s.get("seasons") or []
        parts = [f"{title}"]
        parts.append(f"TVDB={tvdb}")
        if s.get("tmdb_id") is not None:
            parts.append(f"TMDB={s.get('tmdb_id')}")
        parts.append(f"Sonarr={sid}")
        parts.append(f"monitored={monitored}")
        parts.append(f"files={total}")
        parts.append(f"never_watched_series={nev}")
        parts.append(f"stale_in_2y_at_series={sls}")
        if run is not None:
            parts.append(f"run_state={run}")
        if f1 is not None:
            parts.append(f"first_file_epoch={f1}")
        if f2 is not None and f2 != f1:
            parts.append(f"last_file_epoch={f2}")
        if ov.get("requested_at_epoch") is not None:
            parts.append(f"overseerr_requested_epoch={ov.get('requested_at_epoch')}")
        if ov.get("requested_by"):
            parts.append(f"overseerr_by={ov.get('requested_by')}")
        if ov.get("library_available_at_epoch") is not None:
            parts.append(f"overseerr_available_epoch={ov.get('library_available_at_epoch')}")
        if ov.get("matched_via"):
            parts.append(f"overseerr_match={ov.get('matched_via')}")
        lp = s.get("last_tautulli_play") if isinstance(s.get("last_tautulli_play"), dict) else {}
        if lp.get("played_at_epoch") is not None:
            parts.append(f"last_play_epoch={lp.get('played_at_epoch')}")
        if lp.get("user"):
            parts.append(f"last_play_user={lp.get('user')}")
        if lp.get("episode_label"):
            parts.append(f"last_play_episode={lp.get('episode_label')}")
        if lp.get("tautulli_server_name"):
            parts.append(f"last_play_server={lp.get('tautulli_server_name')}")
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
            "tmdb_id",
            "sonarr_series_id",
            "series_monitored",
            "total_files",
            "series_never_watched_tautulli",
            "series_level_stale",
            "series_run_state",
            "sonarr_series_status",
            "first_file_added_epoch",
            "last_file_added_epoch",
            "overseerr_requested_at_epoch",
            "overseerr_requested_by",
            "overseerr_library_available_at_epoch",
            "overseerr_matched_via",
            "last_tautulli_play_epoch",
            "last_tautulli_play_user",
            "last_tautulli_play_episode_label",
            "last_tautulli_play_server_name",
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
        ov = s.get("overseerr") if isinstance(s.get("overseerr"), dict) else {}
        lp = s.get("last_tautulli_play") if isinstance(s.get("last_tautulli_play"), dict) else {}
        w.writerow(
            [
                str(s.get("title") or ""),
                s.get("tvdb_id"),
                s.get("tmdb_id"),
                s.get("sonarr_series_id"),
                s.get("series_monitored"),
                s.get("total_files"),
                s.get("series_never_watched_tautulli"),
                s.get("series_level_stale"),
                s.get("series_run_state"),
                s.get("sonarr_series_status"),
                s.get("first_file_added_epoch"),
                s.get("last_file_added_epoch"),
                ov.get("requested_at_epoch"),
                ov.get("requested_by"),
                ov.get("library_available_at_epoch"),
                ov.get("matched_via"),
                lp.get("played_at_epoch"),
                lp.get("user"),
                lp.get("episode_label"),
                lp.get("tautulli_server_name"),
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
            "tmdb_id",
            "sonarr_series_id",
            "series_monitored",
            "total_files",
            "series_never_watched_tautulli",
            "series_level_stale",
            "series_watched_in_2y",
            "series_watched_ever_tautulli",
            "sonarr_series_status",
            "series_run_state",
            "first_file_added_epoch",
            "last_file_added_epoch",
        ):
            val = s.get(attr) if isinstance(s, dict) else None
            if val is None:
                continue
            show.set(attr, str(val).replace("\n", " "))
        ov = s.get("overseerr") if isinstance(s, dict) else None
        if isinstance(ov, dict) and any(
            ov.get(k) is not None for k in ("requested_at_epoch", "requested_by", "library_available_at_epoch", "matched_via")
        ):
            ox = ET.SubElement(show, "overseerr")
            for ok in ("requested_at_epoch", "requested_by", "library_available_at_epoch", "matched_via"):
                v = ov.get(ok)
                if v is not None:
                    ox.set(ok, str(v).replace("\n", " "))
        lp = s.get("last_tautulli_play") if isinstance(s, dict) else None
        if isinstance(lp, dict) and lp.get("played_at_epoch") is not None:
            lx = ET.SubElement(show, "lastTautulliPlay")
            for lk in (
                "played_at_epoch",
                "user",
                "episode_label",
                "episode_title",
                "season_number",
                "episode_number",
                "tautulli_server_id",
                "tautulli_server_name",
            ):
                v = lp.get(lk)
                if v is not None:
                    lx.set(lk, str(v).replace("\n", " "))
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
