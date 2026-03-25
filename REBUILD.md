This document is a comprehensive technical blueprint for an AI agent or automated script to manage a "stale" media library. It bridges the gap between intent (Overseerr), state (Sonarr), and usage (Tautulli).

***

# Media Lifecycle Management: Programmatic Auditing & Deletion

## 1. Objective
To identify and remove Series and Seasons from disk that are no longer being utilized, specifically targeting two cohorts:
* **Cohort A (Never Played):** Media that has existed on disk for 6+ months but has `0` plays.
* **Cohort B (Long-term Stale):** Media that has not been played in over **2 years**.

---

## 2. API Integration Strategy
The agent must aggregate data from three services using **TVDB ID** (Series) and **Season Number** (Seasons) as the common keys.

### Data Sources
| Metric | Service | Endpoint | Key Data Points |
| :--- | :--- | :--- | :--- |
| **Request History** | Overseerr | `/api/v1/request` | `requestedBy`, `createdAt` |
| **Current State** | Sonarr | `/api/v3/series` | `statistics.sizeOnDisk`, `monitored`, `id` |
| **Usage Statistics** | Tautulli | `get_library_media_info` | `last_played` (Unix), `play_count` |

---

## 3. Stale Media Identification Logic
The agent should follow this logic flow to categorize media before recommending deletion:

### Filtering Criteria
* **Never Played:** If `last_played` is `null` or `0` AND `added_date` > 180 days.
* **Stale (2+ Years):** If `last_played` < `(Current_Time - 63,072,000 seconds)`.

---

## 4. Operational Functions

### A. Toggle Monitoring (The "Prevention" Step)
Before deleting files, the agent **must** unmonitor the content to prevent Sonarr from automatically re-downloading it.

**Function: `Unmonitor_Season`**
1.  Perform `GET /api/v3/series/{id}` to retrieve the full Series JSON.
2.  In the `seasons` array, find the target `seasonNumber`.
3.  Set `"monitored": false` for that specific object.
4.  Perform `PUT /api/v3/series/{id}` with the modified JSON.

### B. Delete Media from Disk
To reclaim space without destroying the database entry for the series:

**Function: `Delete_Season_Files`**
1.  Query `GET /api/v3/episodefile?seriesId={id}`.
2.  Filter the results for the specific `seasonNumber`.
3.  For every file found, execute `DELETE /api/v3/episodefile/{file_id}?apikey={key}`.

### C. Backend Synchronization (Plex)
Deleting files via API can sometimes leave "ghost" entries in the Plex UI.

**Function: `Sync_Backend`**
* Execute `POST /api/v3/command` in Sonarr.
* **Payload:** `{"name": "RescanSeries", "seriesId": {id}}`.
* *Note: This forces Sonarr to update its own disk state and subsequently triggers the Plex 'Update Library' via the Sonarr-Plex Connect integration.*

---

## 5. Execution Workflow for AI Agent

1.  **Ingest:** Load Tautulli library metadata.
2.  **Filter:** Identify TVDB IDs matching "Never Played" or "2+ Years Stale" where `sizeOnDisk > 0`.
3.  **Cross-Check:** Query Overseerr to see who requested the item. If the requester has been inactive or the request is ancient, proceed.
4.  **Confirm:** Present a list of Series/Seasons and total recoverable GB to the user.
5.  **Execute:**
    * Call `Unmonitor_Season`.
    * Call `Delete_Season_Files`.
    * Call `Sync_Backend`.

---

> **Implementation Note:** Ensure all timestamps are handled in UTC. When deleting entire Series (rather than just seasons), use `DELETE /api/v3/series/{id}?deleteFiles=true` to ensure total cleanup in one call.

