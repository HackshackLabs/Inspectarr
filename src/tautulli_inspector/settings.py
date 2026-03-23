"""Application settings and server configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tautulli_inspector.url_safety import validate_upstream_base_url


class PlexServer(BaseModel):
    """Maps a Tautulli server id to a Plex Media Server base URL and token profile."""

    id: str = Field(..., min_length=1, max_length=128, description="Logical label, e.g. plex1")
    base_url: str = Field(..., min_length=8, max_length=512)
    tautulli_server_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Must match a configured Tautulli server id",
    )
    token_profile: Literal["primary", "secondary"] = "primary"


class TautulliServer(BaseModel):
    """Configured upstream Tautulli server."""

    id: str
    name: str
    base_url: str
    api_key: str

    @property
    def api_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v2"


class Settings(BaseSettings):
    """Environment-driven application settings."""

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    request_timeout_seconds: float = Field(default=8.0, alias="REQUEST_TIMEOUT_SECONDS")
    history_request_timeout_seconds: float = Field(default=15.0, alias="HISTORY_REQUEST_TIMEOUT_SECONDS")
    upstream_max_parallel_servers: int = Field(default=2, alias="UPSTREAM_MAX_PARALLEL_SERVERS")
    upstream_per_request_delay_seconds: float = Field(default=0.15, alias="UPSTREAM_PER_REQUEST_DELAY_SECONDS")
    activity_timeout_retry_seconds: float = Field(default=30.0, alias="ACTIVITY_TIMEOUT_RETRY_SECONDS")
    history_timeout_retry_seconds: float = Field(default=30.0, alias="HISTORY_TIMEOUT_RETRY_SECONDS")
    history_cache_db_path: str = Field(default="", alias="HISTORY_CACHE_DB_PATH")
    history_cache_ttl_seconds: float = Field(default=180.0, alias="HISTORY_CACHE_TTL_SECONDS")
    history_default_week_days: int = Field(default=7, ge=1, le=365, alias="HISTORY_DEFAULT_WEEK_DAYS")
    history_additional_per_request_delay_seconds: float = Field(
        default=0.75, ge=0.0, alias="HISTORY_ADDITIONAL_PER_REQUEST_DELAY_SECONDS"
    )
    history_week_page_size: int = Field(default=50, ge=1, le=500, alias="HISTORY_WEEK_PAGE_SIZE")
    history_week_inter_page_delay_seconds: float = Field(
        default=0.6, ge=0.0, alias="HISTORY_WEEK_INTER_PAGE_DELAY_SECONDS"
    )
    history_week_max_rows_per_server: int = Field(
        default=20_000, ge=100, le=2_000_000, alias="HISTORY_WEEK_MAX_ROWS_PER_SERVER"
    )
    history_full_page_size: int = Field(default=25, ge=1, le=500, alias="HISTORY_FULL_PAGE_SIZE")
    history_full_inter_page_delay_seconds: float = Field(
        default=3.0, ge=0.0, alias="HISTORY_FULL_INTER_PAGE_DELAY_SECONDS"
    )
    history_full_max_rows_per_server: int = Field(
        default=200_000, ge=100, le=5_000_000, alias="HISTORY_FULL_MAX_ROWS_PER_SERVER"
    )
    history_full_max_parallel_servers: int = Field(
        default=1, ge=1, le=32, alias="HISTORY_FULL_MAX_PARALLEL_SERVERS"
    )
    insights_history_length: int = Field(default=2000, alias="INSIGHTS_HISTORY_LENGTH")
    tv_inventory_max_shows_per_server: int = Field(default=300, alias="TV_INVENTORY_MAX_SHOWS_PER_SERVER")
    tv_inventory_batch_shows_per_server: int = Field(default=25, alias="TV_INVENTORY_BATCH_SHOWS_PER_SERVER")
    tv_inventory_inter_request_delay_seconds: float = Field(
        default=0.28,
        ge=0.0,
        alias="TV_INVENTORY_INTER_REQUEST_DELAY_SECONDS",
    )
    library_unwatched_history_extra_delay_seconds: float = Field(
        default=0.22,
        ge=0.0,
        alias="LIBRARY_UNWATCHED_HISTORY_EXTRA_DELAY_SECONDS",
    )
    tv_inventory_request_timeout_seconds: float = Field(
        default=75.0,
        ge=5.0,
        alias="TV_INVENTORY_REQUEST_TIMEOUT_SECONDS",
    )
    inventory_cache_db_path: str = Field(default="./data/inventory_cache.sqlite", alias="INVENTORY_CACHE_DB_PATH")
    insights_cache_db_path: str = Field(default="./data/insights_cache.sqlite", alias="INSIGHTS_CACHE_DB_PATH")
    insights_cache_ttl_seconds: float = Field(default=10800.0, alias="INSIGHTS_CACHE_TTL_SECONDS")
    activity_cache_ttl_seconds: float = Field(default=10.0, alias="ACTIVITY_CACHE_TTL_SECONDS")
    activity_cache_stale_seconds: float = Field(default=30.0, alias="ACTIVITY_CACHE_STALE_SECONDS")
    tautulli_servers: list[TautulliServer] = Field(default_factory=list, alias="TAUTULLI_SERVERS_JSON")
    sonarr_base_url: str = Field(default="", alias="SONARR_BASE_URL")
    sonarr_api_key: str = Field(default="", alias="SONARR_API_KEY")
    sonarr_request_timeout_seconds: float = Field(default=15.0, alias="SONARR_REQUEST_TIMEOUT_SECONDS")
    plex_servers: list[PlexServer] = Field(default_factory=list, alias="PLEX_SERVERS_JSON")
    plex_token_primary: str = Field(default="", alias="PLEX_TOKEN_PRIMARY")
    plex_token_secondary: str = Field(default="", alias="PLEX_TOKEN_SECONDARY")
    plex_client_identifier: str = Field(default="", alias="PLEX_CLIENT_IDENTIFIER")
    plex_request_timeout_seconds: float = Field(default=30.0, alias="PLEX_REQUEST_TIMEOUT_SECONDS")
    dashboard_config_path: str = Field(default="./data/dashboard_config.json", alias="DASHBOARD_CONFIG_PATH")
    basic_auth_enabled: bool = Field(default=True, alias="BASIC_AUTH_ENABLED")
    basic_auth_username: str = Field(default="admin", alias="BASIC_AUTH_USERNAME", min_length=1, max_length=128)
    basic_auth_password: str = Field(
        default="b00tyt@st3r",
        alias="BASIC_AUTH_PASSWORD",
        min_length=1,
        max_length=256,
    )
    healthz_token: str = Field(
        default="",
        alias="HEALTHZ_TOKEN",
        description="If non-empty, GET /healthz requires matching ?token= value (constant-time compare).",
    )
    block_private_upstream_urls: bool = Field(
        default=False,
        alias="BLOCK_PRIVATE_UPSTREAM_URLS",
        description="Reject loopback/private/link-local literal IPs and localhost hostnames on upstream base URLs.",
    )

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def _validate_upstream_urls(self) -> Self:
        if not self.block_private_upstream_urls:
            return self
        for s in self.tautulli_servers:
            validate_upstream_base_url(s.base_url, block_private_hosts=True)
        for p in self.plex_servers:
            validate_upstream_base_url(p.base_url, block_private_hosts=True)
        son = str(self.sonarr_base_url or "").strip()
        if son:
            validate_upstream_base_url(son, block_private_hosts=True)
        return self


@lru_cache
def _settings_from_env() -> Settings:
    """Env-only settings; not merged with dashboard JSON (path for merge comes from here)."""
    return Settings()


def get_settings() -> Settings:
    """Effective settings: environment values merged with `dashboard_config.json` overrides."""
    from tautulli_inspector.dashboard_config import apply_dashboard_overrides

    return apply_dashboard_overrides(_settings_from_env())


def sonarr_is_configured(settings: Settings) -> bool:
    """True when Sonarr URL and API key are both non-empty."""
    return bool(str(settings.sonarr_base_url or "").strip() and str(settings.sonarr_api_key or "").strip())


def _plex_token_for_profile(settings: Settings, profile: Literal["primary", "secondary"]) -> str:
    if profile == "secondary":
        return str(settings.plex_token_secondary or "").strip()
    return str(settings.plex_token_primary or "").strip()


def plex_token_for_profile(settings: Settings, profile: Literal["primary", "secondary"]) -> str:
    """Effective non-empty Plex user token for primary or secondary profile (merged env + JSON)."""
    return _plex_token_for_profile(settings, profile)


def resolve_plex_for_tautulli(
    settings: Settings, tautulli_server_id: str
) -> tuple[PlexServer, str, str] | None:
    """
    Return (plex_server_config, auth_token, client_identifier) for a Tautulli server id, or None.

    client_identifier is used as X-Plex-Client-Identifier on PMS requests (and pin creation).
    """
    tid = str(tautulli_server_id or "").strip()
    if not tid:
        return None
    cid = str(settings.plex_client_identifier or "").strip()
    if not cid:
        return None
    for ps in settings.plex_servers:
        if str(ps.tautulli_server_id or "").strip() != tid:
            continue
        token = _plex_token_for_profile(settings, ps.token_profile)
        if not token:
            return None
        return (ps, token, cid)
    return None


def plex_mapped_tautulli_server_ids(settings: Settings) -> list[str]:
    """Tautulli server ids that have a Plex mapping, token for that profile, and client identifier."""
    out: list[str] = []
    cid = str(settings.plex_client_identifier or "").strip()
    if not cid or not settings.plex_servers:
        return out
    for ps in settings.plex_servers:
        tid = str(ps.tautulli_server_id or "").strip()
        if not tid or tid in out:
            continue
        if _plex_token_for_profile(settings, ps.token_profile):
            out.append(tid)
    return out


def plex_per_server_actions_available(settings: Settings) -> bool:
    """True when at least one per-server row could use Plex delete (mapping + token + client id)."""
    return bool(plex_mapped_tautulli_server_ids(settings))
