"""Client for fetching data from upstream Tautulli servers."""

import asyncio
import logging
from time import perf_counter
from typing import Awaitable, Callable, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from scoparr.aggregate import canonical_utc_epoch_for_row
from scoparr.models import ActivityFetchResult, HistoryFetchResult
from scoparr.settings import TautulliServer

logger = logging.getLogger(__name__)

# Hard ceiling for each Tautulli ``get_history`` request (``length`` param), regardless of settings/UI.
TAUTULLI_GET_HISTORY_MAX_ROWS_PER_REQUEST = 200_000

TautulliTraceHook = Callable[[TautulliServer, str, int | None, bool], None]
HistoryRowsHook = Callable[[TautulliServer, int], None]


def _clamp_get_history_length(length: int) -> int:
    return min(max(int(length), 1), TAUTULLI_GET_HISTORY_MAX_ROWS_PER_REQUEST)


def _history_rows_until_cutoff(batch: list[dict], stop_before_epoch: int | None) -> tuple[list[dict], bool]:
    """Keep rows while timestamps stay at or after cutoff (UTC epoch start-of-day)."""
    if stop_before_epoch is None:
        return batch, False
    kept: list[dict] = []
    for row in batch:
        ep = canonical_utc_epoch_for_row(row)
        if ep > 0 and ep < stop_before_epoch:
            return kept, True
        kept.append(row)
    return kept, False


class TautulliClient:
    """Small client wrapper for Tautulli API fan-out calls."""

    def __init__(
        self,
        timeout_seconds: float,
        max_parallel_servers: int = 2,
        per_request_delay_seconds: float = 0.0,
        trace_hook: TautulliTraceHook | None = None,
        history_rows_hook: HistoryRowsHook | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_parallel_servers = max(max_parallel_servers, 1)
        self.per_request_delay_seconds = max(per_request_delay_seconds, 0.0)
        self._trace_hook = trace_hook
        self._history_rows_hook = history_rows_hook

    def _trace_exchange(self, server: TautulliServer, cmd: str, http_status: int | None, ok: bool) -> None:
        hook = self._trace_hook
        if not hook:
            return
        try:
            hook(server, cmd, http_status, ok)
        except Exception:
            logger.debug("Tautulli trace_hook failed", exc_info=True)

    async def fetch_all_activity(self, servers: list[TautulliServer]) -> list[ActivityFetchResult]:
        """Fetch activity from each configured server in parallel."""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            semaphore = asyncio.Semaphore(self.max_parallel_servers)
            tasks = [
                self._run_with_limits(
                    semaphore=semaphore,
                    coro=self._fetch_activity(client=client, server=server),
                )
                for server in servers
            ]
            return await _gather_safe(
                tasks=tasks,
                fallback_factory=lambda err: ActivityFetchResult(
                    server_id="unknown",
                    server_name="Unknown",
                    status="internal_error",
                    error=str(err),
                ),
            )

    async def fetch_all_history_crawled(
        self,
        servers: list[TautulliServer],
        *,
        user: str | None = None,
        media_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        page_size: int = 25,
        inter_page_delay_seconds: float = 0.0,
        max_rows_per_server: int = 100_000,
        stop_before_epoch: int | None = None,
    ) -> list[HistoryFetchResult]:
        """
        Page through get_history per server until exhausted, trimmed, or capped.

        Each HTTP request honors semaphore concurrency and per-request delay. Extra
        inter_page_delay_seconds sleeps between pages on a single server (gentle crawl).
        """
        crawl_page = _clamp_get_history_length(page_size)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            semaphore = asyncio.Semaphore(self.max_parallel_servers)
            tasks = [
                self._crawl_history_for_server(
                    client=client,
                    semaphore=semaphore,
                    server=server,
                    user=user,
                    media_type=media_type,
                    after=after,
                    before=before,
                    page_size=crawl_page,
                    inter_page_delay_seconds=max(inter_page_delay_seconds, 0.0),
                    max_rows_per_server=max(max_rows_per_server, 1),
                    stop_before_epoch=stop_before_epoch,
                )
                for server in servers
            ]
            return await _gather_safe(
                tasks=tasks,
                fallback_factory=lambda err: HistoryFetchResult(
                    server_id="unknown",
                    server_name="Unknown",
                    status="internal_error",
                    error=str(err),
                ),
            )

    async def _fetch_history_page_limited(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        server: TautulliServer,
        start: int,
        length: int,
        user: str | None,
        media_type: str | None,
        after: str | None = None,
        before: str | None = None,
    ) -> HistoryFetchResult:
        async with semaphore:
            if self.per_request_delay_seconds > 0:
                await asyncio.sleep(self.per_request_delay_seconds)
            return await self._fetch_history(
                client=client,
                server=server,
                start=start,
                length=length,
                user=user,
                media_type=media_type,
                after=after,
                before=before,
            )

    async def _crawl_history_for_server(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        server: TautulliServer,
        user: str | None,
        media_type: str | None,
        after: str | None,
        before: str | None,
        page_size: int,
        inter_page_delay_seconds: float,
        max_rows_per_server: int,
        stop_before_epoch: int | None,
    ) -> HistoryFetchResult:
        combined_rows: list[dict] = []
        offset = 0
        page_index = 0
        last_meta: HistoryFetchResult | None = None
        while len(combined_rows) < max_rows_per_server:
            if page_index > 0 and inter_page_delay_seconds > 0:
                await asyncio.sleep(inter_page_delay_seconds)
            chunk = await self._fetch_history_page_limited(
                client=client,
                semaphore=semaphore,
                server=server,
                start=offset,
                length=page_size,
                user=user,
                media_type=media_type,
                after=after,
                before=before,
            )
            last_meta = chunk
            if chunk.status != "ok":
                return HistoryFetchResult(
                    server_id=server.id,
                    server_name=server.name,
                    status=chunk.status,
                    rows=combined_rows,
                    records_filtered=chunk.records_filtered,
                    records_total=chunk.records_total,
                    error=chunk.error,
                )
            batch = [row for row in chunk.rows if isinstance(row, dict)]
            if not batch:
                break
            kept, hit_cutoff = _history_rows_until_cutoff(batch, stop_before_epoch)
            room = max_rows_per_server - len(combined_rows)
            if room <= 0:
                break
            combined_rows.extend(kept[:room])
            if hit_cutoff or len(kept) < len(batch):
                break
            if len(batch) < page_size:
                break
            offset += len(batch)
            page_index += 1
        return HistoryFetchResult(
            server_id=server.id,
            server_name=server.name,
            status="ok",
            rows=combined_rows,
            records_filtered=last_meta.records_filtered if last_meta else None,
            records_total=last_meta.records_total if last_meta else None,
        )

    async def _fetch_activity(self, client: httpx.AsyncClient, server: TautulliServer) -> ActivityFetchResult:
        """Fetch and normalize activity data for one server."""
        params = {"apikey": server.api_key, "cmd": "get_activity"}
        started_at = perf_counter()
        try:
            response = await client.get(server.api_endpoint, params=params)
            self._trace_exchange(server, "get_activity", response.status_code, response.is_success)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            elapsed_ms = _elapsed_ms(started_at)
            logger.warning(
                "Upstream timeout for get_activity",
                extra={
                    "server_id": server.id,
                    "server_name": server.name,
                    "cmd": "get_activity",
                    "timeout_seconds": self.timeout_seconds,
                    "elapsed_ms": elapsed_ms,
                },
            )
            self._trace_exchange(server, "get_activity", None, False)
            return ActivityFetchResult(
                server_id=server.id,
                server_name=server.name,
                status="timeout",
                error=f"Timed out after {self.timeout_seconds}s",
            )
        except httpx.HTTPStatusError as exc:
            elapsed_ms = _elapsed_ms(started_at)
            logger.warning(
                "Upstream HTTP error for get_activity",
                extra={
                    "server_id": server.id,
                    "server_name": server.name,
                    "cmd": "get_activity",
                    "status_code": exc.response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "request_url": _redact_url(str(exc.request.url)),
                },
            )
            self._trace_exchange(
                server,
                "get_activity",
                exc.response.status_code if exc.response is not None else None,
                False,
            )
            return ActivityFetchResult(
                server_id=server.id,
                server_name=server.name,
                status="http_error",
                error=f"HTTP {exc.response.status_code}",
            )
        except (httpx.RequestError, ValueError) as exc:
            elapsed_ms = _elapsed_ms(started_at)
            self._trace_exchange(server, "get_activity", None, False)
            sanitized = _sanitize_error_message(str(exc))
            logger.warning(
                "Upstream request/parsing error for get_activity",
                extra={
                    "server_id": server.id,
                    "server_name": server.name,
                    "cmd": "get_activity",
                    "elapsed_ms": elapsed_ms,
                    "error": sanitized,
                },
            )
            return ActivityFetchResult(
                server_id=server.id,
                server_name=server.name,
                status="request_error",
                error=sanitized,
            )

        response_meta = payload.get("response", {})
        if response_meta.get("result") != "success":
            elapsed_ms = _elapsed_ms(started_at)
            message = response_meta.get("message", "Unknown Tautulli API error")
            logger.warning(
                "Upstream API error for get_activity",
                extra={
                    "server_id": server.id,
                    "server_name": server.name,
                    "cmd": "get_activity",
                    "elapsed_ms": elapsed_ms,
                    "message": message,
                },
            )
            self._trace_exchange(server, "get_activity", response.status_code, False)
            return ActivityFetchResult(
                server_id=server.id,
                server_name=server.name,
                status="upstream_error",
                error=message,
            )

        data = response_meta.get("data", {})
        sessions = data.get("sessions") or []
        if not isinstance(sessions, list):
            sessions = []

        elapsed_ms = _elapsed_ms(started_at)
        logger.info(
            "Upstream get_activity succeeded",
            extra={
                "server_id": server.id,
                "server_name": server.name,
                "cmd": "get_activity",
                "elapsed_ms": elapsed_ms,
                "session_count": len(sessions),
            },
        )
        return ActivityFetchResult(
            server_id=server.id,
            server_name=server.name,
            status="ok",
            sessions=sessions,
        )

    async def _fetch_history(
        self,
        client: httpx.AsyncClient,
        server: TautulliServer,
        start: int,
        length: int,
        user: str | None,
        media_type: str | None,
        after: str | None = None,
        before: str | None = None,
    ) -> HistoryFetchResult:
        """Fetch and normalize history data for one server."""
        base_params: dict[str, str | int] = {
            "apikey": server.api_key,
            "cmd": "get_history",
            "start": max(start, 0),
            "order_column": "date",
            "order_dir": "desc",
        }
        if user:
            base_params["user"] = user
        if media_type:
            base_params["media_type"] = media_type
        if after:
            base_params["after"] = after
        if before:
            base_params["before"] = before

        requested_length = _clamp_get_history_length(length)
        attempt_lengths: list[int] = [requested_length]
        if requested_length > 60:
            attempt_lengths.append(max(60, requested_length // 2))
        if requested_length > 25:
            attempt_lengths.append(25)

        payload: dict | None = None
        timeout_attempted = False
        started_at = perf_counter()
        last_history_http_status: int | None = None
        for attempt_length in _dedupe_preserve_order(attempt_lengths):
            params = dict(base_params)
            params["length"] = attempt_length
            try:
                response = await client.get(server.api_endpoint, params=params)
                last_history_http_status = response.status_code
                self._trace_exchange(server, "get_history", response.status_code, response.is_success)
                response.raise_for_status()
                payload = response.json()
                logger.info(
                    "Upstream get_history attempt succeeded",
                    extra={
                        "server_id": server.id,
                        "server_name": server.name,
                        "cmd": "get_history",
                        "attempt_length": attempt_length,
                        "elapsed_ms": _elapsed_ms(started_at),
                    },
                )
                break
            except httpx.TimeoutException:
                timeout_attempted = True
                self._trace_exchange(server, "get_history", None, False)
                logger.warning(
                    "Upstream timeout for get_history attempt (%.0fs httpx limit per try; raise "
                    "HISTORY_REQUEST_TIMEOUT_SECONDS or History request timeout in /settings if this is frequent).",
                    self.timeout_seconds,
                    extra={
                        "server_id": server.id,
                        "server_name": server.name,
                        "cmd": "get_history",
                        "attempt_length": attempt_length,
                        "timeout_seconds": self.timeout_seconds,
                        "elapsed_ms": _elapsed_ms(started_at),
                    },
                )
                continue
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Upstream HTTP error for get_history",
                    extra={
                        "server_id": server.id,
                        "server_name": server.name,
                        "cmd": "get_history",
                        "status_code": exc.response.status_code,
                        "elapsed_ms": _elapsed_ms(started_at),
                        "request_url": _redact_url(str(exc.request.url)),
                    },
                )
                self._trace_exchange(
                    server,
                    "get_history",
                    exc.response.status_code if exc.response is not None else None,
                    False,
                )
                return HistoryFetchResult(
                    server_id=server.id,
                    server_name=server.name,
                    status="http_error",
                    error=f"HTTP {exc.response.status_code}",
                )
            except (httpx.RequestError, ValueError) as exc:
                sanitized = _sanitize_error_message(str(exc))
                self._trace_exchange(server, "get_history", None, False)
                logger.warning(
                    "Upstream request/parsing error for get_history",
                    extra={
                        "server_id": server.id,
                        "server_name": server.name,
                        "cmd": "get_history",
                        "elapsed_ms": _elapsed_ms(started_at),
                        "error": sanitized,
                    },
                )
                return HistoryFetchResult(
                    server_id=server.id,
                    server_name=server.name,
                    status="request_error",
                    error=sanitized,
                )

        if payload is None:
            timeout_suffix = " after reduced-window retries" if timeout_attempted else ""
            logger.warning(
                "Upstream timeout for get_history after retries (%.0fs per try%s). "
                "Increase HISTORY_REQUEST_TIMEOUT_SECONDS if Tautulli is slow for large history windows.",
                self.timeout_seconds,
                timeout_suffix,
                extra={
                    "server_id": server.id,
                    "server_name": server.name,
                    "cmd": "get_history",
                    "timeout_seconds": self.timeout_seconds,
                    "elapsed_ms": _elapsed_ms(started_at),
                },
            )
            self._trace_exchange(server, "get_history", None, False)
            return HistoryFetchResult(
                server_id=server.id,
                server_name=server.name,
                status="timeout",
                error=f"Timed out after {self.timeout_seconds}s{timeout_suffix}",
            )

        response_meta = payload.get("response", {})
        if response_meta.get("result") != "success":
            message = response_meta.get("message", "Unknown Tautulli API error")
            logger.warning(
                "Upstream API error for get_history",
                extra={
                    "server_id": server.id,
                    "server_name": server.name,
                    "cmd": "get_history",
                    "elapsed_ms": _elapsed_ms(started_at),
                    "message": message,
                },
            )
            self._trace_exchange(
                server,
                "get_history",
                last_history_http_status,
                False,
            )
            return HistoryFetchResult(
                server_id=server.id,
                server_name=server.name,
                status="upstream_error",
                error=message,
            )

        data = response_meta.get("data", {})
        rows = data.get("data") or []
        if not isinstance(rows, list):
            rows = []

        logger.info(
            "Upstream get_history succeeded",
            extra={
                "server_id": server.id,
                "server_name": server.name,
                "cmd": "get_history",
                "elapsed_ms": _elapsed_ms(started_at),
                "row_count": len(rows),
            },
        )
        hr_hook = self._history_rows_hook
        if hr_hook:
            try:
                hr_hook(server, len(rows))
            except Exception:
                logger.debug("history_rows_hook failed", exc_info=True)
        return HistoryFetchResult(
            server_id=server.id,
            server_name=server.name,
            status="ok",
            rows=rows,
            records_filtered=_as_int_or_none(data.get("recordsFiltered")),
            records_total=_as_int_or_none(data.get("recordsTotal")),
        )

    async def _run_with_limits(self, semaphore: asyncio.Semaphore, coro: Awaitable):
        async with semaphore:
            if self.per_request_delay_seconds > 0:
                await asyncio.sleep(self.per_request_delay_seconds)
            return await coro


T = TypeVar("T")


async def _gather_safe(tasks: list[Awaitable[T]], fallback_factory: Callable[[Exception], T]) -> list[T]:
    """Gather task results with exception capture for resilience."""
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[T] = []
    for result in raw_results:
        if not isinstance(result, Exception):
            results.append(result)
            continue
        # If anything unexpected escapes per-server fetch, keep other results.
        results.append(fallback_factory(result))
    return results


def _as_int_or_none(value: object) -> int | None:
    """Best-effort integer conversion for upstream metadata fields."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_preserve_order(values: list[int]) -> list[int]:
    """Return unique values preserving first-seen order."""
    seen: set[int] = set()
    deduped: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


def _redact_url(raw_url: str) -> str:
    """Redact sensitive query parameters in URL strings."""
    try:
        parts = urlsplit(raw_url)
        redacted_query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() == "apikey":
                redacted_query.append((key, "***REDACTED***"))
            else:
                redacted_query.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted_query), parts.fragment))
    except Exception:
        return raw_url.replace("apikey=", "apikey=***REDACTED***")


def _sanitize_error_message(message: str) -> str:
    """Best-effort redaction for exception text that may include URLs."""
    return _redact_url(message).replace("apikey=", "apikey=***REDACTED***")
