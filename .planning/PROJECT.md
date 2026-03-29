# Scoparr

## What This Is

Small "single pane of glass" dashboard that aggregates **live activity** and **merged history** from multiple [Tautulli](https://tautulli.com/) instances, plus a **Sonarr + Tautulli** view for long-unwatched TV library rows (optional Sonarr / Plex / Overseerr actions).

## Core Value

Multi-instance Tautulli monitoring with unified activity view and stale-library insights for Plex media server operators managing multiple servers.

## Requirements

### Validated

- ✓ Multi-Tautulli server aggregation — existing
- ✓ Deck Watch (live activity) — existing
- ✓ Broadside Range (merged history) — existing
- ✓ Horizon Watch (stale-library insights) — existing
- ✓ Settings page with configuration — existing
- ✓ HTTP Basic authentication — existing
- ✓ Health check endpoint — existing

### Active

- [ ] Improve UI/UX based on review backlog
- [ ] Additional features from TODO.md

### Out of Scope

- Real-time WebSocket updates — polling suffices for current use case
- Mobile app — web-first approach, mobile later
- Public deployment without auth — requires reverse proxy with TLS

## Context

- Stack: Python 3.11+, FastAPI, Jinja2 (server-rendered UI)
- Existing codebase with tests
- Multiple Tautulli server support is core to the value proposition
- Optional integrations: Sonarr, Radarr, Overseerr, Plex

## Constraints

- **Python version**: 3.11+ required
- **Network access**: Must reach Tautulli (and optional Sonarr/Plex/Overseerr) servers
- **API keys**: Valid Tautulli API key for each configured server

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Server-rendered UI (Jinja2) | Simpler deployment, no client-side complexity | ✓ Good |
| SQLite optional cache | Graceful degradation when upstream fails | ✓ Good |
| Rate limiting on actions | Prevent abuse of Sonarr/Plex operations | ✓ Good |

---
*Last updated: 2026-03-29 after initialization*
