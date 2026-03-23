# Tautulli API Usage

This document describes the upstream API calls used by `inspectarr`.

Official reference:

- [Tautulli API Reference](https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference)

## Endpoint pattern

Requests use Tautulli v2 API over HTTP GET:

`/api/v2?apikey=<API_KEY>&cmd=<COMMAND>&...params`

Base URL is provided per configured server (for example `http://host:8181`).

## Commands used

## `get_activity`

Purpose:

- Retrieve current stream/session activity for live dashboard view.

Typical query parameters:

- `apikey`
- `cmd=get_activity`

Notable response sections:

- `response.result` (`success` or `error`)
- `response.data.stream_count`
- `response.data.sessions` (array of active streams)

Insecpectarr usage notes:

- Called in parallel across all configured servers.
- `sessions` rows are normalized and tagged with `server_id`.

## `get_history`

Purpose:

- Retrieve playback history for merged history page.

Typical query parameters:

- `apikey`
- `cmd=get_history`
- `start` (offset)
- `length` (page/window size)
- `after` / `before` (optional `YYYY-MM-DD` bounds; see Tautulli API reference)
- `order_column` / `order_dir` (inspector sets `date` + `desc` for predictable newest-first paging)
- optional filters (as supported upstream and selected by UI)

Notable response sections:

- `response.result`
- `response.data.data` (history rows array)
- `response.data.recordsFiltered`
- `response.data.recordsTotal`

Insecpectarr usage notes:

- Called per server, then merged to one timeline.
- Uses global timestamp-based merge-sort via canonical UTC epoch normalization.
- Each normalized row includes `server_id`.
- Current upstream passthrough filters: `user`, `media_type`, and when configured, `after` / `before` for the merged history page.
- The `/history` dashboard defaults to a rolling week (UTC) via upstream `after`, or an optional **all-time** mode that pages with small `length`, long inter-page delays, optional single-server parallelism, and a configurable per-server row cap.
- UI date inputs still apply a post-merge filter on canonical timestamps as a safety net.
- Unwatched insights (`/insights/unwatched`) are derived from indexed history rows (`get_history`) filtered by media type and staleness threshold.

## `get_libraries`

Purpose:

- Discover library sections and identify TV libraries (`section_type=show`) for inventory traversal.

Insecpectarr usage notes:

- Called per server before inventory fetch in `/insights/library-unwatched`.

## `get_library_media_info`

Purpose:

- Fetch show-level rows for a TV section.

Typical query parameters:

- `apikey`
- `cmd=get_library_media_info`
- `section_id`
- `start`
- `length`

Insecpectarr usage notes:

- Used to page through show rows before season/episode traversal.
- In library-unwatched mode, called incrementally with small `start`/`length` chunks per request.

## `get_children_metadata`

Purpose:

- Fetch child metadata for a given rating key.

Typical query parameters:

- `apikey`
- `cmd=get_children_metadata`
- `rating_key`

Insecpectarr usage notes:

- Called on show `rating_key` to fetch seasons, then on season `rating_key` to fetch episodes.
- Supports inventory-joined report for shows/seasons/episodes not watched in the history index window.

Library-unwatched rows may include `guid` / `grandparent_guid` fields (when returned by upstream) so the dashboard can derive a TVDB series id for optional Sonarr integration; see `docs/SONARR.md`.

## Optional commands

## `get_users` (optional)

Purpose:

- Enrich user displays, validate user mapping assumptions, or support filter metadata.

## `server_status` (optional)

Purpose:

- Provide a direct status indicator for per-server health strip.

## Common error handling

For each upstream call:

- Handle timeout as degraded server (do not fail entire response).
- Handle non-200 HTTP as degraded server.
- Handle `response.result != success` as degraded server with message.
- Surface per-server status in UI.

## Response normalization guidance

Normalize each row into a stable internal schema before merge operations. Recommended minimum fields:

- `server_id`
- `server_name`
- `user`
- `title`
- `media_type`
- `started` or canonical timestamp
- `raw` (optional full upstream row for diagnostics)

Keeping a normalized shape avoids template coupling to server-specific payload quirks.
