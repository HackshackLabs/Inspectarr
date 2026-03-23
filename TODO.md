# TODO

## P0 - MVP foundation

- [x] Add initial project documentation scaffold (`README.md`, docs, `.env.example`).
- [x] Create minimal FastAPI app bootstrap (`main.py`) and health route.
- [x] Implement config loader for multi-server definitions and runtime settings.
- [x] Implement Tautulli client with request timeout and basic error mapping.
- [x] Add API key redaction in all request/error logs (never log raw upstream URLs).
- [x] Implement merged live activity aggregation with `server_id` tagging.
- [x] Render initial dashboard page with now-playing table and server health strip.

## P1 - History view

- [x] Implement per-server history fetch and normalized row model.
- [x] Implement global merge-sort timeline strategy for cross-server correctness.
- [x] Normalize history timestamps to a canonical UTC epoch field before merge-sort.
- [x] Add history page with filters (`user`, `media_type`, date range where supported).
- [x] Add pagination behavior consistent with merged timeline semantics.
- [x] Improve UI states for partial failures and empty results.

## P2 - Hardening and polish

- [x] Add local cache/storage option for history (for example SQLite).
- [x] Add short-lived cache/stale-while-revalidate for activity to reduce fan-out polling pressure.
- [x] Add auth strategy for non-local deployments (basic auth/SSO/reverse proxy pattern).
- [x] Add Docker image and container run docs.
- [x] Add lightweight test coverage for aggregation and normalization logic.
- [x] Add observability notes/logging improvements for upstream latency/errors.

## P3 - Analytics extensions

- [x] Add episode/movie stale-watch insights page with per-server and cumulative indexing.
- [x] Add configurable stale window (`days`) and media type filtering for insights.
- [x] Document history-index limitation for "never watched" detection and future inventory integration path.
- [x] Add TV library-inventory join report for shows/seasons/episodes with zero watches in index window.
- [x] Add incremental chunked TV inventory indexing with persistent progress and tuning knobs.
- [x] Fix cumulative TV unwatched dedupe so show/season appears once globally and is excluded if watched on any server in window.
- [x] Standardize server-identification panel sizing/layout across dashboard pages to match history page card grid.
- [x] Add pagination controls to library-unwatched report for browsing full cumulative and per-server result sets.
- [x] Add 3-hour cached background snapshot refresh for insights pages with automatic client-side refresh while pending.
- [x] Add independent pagination cursors for library-unwatched groups (shows, seasons, episodes, per-server).
- [x] Apply background snapshot refresh + auto-reload pending state to history page cache misses/expirations.
- [x] Compact library-unwatched cumulative presentation with collapsible groups and smaller default page size.
- [x] Tighten show-level unwatched semantics: include show only when no episode has any watch record ever.
- [x] Add explicit `refresh=1` force-recompute controls for history and insights pages.
- [x] Add configurable upstream throttling (parallel fan-out cap + per-request delay) to reduce timeout pressure.
- [x] Tighten season-level unwatched matching to exclude seasons with any in-window watch evidence, including `SxEy` full-title parsing fallback.
- [x] Surface retry/throttle visibility on library-unwatched and auto-schedule retries for timed-out/incomplete servers.
- [x] Add front-page visual summaries (streams by server and media type) for faster at-a-glance interpretation.
- [x] Add live-activity timeout retry scheduling with visible countdown until next retry.
- [x] Add live-activity timeout retry backoff escalation (30s -> 60s -> 120s) with reset after healthy snapshot.

## P4 - Plex API (optional)

- [x] Direct Plex delete on per-server Library Unwatched rows + `/settings` Plex mapping, dual tokens (primary/secondary), PIN sign-in (`docs/PLEX_API_LIBRARY_REMOVAL.md`, `docs/CONFIGURATION.md`).
- [ ] Align `docs/PLEX_API_LIBRARY_REMOVAL.md` with current PMS OpenAPI when targeting a specific server version.
- [ ] Optional: mocked `httpx` tests for PMS DELETE; empty-trash / alternate delete endpoints if needed.
