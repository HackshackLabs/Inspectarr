"""Serialize stale-movie snapshot for download (JSON, CSV, TXT, XML)."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal
from xml.etree import ElementTree as ET

ExportFormat = Literal["json", "csv", "txt", "xml"]
StaleMoviesSort = Literal["asc", "desc", "size_asc", "size_desc"]


def _movie_size_on_disk_for_sort(m: dict[str, Any]) -> int:
    raw = m.get("size_on_disk_bytes")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def sort_stale_movies(movies: list[dict[str, Any]], sort: StaleMoviesSort) -> None:
    if sort in ("size_asc", "size_desc"):
        reverse = sort == "size_desc"
        movies.sort(
            key=lambda x: (_movie_size_on_disk_for_sort(x), str(x.get("title") or "").lower()),
            reverse=reverse,
        )
        return
    reverse = sort == "desc"
    movies.sort(key=lambda x: str(x.get("title") or "").lower(), reverse=reverse)


def _sorted_movies(payload: dict[str, Any], sort: StaleMoviesSort) -> list[dict[str, Any]]:
    movies: list[dict[str, Any]] = list(payload.get("movies") or [])
    sort_stale_movies(movies, sort)
    return movies


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
        "radarr_movies_scanned",
        "radarr_movies_with_files",
        "overseerr_configured",
        "overseerr_movie_tmdb_keys",
        "overseerr_fetch_error",
        "errors",
    )
    return {k: payload.get(k) for k in keys if k in payload}


def render_stale_movies_export_json(payload: dict[str, Any], sort: StaleMoviesSort) -> bytes:
    movies = _sorted_movies(payload, sort)
    out: dict[str, Any] = {
        **_export_meta(payload),
        "movie_count": len(movies),
        "movies": movies,
    }
    return (json.dumps(out, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render_stale_movies_export_txt(payload: dict[str, Any], sort: StaleMoviesSort) -> bytes:
    lines: list[str] = []
    for m in _sorted_movies(payload, sort):
        title = str(m.get("title") or "")
        parts = [title]
        if m.get("tmdb_id") is not None:
            parts.append(f"TMDB={m.get('tmdb_id')}")
        if m.get("imdb_id") is not None:
            parts.append(f"IMDB={m.get('imdb_id')}")
        parts.append(f"Radarr={m.get('radarr_movie_id')}")
        parts.append(f"monitored={m.get('movie_monitored')}")
        if m.get("size_on_disk_bytes") is not None:
            parts.append(f"size_on_disk_bytes={m.get('size_on_disk_bytes')}")
        parts.append(f"never_watched={m.get('movie_never_watched_tautulli')}")
        parts.append(f"stale_in_2y_level={m.get('movie_level_stale')}")
        ov = m.get("overseerr") if isinstance(m.get("overseerr"), dict) else {}
        if ov.get("requested_at_epoch") is not None:
            parts.append(f"overseerr_requested_epoch={ov.get('requested_at_epoch')}")
        if ov.get("requested_by"):
            parts.append(f"overseerr_by={ov.get('requested_by')}")
        if ov.get("library_available_at_epoch") is not None:
            parts.append(f"overseerr_available_epoch={ov.get('library_available_at_epoch')}")
        lp = m.get("last_tautulli_play") if isinstance(m.get("last_tautulli_play"), dict) else {}
        if lp.get("played_at_epoch") is not None:
            parts.append(f"last_play_epoch={lp.get('played_at_epoch')}")
        if lp.get("user"):
            parts.append(f"last_play_user={lp.get('user')}")
        lines.append(" · ".join(str(p) for p in parts))
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def render_stale_movies_export_csv(payload: dict[str, Any], sort: StaleMoviesSort) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "title",
            "tmdb_id",
            "imdb_id",
            "radarr_movie_id",
            "monitored",
            "size_on_disk_bytes",
            "never_watched_tautulli",
            "stale_movie_level_2y",
            "overseerr_requested_epoch",
            "overseerr_requested_by",
            "overseerr_available_epoch",
            "last_play_epoch",
            "last_play_user",
        ]
    )
    for m in _sorted_movies(payload, sort):
        ov = m.get("overseerr") if isinstance(m.get("overseerr"), dict) else {}
        lp = m.get("last_tautulli_play") if isinstance(m.get("last_tautulli_play"), dict) else {}
        w.writerow(
            [
                m.get("title"),
                m.get("tmdb_id"),
                m.get("imdb_id"),
                m.get("radarr_movie_id"),
                m.get("movie_monitored"),
                m.get("size_on_disk_bytes"),
                m.get("movie_never_watched_tautulli"),
                m.get("movie_level_stale"),
                ov.get("requested_at_epoch"),
                ov.get("requested_by"),
                ov.get("library_available_at_epoch"),
                lp.get("played_at_epoch"),
                lp.get("user"),
            ]
        )
    return buf.getvalue().encode("utf-8")


def render_stale_movies_export_xml(payload: dict[str, Any], sort: StaleMoviesSort) -> bytes:
    root = ET.Element("staleMovies")
    meta = ET.SubElement(root, "meta")
    for k, v in _export_meta(payload).items():
        if v is None:
            continue
        el = ET.SubElement(meta, k.replace("_", "-"))
        el.text = str(v)
    grid = ET.SubElement(root, "movies")
    for m in _sorted_movies(payload, sort):
        row = ET.SubElement(grid, "movie")
        for key in (
            "title",
            "tmdb_id",
            "imdb_id",
            "radarr_movie_id",
            "movie_monitored",
            "size_on_disk_bytes",
            "movie_never_watched_tautulli",
            "movie_level_stale",
        ):
            if m.get(key) is None and key not in ("movie_monitored", "movie_never_watched_tautulli", "movie_level_stale"):
                continue
            el = ET.SubElement(row, key.replace("_", "-"))
            el.text = str(m.get(key))
        ov = m.get("overseerr") if isinstance(m.get("overseerr"), dict) else {}
        if ov:
            oel = ET.SubElement(row, "overseerr")
            for ok in ("requested_at_epoch", "requested_by", "library_available_at_epoch", "matched_via"):
                if ov.get(ok) is None:
                    continue
                o = ET.SubElement(oel, ok.replace("_", "-"))
                o.text = str(ov.get(ok))
        lp = m.get("last_tautulli_play") if isinstance(m.get("last_tautulli_play"), dict) else {}
        if lp:
            pel = ET.SubElement(row, "last-tautulli-play")
            for pk in ("played_at_epoch", "user", "episode_label", "tautulli_server_name"):
                if lp.get(pk) is None:
                    continue
                p = ET.SubElement(pel, pk.replace("_", "-"))
                p.text = str(lp.get(pk))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_stale_movies_export(
    fmt: ExportFormat,
    payload: dict[str, Any],
    sort: StaleMoviesSort,
) -> tuple[bytes, str, str]:
    if fmt == "json":
        return render_stale_movies_export_json(payload, sort), "application/json", "stale-movies.json"
    if fmt == "csv":
        return render_stale_movies_export_csv(payload, sort), "text/csv; charset=utf-8", "stale-movies.csv"
    if fmt == "txt":
        return render_stale_movies_export_txt(payload, sort), "text/plain; charset=utf-8", "stale-movies.txt"
    return render_stale_movies_export_xml(payload, sort), "application/xml", "stale-movies.xml"
