# Plex API: removing episodes, seasons, and series

This note records how **Plex Media Server (PMS)** can remove library items via its HTTP API, how that differs from this app’s **Sonarr** path, and what to watch for when operating it.

## Implemented in scoparr

- **`/settings`**: Plex server JSON (`plex_servers`), optional pasted tokens, **Sign in with Plex (primary / secondary)** using Plex.tv PIN + `GET /settings/plex-auth/check` (tokens and auto `plex_client_identifier` persist in dashboard JSON).
- **`POST /insights/library-unwatched/plex/delete-library-item`**: invoked from the browser after successful Sonarr **Remove & unmonitor** or **Delete** on per-server rows when `ratingKey` + Plex mapping exist; also callable alone for automation. Uses `DELETE /library/metadata/{ratingKey}` on the PMS for `tautulli_server_id` and `token_profile` **primary** or **secondary**.
- Cumulative unwatched tables do not chain Plex (no reliable per-server `ratingKey`). See `docs/CONFIGURATION.md`.

## Official reference

- [Plex Media Server API](https://developer.plex.tv/pms/) — download the OpenAPI spec from that page for exact paths and parameters on your target PMS version.

The published Library surface includes operations such as **delete a metadata item** and **delete a media item** (related but not identical; confirm against OpenAPI). Management endpoints (including media deletion) are associated with the provider **`manage`** feature in the docs.

## Can the API remove an episode / season / series?

**Yes**, in the usual PMS deployment. Removal is **server-local**: your client calls **that** Plex instance’s base URL (often port `32400`), authenticated as a user who may **manage** libraries.

### Identifiers

- Items are addressed by **`ratingKey`** (commonly a numeric string).
- Obtain keys from `GET /library/metadata/{id}`, library section listings, or—**in this project**—Tautulli inventory/metadata commands (see `docs/TAUTULLI_API.md` for `get_children_metadata` and `docs/ARCHITECTURE.md` for inventory traversal).

### Metadata types (TV)

From the official docs’ type table (names and numeric codes):

| Type   | Code |
|--------|------|
| `show` | 2    |
| `season` | 3  |
| `episode` | 4 |

Deletion is typically performed at the node you want gone: **episode** key for one episode; **season** or **show** key for broader scope. **Verify behavior on your PMS version** for edge cases (empty seasons, multiple files, editions).

### Authentication

- Most calls use **`X-Plex-Token`** (or the same value as a query parameter). **`X-Plex-Client-Identifier`** is commonly required.
- The docs describe **JWT** flows for newer clients ([Authenticating with Plex](https://developer.plex.tv/pms/) in the same site).
- The token must represent a user allowed to perform **library management** on that server.

### Typical DELETE pattern (community-documented)

Third-party guides (aligned with what the Plex web UI uses) describe:

```http
DELETE http://{host}:32400/library/metadata/{ratingKey}?X-Plex-Token={token}
```

Example write-up: [Plexopedia: Delete a Movie](https://www.plexopedia.com/plex-media-server/api/library/movie-delete/) — for that endpoint they state the movie **and associated files** are removed. **Do not rely on community pages alone** for production; reconcile with the official OpenAPI for your server version.

### Trash and library sections

- Server/library settings may send deletions to **trash** instead of immediate removal from disk.
- There are **section-level** operations such as **empty trash** in the API surface (see Library section in the official index; community: [Empty Trash](https://www.plexopedia.com/plex-media-server/api/library/empty-trash/)).
- **Section id** for trash operations is the **library section** identifier, not a show/episode `ratingKey`.

### Metadata delete vs media delete

The official API lists separate delete operations for **metadata** vs **media**. Implementations should use the OpenAPI definitions to choose the correct endpoint and query/body parameters for “remove from library only” vs “remove underlying files,” if both are exposed on your build.

## How this relates to scoparr

- **`POST /insights/library-unwatched/sonarr/remove-from-plex-and-unmonitor`** does **not** call Plex. It unmonitors in Sonarr and deletes files via Sonarr; Plex updates after refresh. See `docs/SONARR.md`.
- **`POST /insights/library-unwatched/sonarr/delete`** removes the series from Sonarr (show) or deletes episode file(s) without unmonitoring (season/episode); also Sonarr-only, no Plex API.
- **Tautulli** is not a substitute for PMS management APIs: direct removal requires **PMS URL + credentials** and a known **`ratingKey`** for **that** server.

### Multi-server and cumulative rows

- **`ratingKey` is per Plex instance.** A row that is deduplicated across Tautulli servers may not carry the correct key for every underlying PMS.
- Any future “delete in Plex” action should be scoped to **per-server** inventory rows (or store **per-server `ratingKey`**) so the target server is unambiguous.

## Suggestions for a future implementation

1. **Settings** — Per PMS (or mapped per Tautulli server): base URL, token, timeout, TLS; document in `docs/CONFIGURATION.md`.
2. **Server-side only** — Proxy deletes from the app; never send the Plex token to the browser; reuse the project’s pattern of redacting secrets in logs.
3. **UX** — Strong confirmation and optional “preview” (resolve metadata title/path before DELETE).
4. **Choose integration path** — Sonarr-managed TV: keep using Sonarr for file lifecycle when appropriate. Non-Sonarr libraries, movies, or operators who want Plex-native deletion: use PMS API.
5. **Testing** — Mock PMS or use a disposable library; confirm trash vs permanent delete for your settings.

## TODO (documentation)

- [ ] Keep this file aligned with the OpenAPI bundle from [developer.plex.tv/pms](https://developer.plex.tv/pms/) when upgrading PMS.
- [ ] If direct Plex deletion is implemented, add `docs/CONFIGURATION.md` variables and link from `README.md`.

## TODO (optional follow-ups)

- [ ] Optional `httpx` mock tests for `plex_delete_library_metadata_optional` and PIN helpers.
- [ ] Optional section **empty trash** or **delete media** variant once confirmed against target PMS OpenAPI.
- [ ] Optional JWT device flow if Plex deprecates classic PIN for your use case.
