# Dashboard configuration

Beyond environment variables (`.env` / `.env.local`), the app can load a **JSON dashboard config file** and serve a browser **Settings** page to edit most options.

## File location

- Environment variable: `DASHBOARD_CONFIG_PATH` (default `./data/dashboard_config.json`).
- This path is read **only from the environment** (not from the JSON file itself).
- Logos uploaded from Settings are stored under `uploads/` next to that file (e.g. `./data/uploads/`).

## What the file contains

```json
{
  "presentation": {
    "theme": "slate",
    "site_title": "Scoparr",
    "logo_file": "abc123.png",
    "footer_text": "",
    "custom_nav_note": ""
  },
  "overrides": {
    "port": 8000,
    "tautulli_servers": [
      { "id": "s1", "name": "Home", "base_url": "http://127.0.0.1:8181", "api_key": "..." }
    ],
    "sonarr_base_url": "",
    "sonarr_api_key": "",
    "radarr_base_url": "",
    "radarr_api_key": "",
    "plex_servers": [],
    "plex_token_primary": "",
    "plex_token_secondary": "",
    "plex_client_identifier": "",
    "plex_request_timeout_seconds": 30
  }
}
```

- **`presentation`**: UI-only (themes, title, logo filename, footer, optional nav note).
- **`overrides`**: Any subset of `Settings` model fields in `scoparr.settings` (snake_case), merged on top of environment values on each `get_settings()` call.

If a key is **absent** from `overrides`, the value comes from the environment (or pydantic default).

## Settings page

- URL: **`GET /settings`**
- **POST /settings** accepts `multipart/form-data` (same form): saves presentation + overrides, optional logo upload. The Tautulli servers textarea is parsed and validated as `TautulliServer` rows, then written to `overrides.tautulli_servers` as a JSON array of plain objects (so the file stays valid JSON).
- **Themes** (body class `theme-*`): `slate`, `ocean`, `ember`, `forest`, `paper`.
- **Sonarr API key**: leave password blank to keep the previous stored or env value; use **Clear stored Sonarr API key** to drop the key from the JSON file (env may still supply one).
- **Radarr** (Harbor Watch): same pattern as Sonarr — **Radarr base URL**, **request timeout**, optional **Radarr API key** in the Radarr section; blank password keeps the previous JSON or `.env` value; **Clear stored Radarr API key** removes it from the dashboard file only.
- **Plex**: optional JSON array `plex_servers` (`PlexServer`: `id`, `base_url`, `tautulli_server_id`, `token_profile` `primary`|`secondary`). **Sign in with Plex** saves tokens to JSON only — it does not populate `plex_servers`; you still edit that array and **Save**. The settings page shows whether each token is set, where it is stored (JSON vs `.env`), a masked suffix, and **Verify token at Plex.tv** (`GET /settings/plex-auth/validate`). See `docs/PLEX_API_LIBRARY_REMOVAL.md`, `POST /settings/plex-auth/start`, and `GET /settings/plex-auth/check`.

## Security

### HTTP Basic (application)

When **`BASIC_AUTH_ENABLED`** is true (default), every route except **`GET /healthz`** requires a valid **`Authorization: Basic …`** header. Username and password come from **`.env` / environment only** (`BASIC_AUTH_USERNAME`, `BASIC_AUTH_PASSWORD`); defaults are `admin` / `b00tyt@st3r` — **change them** before exposing the app. Dashboard JSON **cannot** override these fields. Set **`BASIC_AUTH_ENABLED=false`** for open local development without a login prompt.

### Settings page

Anyone who passes Basic auth can use **/settings** and change upstream API keys, Plex tokens, and operational tuning stored in the dashboard file. For defense in depth on the public internet, still use TLS (reverse proxy) and consider proxy-level auth (see `docs/DEPLOYMENT.md`).

## Process / env cache

- **Environment** variables are loaded once per process via `_settings_from_env()` (LRU-cached). Changing `.env` still requires an app restart.
- **Dashboard JSON** is re-read when `get_settings()` runs (no in-process cache of the merged result), so edits from `/settings` apply on the next request.

## Docker / persistence

Mount a volume on the directory containing `dashboard_config.json` and `uploads/` so UI-driven changes survive container restarts.

## Optional Overseerr (Cold Storage)

When **Overseerr** is configured, **Cold Storage** expanded series cards can show TV request metadata (request time, requester, when Overseerr marked the title available). Matching uses **Sonarr’s TVDB id** first, then **TMDB id**, against Overseerr’s request list.

Set via **environment** (see `.env.example`) or **`overrides`** in the dashboard JSON (same field names, snake_case):

| Variable / override key | Purpose |
| --- | --- |
| `overseerr_base_url` / `OVERSEERR_BASE_URL` | Base URL of your Overseerr instance (no trailing slash). |
| `overseerr_api_key` / `OVERSEERR_API_KEY` | API key from Overseerr **Settings → General**. |
| `overseerr_request_timeout_seconds` / `OVERSEERR_REQUEST_TIMEOUT_SECONDS` | HTTP timeout for Overseerr calls (default 30). |

If the base URL or API key is empty, Cold Storage skips Overseerr calls and cards omit that block.
