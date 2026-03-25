# Scoparr

Small “single pane of glass” dashboard that aggregates **live activity** and **merged history** from multiple [Tautulli](https://tautulli.com/) instances, plus a **Sonarr + Tautulli** view for long-unwatched TV library rows (optional Sonarr / Plex / Overseerr actions).

In the UI, the main areas are labeled **Deck Watch** (live), **Broadside Range** (history), and **Horizon Watch** (stale-library insight).

## What it does

- Fans out read requests to multiple Tautulli servers.
- Merges live activity into one view while retaining `server_id` per row.
- Merges history into a single timeline view (week or all-time crawl modes).
- Shows per-server health so one failed node does not blank the dashboard.
- Deck Watch includes stream summaries by server and media type.
- Horizon Watch joins Sonarr series inventory to merged Tautulli history (all-time crawl capped per server) to surface “stale” shows; optional exports, Sonarr controls, Plex delete chaining, and Overseerr card extras when configured.

## Features (shipped)

| UI name | Path | Notes |
|--------|------|--------|
| **Deck Watch** | `/` | Merged `get_activity`, per-server health, stream summaries |
| **Broadside Range** | `/history` | Global merge-sort timeline, filters, optional SQLite page cache, `range_mode=week` (default) or `all` |
| **Horizon Watch** | `/insights/stale-library` | Stale-library cards, JSON API, export (JSON/CSV/TXT/XML), Sonarr + optional Plex chain |
| **Settings** | `/settings` | Themes, branding, `TAUTULLI_SERVERS_JSON`, Sonarr / Overseerr / Plex, tuning overrides → `DASHBOARD_CONFIG_PATH` |
| **Plex sign-in** | `/settings/plex-auth/*` | Pin flow + token check (used from Settings) |

Stack: Python 3.11+, FastAPI, Jinja2 (server-rendered UI).

## Prerequisites

- Python 3.11+
- Network access from this app to each Tautulli server (and optionally Sonarr, Plex, Overseerr)
- A valid Tautulli API key for each configured server

## Configuration

Copy `.env.example` to `.env` and adjust for your environment. **Authoritative names and comments** for variables live in `.env.example` and in `scoparr.settings` (`src/scoparr/settings.py`).

**Minimum to run**

- `TAUTULLI_SERVERS_JSON` — JSON array of `{ id, name, base_url, api_key }`
- If HTTP Basic auth is enabled (default): `BASIC_AUTH_USERNAME`, `BASIC_AUTH_PASSWORD` (change the default password for any non-local use)

**Groups of settings** (non-exhaustive; see `.env.example`)

- **Binding:** `HOST`, `PORT`
- **Auth / probes:** `BASIC_AUTH_*`, `HEALTHZ_TOKEN`, `BLOCK_PRIVATE_UPSTREAM_URLS`
- **Upstream:** `REQUEST_TIMEOUT_SECONDS`, `HISTORY_REQUEST_TIMEOUT_SECONDS`, `UPSTREAM_MAX_PARALLEL_SERVERS`, `UPSTREAM_PER_REQUEST_DELAY_SECONDS`, timeout retry intervals for activity and history cache
- **Live activity cache:** `ACTIVITY_CACHE_TTL_SECONDS`, `ACTIVITY_CACHE_STALE_SECONDS`
- **History crawl:** `HISTORY_DEFAULT_WEEK_DAYS`, week vs full crawl sizes/delays/limits, optional `HISTORY_CACHE_DB_PATH` + `HISTORY_CACHE_TTL_SECONDS`
- **Horizon Watch snapshot:** `STALE_LIBRARY_CACHE_PATH`, `STALE_LIBRARY_CACHE_TTL_SECONDS`, `LIBRARY_UNWATCHED_HISTORY_EXTRA_DELAY_SECONDS`, `TV_INVENTORY_REQUEST_TIMEOUT_SECONDS`
- **Optional integrations:** `SONARR_*`, `OVERSEERR_*`, `PLEX_*`, `DASHBOARD_CONFIG_PATH`

### Timeout and gentleness

- Use `REQUEST_TIMEOUT_SECONDS` for lighter calls (e.g. live activity) and `HISTORY_REQUEST_TIMEOUT_SECONDS` for `get_history`.
- Reduce pressure with lower `UPSTREAM_MAX_PARALLEL_SERVERS` and/or higher `UPSTREAM_PER_REQUEST_DELAY_SECONDS` (and history-specific delays).
- Live activity uses stale-while-revalidate (`ACTIVITY_CACHE_*`). History can use an optional SQLite page cache when `HISTORY_CACHE_DB_PATH` is set; timed-out servers can show a countdown and background retry when that cache is enabled.

### `TAUTULLI_SERVERS_JSON`

Each server object should include:

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

1. Copy `.env.example` to `.env` and set at least `TAUTULLI_SERVERS_JSON` and auth-related variables.
2. Install and run (pick one):

   **uv (recommended)**

   ```bash
   uv sync
   uv run uvicorn scoparr.main:app --reload --host 127.0.0.1 --port 8000
   ```

   Or the installed console script (uses `HOST` / `PORT` from settings, `reload=True`):

   ```bash
   uv run scoparr
   ```

   **pip**

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -e .
   uvicorn scoparr.main:app --reload --host 127.0.0.1 --port 8000
   ```

Open `http://127.0.0.1:8000/` by default.

### Main HTTP routes

- `GET /` — Deck Watch (live activity, health, stream summaries)
- `GET /history` — Broadside Range (merged history). Query params include `start`, `length`, `user`, `media_type`, `start_date`, `end_date`, `range_mode` (`week` | `all`), `refresh`
- `GET /insights/stale-library` — Horizon Watch (HTML)
- `GET /insights/stale-library/api/data` — paginated JSON for the UI
- `GET /insights/stale-library/api/export?format=json|csv|txt|xml` — full snapshot export
- `POST /insights/stale-library/api/refresh` — invalidate and rebuild snapshot (rate limited)
- `GET /insights/stale-library/api/upstream` — live upstream trace while a build runs
- `POST /insights/stale-library/sonarr/*` — Sonarr actions from the UI (rate limited); optional Plex chain on destructive actions when configured
- `GET /settings`, `POST /settings` — configuration form and save
- `POST /settings/plex-auth/start`, `POST /settings/plex-auth/check`, `GET /settings/plex-auth/validate` — Plex token flow
- `GET /healthz` — health check (see `BASIC_AUTH_ENABLED` / `HEALTHZ_TOKEN`)

See `docs/CONFIGURATION.md` for dashboard JSON and override behavior.

## Run tests

```bash
uv run pytest
```

## Docker

Build:

```bash
docker build -t scoparr:latest .
```

Run:

```bash
docker run --rm -p 8000:8000 --env-file .env.local scoparr:latest
```

## Security notes

- Never commit real API keys, `.env`, or other secrets.
- **HTTP Basic auth** is on by default (`BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` in `.env`). **`GET /healthz`** stays unauthenticated for probes unless `HEALTHZ_TOKEN` is set. Set **`BASIC_AUTH_ENABLED=false`** for open local dev only.
- Keep the app bound to localhost unless it sits behind a reverse proxy with TLS and auth.
- Treat upstream Tautulli (and Sonarr/Plex) endpoints as sensitive operational surfaces.
- Sonarr actions can unmonitor and delete files on disk; Plex chaining can remove library items on your PMS. Protect the dashboard like any admin tool.
- `/settings` can write API keys and tuning to `DASHBOARD_CONFIG_PATH`; restrict network access accordingly.
- For non-local deployment, use a reverse proxy with TLS and authentication.

## Docs

| Doc | Purpose |
|-----|---------|
| `docs/ARCHITECTURE.md` | Data flow, merge rules, caching |
| `docs/CONFIGURATION.md` | Environment variables and dashboard JSON overrides |
| `docs/SONARR.md` | Sonarr matching and API semantics (where applicable to Horizon Watch) |
| `docs/PLEX_API_LIBRARY_REMOVAL.md` | Plex delete chaining from stale-library / Sonarr flows |
| `docs/TAUTULLI_API.md` | Tautulli endpoints used by the client |
| `docs/DEPLOYMENT.md` | Reverse proxy, TLS, container notes |
| `docs/KNOWN_ISSUES.md` | Risk register |
| `docs/DECISIONS.md` | Design decisions |
| `SECURITY_REVIEW.md` | Security review summary |
| `docs/UI_IMPROVEMENTS.md` | UI review backlog |
| `TODO.md` | Maintainer checklist (may include historical items) |

Shared layout, nav, and theme tokens: `src/scoparr/templates/layout.html` (`nav_current` from `routes_dashboard.py` / `routes_configuration.py` / `routes_stale_library.py`).
