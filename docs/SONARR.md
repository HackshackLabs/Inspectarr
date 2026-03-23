# Sonarr integration (Library Unwatched)

When `SONARR_BASE_URL` and `SONARR_API_KEY` are set, `/insights/library-unwatched` shows a **Sonarr** column for each show, season, and episode row. Each row loads **monitored** state and a **file count** (Sonarr episode files on disk for that scope) inline; **Unmonitor** and **Remove & unmonitor** stay visible next to **ⓘ** (paths tooltip). The UI proxies your browser to this app, which calls Sonarr’s HTTP API (v3-style paths under `/api/v3/`). The app caches the Sonarr series list briefly (about 45s) to avoid downloading `/api/v3/series` once per table row; the cache is cleared after successful write actions.

## Configuration

| Variable | Purpose |
|----------|---------|
| `SONARR_BASE_URL` | Sonarr root URL (no trailing slash required), e.g. `http://127.0.0.1:8989` |
| `SONARR_API_KEY` | Sonarr **Settings → General → Security → API Key** |
| `SONARR_REQUEST_TIMEOUT_SECONDS` | HTTP timeout for Sonarr calls (default `15`) |

Sonarr URL/key can be set in the environment or in the dashboard JSON `overrides` section (via **`/settings`**). Merged settings are recomputed on each request from env + JSON; only the **environment** layer is LRU-cached per process, so changing `.env` still requires a restart. Editing Sonarr fields in `/settings` updates the JSON file and applies on the next HTTP request without restart.

## Matching Plex rows to Sonarr

Rows prefer a **TVDB id** parsed from Plex/Tautulli metadata (`guid` / `grandparent_guid` with the TheTVDB agent). If TVDB is missing, the UI still sends **series title** (show title or episode grandparent title) so Sonarr can match by `title` / `cleanTitle`. The cell shows **—** only when there is neither a usable TVDB id nor a non-empty title.

For the most reliable matches, use TheTVDB as the metadata agent in Plex for TV libraries.

## HTTP endpoints (same origin as the dashboard)

### `GET /insights/library-unwatched/sonarr/status`

Query parameters:

- `kind` — `show` | `season` | `episode` (required)
- `tvdb_id` — optional integer
- `series_title` — optional fallback if `tvdb_id` is absent
- `season_number` — required for `season` / `episode`
- `episode_number` — required for `episode`

Returns JSON used by the page and hover popup:

- `series_found`, `monitored` (boolean or `null` when mixed/unknown), `file_count` (integer: for **show**, count of episodes that have an `episodeFile` in Sonarr; for **season**, count of on-disk files in that season; for **episode**, `0` or `1`)
- `paths`: list of folder or file paths from Sonarr when available
- optional `message` for lookup failures or caveats

If Sonarr is not configured, returns `sonarr_configured: false` and a short message (HTTP 200).

### `POST /insights/library-unwatched/sonarr/unmonitor`

JSON body:

```json
{
  "kind": "show|season|episode",
  "tvdb_id": 12345,
  "series_title": "Optional if tvdb_id set",
  "season_number": 1,
  "episode_number": 2
}
```

- **show** — sets the series `monitored` flag to `false` in Sonarr.
- **season** — sets all episodes in that season to unmonitored.
- **episode** — sets one episode to unmonitored.

### `POST /insights/library-unwatched/sonarr/remove-from-plex-and-unmonitor`

Same JSON body as unmonitor.

This route **only** calls Sonarr. It:

1. Applies the same unmonitor behavior as above.
2. Deletes managed **episode files** via Sonarr (`DELETE /api/v3/episodefile/{id}`).

**Show** scope unmonitors the series and deletes **all** episode files Sonarr still tracks for that series—this is destructive and the UI asks for confirmation.

On **per-server** Library Unwatched rows, when Plex is configured and the row has a `ratingKey`, the page runs Sonarr first, then **`POST /insights/library-unwatched/plex/delete-library-item`** in the same click so Plex removes the matching library item (see `docs/PLEX_API_LIBRARY_REMOVAL.md`). Cumulative rows have no Plex follow-up (no per-server rating key).

### `POST /insights/library-unwatched/sonarr/delete`

Same JSON body as unmonitor.

- **show** — `DELETE /api/v3/series/{id}` with `deleteFiles=true` (series is removed from Sonarr; import-list exclusion is **not** added).
- **season** — deletes every managed **episode file** in that season via `DELETE /api/v3/episodefile/{id}`; the series remains in Sonarr and **monitored** flags are unchanged (contrast with **remove-from-plex-and-unmonitor**, which unmonitors first).
- **episode** — deletes that episode’s file on disk if present; monitored state is unchanged.

Use **Delete** when you want to drop files (or the whole Sonarr series) without the unmonitor step used by **Remove & unmonitor**.

On **per-server** rows with Plex configured and a `ratingKey`, **Delete** triggers the same Plex delete call as **Remove & unmonitor** after Sonarr succeeds. See `docs/PLEX_API_LIBRARY_REMOVAL.md` for API details.

## Security

These routes are as exposed as the rest of the dashboard. If you bind the app beyond localhost, protect it (reverse proxy, auth). The Sonarr API key is server-side only and never sent to the browser.

## Cumulative vs per-server rows

Cumulative lists are deduplicated across Tautulli servers. Metadata (TVDB id, title) may come from whichever server contributed the row first; Plex/Sonarr actions still target a single logical series in Sonarr. If you need guaranteed per-Plex-server rating keys, use the per-server unwatched tables.
