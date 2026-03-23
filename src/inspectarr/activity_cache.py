"""In-memory cache for merged live activity snapshots."""

import asyncio
import logging
from time import monotonic
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class ActivitySnapshotCache:
    """Cache with stale-while-revalidate behavior."""

    def __init__(self, ttl_seconds: float, stale_seconds: float) -> None:
        self.ttl_seconds = max(ttl_seconds, 0.0)
        self.stale_seconds = max(stale_seconds, 0.0)
        self._cached_payload: dict | None = None
        self._fetched_at_monotonic: float = 0.0
        self._refresh_lock = asyncio.Lock()
        self._background_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._retry_due_monotonic: float | None = None
        self._timeout_failure_streak: int = 0
        self._last_timeout_snapshot_token: float | None = None
        self._next_retry_interval_seconds: float | None = None

    async def get(self, fetcher: Callable[[], Awaitable[dict]]) -> tuple[dict, str, float]:
        """Return merged activity with cache metadata."""
        now = monotonic()
        if self._cached_payload is None:
            payload = await self._refresh(fetcher)
            return payload, "miss", 0.0

        age = now - self._fetched_at_monotonic
        if age <= self.ttl_seconds:
            return self._cached_payload, "hit_fresh", age

        if age <= (self.ttl_seconds + self.stale_seconds):
            self._ensure_background_refresh(fetcher)
            return self._cached_payload, "hit_stale", age

        try:
            payload = await self._refresh(fetcher)
            return payload, "miss_expired", 0.0
        except Exception:
            logger.exception("Activity cache refresh failed; serving expired stale snapshot")
            return self._cached_payload, "hit_stale_on_error", age

    async def _refresh(self, fetcher: Callable[[], Awaitable[dict]], force: bool = False) -> dict:
        async with self._refresh_lock:
            now = monotonic()
            if not force and self._cached_payload is not None:
                age = now - self._fetched_at_monotonic
                if age <= self.ttl_seconds:
                    return self._cached_payload

            payload = await fetcher()
            self._cached_payload = payload
            self._fetched_at_monotonic = monotonic()
            self._retry_due_monotonic = None
            return payload

    def _ensure_background_refresh(self, fetcher: Callable[[], Awaitable[dict]]) -> None:
        if self._background_task and not self._background_task.done():
            return
        self._background_task = asyncio.create_task(self._background_refresh(fetcher))

    async def _background_refresh(self, fetcher: Callable[[], Awaitable[dict]]) -> None:
        try:
            await self._refresh(fetcher)
        except Exception:
            logger.exception("Background activity cache refresh failed")

    def schedule_retry(self, fetcher: Callable[[], Awaitable[dict]], retry_after_seconds: float) -> None:
        """Schedule forced refresh after delay, if one is not already pending."""
        delay = max(retry_after_seconds, 0.0)
        if self._retry_task and not self._retry_task.done():
            return
        self._retry_due_monotonic = monotonic() + delay
        self._retry_task = asyncio.create_task(self._retry_refresh(fetcher, delay))

    def retry_countdown_seconds(self) -> int | None:
        """Seconds remaining until scheduled retry starts."""
        if self._retry_due_monotonic is None:
            return None
        remaining = int(self._retry_due_monotonic - monotonic())
        if remaining <= 0:
            return 0
        return remaining

    def update_timeout_retry_state(self, has_timeouts: bool, base_retry_seconds: float) -> float | None:
        """Update timeout retry backoff state and return next interval."""
        base = max(base_retry_seconds, 0.0)
        if not has_timeouts:
            self._timeout_failure_streak = 0
            self._last_timeout_snapshot_token = None
            self._next_retry_interval_seconds = None
            return None

        snapshot_token = self._fetched_at_monotonic
        if self._last_timeout_snapshot_token != snapshot_token:
            self._timeout_failure_streak += 1
            self._last_timeout_snapshot_token = snapshot_token
            multiplier = min(2 ** max(self._timeout_failure_streak - 1, 0), 4)
            self._next_retry_interval_seconds = base * multiplier

        return self._next_retry_interval_seconds or base

    def current_retry_interval_seconds(self) -> float | None:
        return self._next_retry_interval_seconds

    async def _retry_refresh(self, fetcher: Callable[[], Awaitable[dict]], delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._refresh(fetcher, force=True)
        except Exception:
            logger.exception("Scheduled activity retry refresh failed")
        finally:
            self._retry_due_monotonic = None
