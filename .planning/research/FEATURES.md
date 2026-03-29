# Feature Landscape

**Domain:** Plex/Tautulli Monitoring Dashboard
**Researched:** 2026-03-29
**Confidence:** HIGH

## Executive Summary

The Plex monitoring ecosystem is anchored by Tautulli (6.4k GitHub stars), which provides comprehensive monitoring, analytics, and notifications. Newer alternatives like Tracearr (1.6k stars) add account sharing detection and rules-based automation, while PlixMetrics offers modern UI with geo-visualization. The multi-instance aggregation space is underserved, with Scoparr's Horizon Watch (stale-library insights) representing a genuine differentiator.

## Table Stakes

Features users expect. Missing = product feels incomplete or unusable.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Live Activity Stream** | Core monitoring capability - see who's watching what, right now | Low | Standard Tautulli API; polling is acceptable per constraints |
| **Watch History** | Track what was watched, when, by whom | Low | Tautulli `get_history` API provides this |
| **Library Statistics** | Media counts, storage usage, content breakdown | Low | Tautulli `get_library_names` + `get_library_media_info` |
| **User Tracking** | Per-user activity, watch time, device usage | Low | Tautulli `get_users` + user-specific history |
| **Multi-Tautulli Instance Support** | Manage multiple Plex servers from one dashboard | Medium | Core value prop; existing in Scoparr |
| **HTTP Basic Authentication** | Secure access to dashboard | Low | Existing in Scoparr |
| **Server Health/Status** | Is each Tautulli instance reachable? | Low | Health check endpoint exists |
| **Recently Added Content** | Track new media arrivals | Low | Tautulli `get_recently_added` |
| **Responsive Web UI** | Usable on desktop and tablet | Low | Jinja2 templates handle this |

## Differentiators

Features that set Scoparr apart. Not expected, but valued by target users.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Stale Library Insights (Horizon Watch)** | Identify long-unwatched content across all libraries | Medium | Core differentiator; unique value add |
| **Merged History View (Broadside Range)** | Unified history from multiple Tautulli instances | Medium | Existing feature; valuable for multi-server operators |
| **Sonarr/Radarr/Overseerr Integration** | Actionable insights - refresh, search, request content | Medium | Optional per PROJECT.md; adds workflow value |
| **Unified Live Activity (Deck Watch)** | Single pane showing all streams across instances | Low-Medium | Existing; valuable for ops monitoring |
| **SQLite Caching** | Graceful degradation when upstream fails | Low | Existing; prevents complete outage |
| **Rate-Limited Actions** | Prevent accidental bulk operations | Low | Existing; protects Sonarr/Plex APIs |

### Emerging Differentiators in Ecosystem

These features exist in competing products but not yet in Scoparr:

| Feature | Source | Opportunity |
|---------|--------|-------------|
| **Geo Visualization** | PlixMetrics | LOW - Nice to have, not core value |
| **Account Sharing Detection** | Tracearr | MEDIUM - Could be valuable for Plex ops |
| **Rules Engine (stream limits, geo-restrictions)** | Tracearr | LOW - Beyond monitoring scope |
| **Bandwidth/Transcode Analytics** | Tracearr, PlixMetrics | LOW - Nice for server ops |
| **PWA/Mobile App** | PlixMetrics | Out of scope per PROJECT.md |

## Anti-Features

Features to explicitly NOT build.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Real-time WebSocket Updates** | PROJECT.md explicitly states polling suffices; adds complexity | Continue polling approach |
| **Mobile Native App** | Web-first approach; mobile later | Progressive Web App if needed |
| **Public Deployment Without Auth** | Security risk; requires reverse proxy with TLS | Document secure deployment |
| **Full Server Management** | Not a Plex replacement; monitoring only | Stay in monitoring/insights lane |
| **Content Playback** | Not a media player | Keep focused on monitoring |
| **Media File Management** | Beyond scope; Sonarr/Radarr handle this | Provide read-only insights + optional actions |

## Feature Dependencies

```
Deck Watch (Live Activity)
  └─> Multi-Instance Configuration
       └─> Server Health Checks

Broadside Range (Merged History)
  └─> Multi-Instance Configuration
       └─> Tautulli API Access (history endpoint)

Horizon Watch (Stale Library)
  └─> Multi-Instance Configuration
       └─> Library Metadata Access
            └─> (Optional) Sonarr/Radarr Integration for Actions

Settings Page
  └─> HTTP Basic Authentication
       └─> Health Check Endpoint
```

## MVP Recommendation

Based on research, current Scoparr implementation is already well-positioned:

### Already Implemented (Keep)
1. **Deck Watch** - Live activity across instances (table stakes + differentiator)
2. **Broadside Range** - Merged history (differentiator for multi-server ops)
3. **Horizon Watch** - Stale library insights (unique differentiator)
4. **Settings/Config** - Multi-instance management
5. **HTTP Basic Auth** - Security
6. **Health Check** - Reliability

### Prioritize for Enhancement
1. **Stale Library Actions** - Add Sonarr/Radarr/Overseerr integration for Horizon Watch (addressing TODO.md)
2. **UI/UX Improvements** - From review backlog

### Defer
- WebSocket (polling suffices)
- Mobile app (web-first)
- PWA (low value vs effort)
- Account sharing detection (not the target market)

## Sources

- Tautulli Official (tautulli.com) - Core feature reference
- Tracearr (tracearr.com, 1.6k GitHub stars) - Modern monitoring features
- PlixMetrics (github.com/plix-labs/PlixMetrics, 58 stars) - UI/visualization patterns
- Tautulli_Combined (github.com/jsgiacomi) - Multi-instance precedent
- Reddit r/Tautulli, r/Plex, r/selfhosted - User expectations
- Context: PROJECT.md constraints (polling OK, web-first, auth required)
