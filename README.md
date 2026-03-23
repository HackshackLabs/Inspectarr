# Insecpectarr

Small "single pane of glass" dashboard that aggregates activity and history from multiple Tautulli instances.

## What it does

- Fans out read requests to multiple Tautulli servers.
- Merges live activity into one view while retaining `server_id` per row.
- Merges history into a single timeline view.
- Shows per-server health so one failed node does not blank the dashboard.
- Front page includes visual stream summaries by server and media type.

## Current status

MVP application skeleton is in place with:

- FastAPI app entrypoint under `src/tautulli_inspector/main.py`
- Environment-driven settings loader
- Tautulli fan-out client for `get_activity` and `get_history`
- Merged live dashboard page at `/`
- Merged history page at `/history` with global ordering and filters
- Stale insights page at `/insights/unwatched`
- Library-joined unwatched page at `/insights/library-unwatched` with incremental indexing progress
- Settings UI at `/settings` (themes, site title, logo, Tautulli/Sonarr and timing options via JSON config file)

## Planned stack

- Python
- FastAPI
- Jinja2 templates
- Optional htmx for lightweight auto-refresh

## Prerequisites

- Python 3.11+ recommended
- Network access from this app to each Tautulli server
- A valid Tautulli API key for each configured server

## Configuration

Copy `.env.example` to `.env` and set values for your environment.

Required variables:

- `HOST`
- `PORT`
- `REQUEST_TIMEOUT_SECONDS`
- `HISTORY_REQUEST_TIMEOUT_SECONDS`
- `UPSTREAM_MAX_PARALLEL_SERVERS` (throttle max simultaneous server fan-out)
- `UPSTREAM_PER_REQUEST_DELAY_SECONDS` (small delay before each server request)
- `ACTIVITY_TIMEOUT_RETRY_SECONDS` (retry delay for timed-out live activity servers)
- `HISTORY_TIMEOUT_RETRY_SECONDS` (retry delay for timed-out history fetches when the history page cache is enabled)
- `ACTIVITY_CACHE_TTL_SECONDS`
- `ACTIVITY_CACHE_STALE_SECONDS`
- `HISTORY_CACHE_DB_PATH` (optional; empty disables SQLite cache)
- `HISTORY_CACHE_TTL_SECONDS` (optional; applies when SQLite cache is enabled)
- `HISTORY_DEFAULT_WEEK_DAYS` (default rolling window for `/history` week mode, UTC)
- `HISTORY_ADDITIONAL_PER_REQUEST_DELAY_SECONDS` (extra delay before each `get_history`, added to upstream delay)
- `HISTORY_WEEK_PAGE_SIZE`, `HISTORY_WEEK_INTER_PAGE_DELAY_SECONDS`, `HISTORY_WEEK_MAX_ROWS_PER_SERVER` (week-mode crawl tuning)
- `HISTORY_FULL_PAGE_SIZE`, `HISTORY_FULL_INTER_PAGE_DELAY_SECONDS`, `HISTORY_FULL_MAX_ROWS_PER_SERVER`, `HISTORY_FULL_MAX_PARALLEL_SERVERS` (all-time crawl tuning; default parallel 1)
- `INSIGHTS_HISTORY_LENGTH` (history rows fetched per server for insights reports)
- `TV_INVENTORY_MAX_SHOWS_PER_SERVER` (show cap per server for library inventory traversal)
- `TV_INVENTORY_BATCH_SHOWS_PER_SERVER` (incremental shows/server/request for library indexing)
- `INVENTORY_CACHE_DB_PATH` (SQLite store for incremental inventory index)
- `INSIGHTS_CACHE_DB_PATH` (SQLite store for cached insights snapshots)
- `INSIGHTS_CACHE_TTL_SECONDS` (insights cache TTL; default 3 hours)
- `DASHBOARD_CONFIG_PATH` (optional JSON for presentation + settings overrides; default `./data/dashboard_config.json`)
- `TAUTULLI_SERVERS_JSON`

Optional Sonarr (Library Unwatched actions only):

- `SONARR_BASE_URL` (empty disables Sonarr UI and API routes)
- `SONARR_API_KEY`
- `SONARR_REQUEST_TIMEOUT_SECONDS` (optional, default 15)

Timeout guidance:

- Use `REQUEST_TIMEOUT_SECONDS` for lightweight calls (for example live activity).
- Use `HISTORY_REQUEST_TIMEOUT_SECONDS` for heavier history queries.
- Reduce upstream pressure with:
  - `UPSTREAM_MAX_PARALLEL_SERVERS` (lower = gentler, slower)
  - `UPSTREAM_PER_REQUEST_DELAY_SECONDS` (higher = gentler, slower)
- Live activity timeout recovery:
  - timed-out servers auto-schedule retries with backoff:
    - `ACTIVITY_TIMEOUT_RETRY_SECONDS` (base, default 30s)
    - then 60s, then 120s for consecutive timeout snapshots
  - dashboard shows countdown until next retry attempt
- History page timeout recovery (when `HISTORY_CACHE_DB_PATH` is set):
  - uses `HISTORY_TIMEOUT_RETRY_SECONDS` with the same 30s / 60s / 120s backoff pattern per cached filter snapshot
  - `/history` shows a countdown until the next background refetch attempt
- Live activity uses stale-while-revalidate cache:
  - `ACTIVITY_CACHE_TTL_SECONDS` defines fresh-hit lifetime (default 300s / 5 minutes).
  - `ACTIVITY_CACHE_STALE_SECONDS` defines additional stale-serve window while background refresh runs.
- History page can use optional SQLite page cache:
  - set `HISTORY_CACHE_DB_PATH` to a writable path (for example `./data/history_cache.sqlite`)
  - `HISTORY_CACHE_TTL_SECONDS` controls cache expiry
  - cold/expired snapshots are refreshed in background and page auto-refreshes while pending
  - if `HISTORY_CACHE_DB_PATH` is empty, each `/history` load runs a full compute inline (no background cache); server health cards still render
- `/history` time range:
  - default **week** mode sends Tautulli `after` for the last `HISTORY_DEFAULT_WEEK_DAYS` UTC days and pages gently within that window
  - **all** mode omits `after` (unless you set start/end dates) and walks the full history with small pages, long inter-page delays, and `HISTORY_FULL_MAX_PARALLEL_SERVERS` (default 1); each server stops at `HISTORY_FULL_MAX_ROWS_PER_SERVER`
  - every `get_history` still uses `UPSTREAM_PER_REQUEST_DELAY_SECONDS` plus `HISTORY_ADDITIONAL_PER_REQUEST_DELAY_SECONDS`
- Unwatched insights use history-indexed data:
  - `INSIGHTS_HISTORY_LENGTH` controls rows fetched per server for indexing
  - items never present in history are not included in stale candidate reports
- Library-unwatched insights join TV inventory and episode history:
  - `TV_INVENTORY_MAX_SHOWS_PER_SERVER` limits traversal work per server
  - identifies shows/seasons/episodes with zero watched episodes in the index window
  - indexing can run incrementally:
    - each request fetches `TV_INVENTORY_BATCH_SHOWS_PER_SERVER` new shows per server section
    - indexed inventory is persisted in `INVENTORY_CACHE_DB_PATH`
    - UI shows per-section indexing progress and completion state
- Insights pages use background snapshot refresh:
  - if cache is cold/expired, page returns quickly in pending state and auto-refreshes
  - when snapshot job completes, page renders cached data
  - default snapshot cache TTL is 3 hours (`INSIGHTS_CACHE_TTL_SECONDS=10800`)

`TAUTULLI_SERVERS_JSON` is a JSON array. Each server object should include:

- `id` (stable machine-friendly identifier)
- `name` (display name)
- `base_url` (e.g. `http://192.168.1.10:8181`)
- `api_key` (Tautulli API key)

Example:

```json
[
  {
    "id": "home-main",
    "name": "Home Plex",
    "base_url": "http://127.0.0.1:8181",
    "api_key": "replace_me"
  }
]
```

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` (or use `.env.local`) and set values.
4. Run the app:
   - `uvicorn tautulli_inspector.main:app --reload`
   - or `tautulli-inspector`

By default, the dashboard is available at `http://127.0.0.1:8000/`.

Pages:

- `GET /` live now-playing and server health
- `GET /settings` browser configuration (themes, title, logo, Tautulli servers JSON, Sonarr, timeouts); `POST /settings` saves to `DASHBOARD_CONFIG_PATH` (see `docs/CONFIGURATION.md`)
- `GET /history` merged history timeline
  - query params: `start`, `length`, `user`, `media_type`, `start_date`, `end_date`, `range_mode` (`week` default or `all`), `refresh`
  - per-server status cards are sorted by configured display name; when the history SQLite cache is enabled and any server is `timeout`, the page shows a countdown to the next automatic refetch (see `HISTORY_TIMEOUT_RETRY_SECONDS`)
- `GET /insights/unwatched` stale media insights (per-server and cumulative)
  - query params: `media_type` (`episode|movie`), `days`, `max_items`, `refresh`
  - exports (cached full lists, not HTML page slices): `GET /insights/unwatched/export?group=cumulative_stale|server_stale&format=txt|csv|json|xml&media_type=…&days=…` (add `server_id` for `server_stale`)
- `GET /insights/library-unwatched` TV inventory joined with index-window watch history
  - query params: `show_start`, `season_start`, `episode_start`, `server_start`, `length`, `max_items`, `refresh`
  - cumulative shows/seasons/episodes appear side-by-side in columns on wide viewports; per-server unwatched inventory is collapsed by default (expand to view)
  - exports (cached full lists): `GET /insights/library-unwatched/export?group=cumulative_shows|cumulative_seasons|cumulative_episodes|server_shows|server_seasons|server_episodes&format=txt|csv|json|xml` (add `server_id` for `server_*` groups)
  - show-level unwatched lists require no recorded watch evidence ever (episode play/view metadata or watched history)
  - season-level unwatched lists exclude seasons with any in-window watch evidence (including parsed `SxEy` title formats)
  - timed-out/incomplete server index states auto-schedule background retries and surface retry status in UI
  - cumulative lists are globally deduped and exclude items watched on any server in the window
  - optional Sonarr column (when `SONARR_BASE_URL` and `SONARR_API_KEY` are set): each row loads **monitored** + **file count** inline; **ⓘ** shows paths; **Unmonitor**, **Remove & unmonitor**, and **Delete** (see `docs/SONARR.md`)
  - Sonarr API (proxied by this app): `GET /insights/library-unwatched/sonarr/status` (includes `file_count`), `POST …/sonarr/unmonitor`, `POST …/sonarr/remove-from-plex-and-unmonitor`, `POST …/sonarr/delete`
  - optional Plex ( **`/settings`**, primary/secondary tokens): on **per-server** tables, **Remove & unmonitor** and **Delete** call Sonarr then `POST /insights/library-unwatched/plex/delete-library-item` when the row has a `ratingKey` and Plex is mapped for that server. Cumulative tables are Sonarr-only.

## Run tests

- `PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"`

## Docker

Build:

- `docker build -t tautulli-inspector:latest .`

Run:

- `docker run --rm -p 8000:8000 --env-file .env.local tautulli-inspector:latest`

## Security notes

- Never commit real API keys, `.env`, or other secrets.
- **HTTP Basic auth** is on by default (`BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` in `.env`; default password must be changed for any non-local use). **`GET /healthz`** stays unauthenticated for probes. Set **`BASIC_AUTH_ENABLED=false`** to disable.
- Keep dashboard bound to localhost for MVP (`127.0.0.1`) unless protected by a reverse proxy and auth.
- Treat upstream Tautulli endpoints as sensitive operational surfaces.
- Sonarr actions can unmonitor and delete files on disk; Plex actions can delete library items (and often media files) on your PMS; protect the dashboard the same way as Tautulli keys if exposed beyond localhost.
- `/settings` can write API keys and operational tuning to disk (`DASHBOARD_CONFIG_PATH`); restrict network access or add proxy auth.
- Upstream request/error logging redacts `apikey`; raw upstream URLs should not be logged.
- For non-local deployment, put the app behind a reverse proxy with TLS and auth (basic auth or SSO forward-auth).

## Docs

- `UIDESIGN.md` — UI design principles for contributors and agents
- `docs/UI_IMPROVEMENTS.md` — Site review vs those principles; prioritized UI backlog
- **UI implementation:** shared tokens, focus styles, skip link, and nav live in `src/tautulli_inspector/templates/layout.html` (`nav_current` is set per route in `routes_dashboard.py` and `routes_configuration.py`)
- `docs/ARCHITECTURE.md`
- `docs/CONFIGURATION.md`
- `docs/SONARR.md`
- `docs/TAUTULLI_API.md`
- `docs/DECISIONS.md`
- `docs/DEPLOYMENT.md`
- `docs/KNOWN_ISSUES.md`
- `TODO.md`
