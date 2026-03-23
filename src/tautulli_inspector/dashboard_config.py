"""Dashboard JSON config (presentation + optional settings overrides)."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from tautulli_inspector.settings import PlexServer, Settings, TautulliServer

logger = logging.getLogger(__name__)

ThemeId = Literal["slate", "ocean", "ember", "forest", "paper"]

THEME_CHOICES: list[tuple[str, str]] = [
    ("slate", "Slate (default dark)"),
    ("ocean", "Ocean (cool blue dark)"),
    ("ember", "Ember (warm dark)"),
    ("forest", "Forest (green dark)"),
    ("paper", "Paper (light)"),
]


class PresentationConfig(BaseModel):
    """UI-only options stored under `presentation` in the dashboard config file."""

    theme: ThemeId = "slate"
    site_title: str = Field(default="Tautulli Inspector", max_length=200)
    logo_file: str | None = Field(default=None, max_length=255)
    footer_text: str = Field(default="", max_length=500)
    custom_nav_note: str = Field(default="", max_length=300)


def config_file_path(base: Settings) -> Path:
    """Path to dashboard JSON (env-only `dashboard_config_path`)."""
    return Path(base.dashboard_config_path).expanduser()


def upload_dir(base: Settings) -> Path:
    return config_file_path(base).parent / "uploads"


def load_raw_config(base: Settings) -> dict[str, Any]:
    path = config_file_path(base)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read dashboard config %s: %s", path, exc)
        return {}


def save_raw_config(base: Settings, data: dict[str, Any]) -> None:
    path = config_file_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_plex_client_identifier(base: Settings) -> str:
    """
    Return X-Plex-Client-Identifier, persisting a new UUID into dashboard overrides when missing.
    """
    raw = load_raw_config(base)
    ov = raw.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
    cid = str(ov.get("plex_client_identifier") or "").strip()
    if cid:
        return cid
    cid = str(uuid.uuid4())
    ov = {**ov, "plex_client_identifier": cid}
    out = {**raw, "overrides": ov}
    save_raw_config(base, out)
    return cid


def load_presentation(base: Settings) -> PresentationConfig:
    raw = load_raw_config(base).get("presentation")
    if not isinstance(raw, dict):
        return PresentationConfig()
    try:
        return PresentationConfig.model_validate(raw)
    except ValidationError:
        return PresentationConfig()


def load_overrides_dict(base: Settings) -> dict[str, Any]:
    raw = load_raw_config(base).get("overrides")
    if not isinstance(raw, dict):
        return {}
    return raw


def apply_dashboard_overrides(base: Settings) -> Settings:
    """Merge JSON `overrides` onto env-loaded settings."""
    ov = load_overrides_dict(base)
    if not ov:
        return base
    # Basic auth credentials must never be overridden from dashboard JSON (env / .env only).
    _auth_keys = frozenset({"basic_auth_enabled", "basic_auth_username", "basic_auth_password"})
    allowed = set(Settings.model_fields.keys()) - {"model_config"} - _auth_keys
    filtered = {k: v for k, v in ov.items() if k in allowed}
    if not filtered:
        return base
    try:
        if "tautulli_servers" in filtered and filtered["tautulli_servers"] is not None:
            servers = filtered["tautulli_servers"]
            if isinstance(servers, list):
                filtered["tautulli_servers"] = [TautulliServer.model_validate(s) for s in servers]
        if "plex_servers" in filtered and filtered["plex_servers"] is not None:
            plex_list = filtered["plex_servers"]
            if isinstance(plex_list, list):
                filtered["plex_servers"] = [PlexServer.model_validate(s) for s in plex_list]
        return base.model_copy(update=filtered)
    except (ValidationError, TypeError, ValueError) as exc:
        logger.warning("Invalid dashboard overrides ignored: %s", exc)
        return base


def build_template_globals(page_title: str | None = None) -> dict[str, Any]:
    """Context keys shared by all HTML pages (call from routes)."""
    import tautulli_inspector.settings as settings_mod

    env_base = settings_mod._settings_from_env()
    pres = load_presentation(env_base)
    logo_url = f"/uploads/{pres.logo_file}" if pres.logo_file else None
    return {
        "site_title": pres.site_title,
        "page_title": page_title or "",
        "theme": pres.theme,
        "logo_url": logo_url,
        "footer_text": pres.footer_text,
        "nav_note": pres.custom_nav_note,
        "nav_current": "",
    }


# Operational fields editable from /settings (snake_case Settings attribute names).
# Excludes tautulli_servers (JSON textarea) and dashboard_config_path (env-only).
SETTINGS_EDITOR_FIELDS: list[tuple[str, str, str]] = [
    ("host", "Bind host", "text"),
    ("port", "Bind port", "int"),
    ("request_timeout_seconds", "Request timeout (s)", "float"),
    ("history_request_timeout_seconds", "History request timeout (s)", "float"),
    ("upstream_max_parallel_servers", "Max parallel upstream servers", "int"),
    ("upstream_per_request_delay_seconds", "Per-request delay (s)", "float"),
    ("activity_timeout_retry_seconds", "Activity timeout retry (s)", "float"),
    ("history_timeout_retry_seconds", "History timeout retry (s)", "float"),
    ("history_cache_db_path", "History cache DB path (empty = off)", "text"),
    ("history_cache_ttl_seconds", "History cache TTL (s)", "float"),
    ("history_default_week_days", "Default history week length (days)", "int"),
    ("history_additional_per_request_delay_seconds", "Extra history delay (s)", "float"),
    ("history_week_page_size", "History week page size", "int"),
    ("history_week_inter_page_delay_seconds", "History week inter-page delay (s)", "float"),
    ("history_week_max_rows_per_server", "History week max rows / server", "int"),
    ("history_full_page_size", "History full crawl page size", "int"),
    ("history_full_inter_page_delay_seconds", "History full inter-page delay (s)", "float"),
    ("history_full_max_rows_per_server", "History full max rows / server", "int"),
    ("history_full_max_parallel_servers", "History full max parallel servers", "int"),
    ("insights_history_length", "Insights history rows / server", "int"),
    ("tv_inventory_max_shows_per_server", "TV inventory max shows / server", "int"),
    ("tv_inventory_batch_shows_per_server", "TV inventory batch shows / request", "int"),
    ("inventory_cache_db_path", "Inventory cache DB path", "text"),
    ("insights_cache_db_path", "Insights cache DB path", "text"),
    ("insights_cache_ttl_seconds", "Insights cache TTL (s)", "float"),
    ("activity_cache_ttl_seconds", "Activity cache TTL (s)", "float"),
    ("activity_cache_stale_seconds", "Activity cache stale window (s)", "float"),
]
