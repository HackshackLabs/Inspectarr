"""Shared typed models."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ActivityFetchResult:
    """Normalized live activity result for one server."""

    server_id: str
    server_name: str
    status: str
    sessions: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class HistoryFetchResult:
    """Normalized history result for one server."""

    server_id: str
    server_name: str
    status: str
    rows: list[dict] = field(default_factory=list)
    records_filtered: int | None = None
    records_total: int | None = None
    error: str | None = None


@dataclass(slots=True)
class InventoryFetchResult:
    """Normalized TV inventory result for one server."""

    server_id: str
    server_name: str
    status: str
    shows: list[dict] = field(default_factory=list)
    seasons: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    section_progress: list[dict] = field(default_factory=list)
    index_complete: bool = False
    error: str | None = None
