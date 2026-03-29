# Project Research Summary

**Project:** Tautulli Inspector (Scoparr)
**Domain:** Multi-source Plex/Tautulli Monitoring Dashboard
**Researched:** 2026-03-29
**Confidence:** MEDIUM-HIGH

## Executive Summary

This project is a multi-source monitoring dashboard that aggregates data from multiple Tautulli/Plex servers into a unified "single pane of glass" view. The existing stack (Python 3.11+, FastAPI, Jinja2, SQLite) is well-suited for this use case. Research confirms that the recommended approach uses a **layered adapter pattern** with fan-out polling to multiple Tautulli instances, followed by data normalization and fan-in aggregation to present unified views.

The key insight from research is that the existing Scoparr implementation already has strong feature parity with competitors — Deck Watch (live activity), Broadside Range (merged history), and Horizon Watch (stale library insights) represent both table stakes and differentiators. The main gaps are **not** in features but in **operational robustness**: error handling, caching strategies, and async conversion.

**Key risks to mitigate:**
- One offline Tautulli server currently takes down the entire dashboard (no graceful degradation)
- Sequential API calls create latency that scales linearly with server count
- No stale-data fallback when upstream APIs are temporarily unavailable

## Key Findings

### Recommended Stack

The existing stack is solid. Research recommends **adding** httpx for async HTTP communication (replacing requests), HTMX + Alpine.js for SPA-like interactivity without SPA complexity, and Chart.js for visualization enhancements.

**Core technologies:**
- **Python 3.11+** — Already in use; provides 25% performance improvement over 3.10
- **FastAPI >=0.115.0** — Already in use; excellent async support, native Pydantic integration
- **httpx >=0.28.0** — **Critical addition.** Supports both sync and async HTTP calls with connection pooling. Replace direct `requests` usage.
- **HTMX >=1.9.10** — Enables dynamic updates without JavaScript complexity. Works perfectly with Jinja2/FastAPI
- **Alpine.js >=3.14.0** — Lightweight client-side state (modals, toggles). ~15KB alternative to React/Vue
- **SQLAlchemy >=2.0.0** — ORM abstraction. Supports both SQLite and PostgreSQL with same code

**Avoid:** pytulli library (limited maintenance, last update 2020). Build a thin httpx wrapper for Tautulli API instead.

### Expected Features

**Must have (table stakes):**
- Live Activity Stream — Core monitoring, see who's watching what right now
- Watch History — Track what was watched, when, by whom
- Library Statistics — Media counts, storage usage
- User Tracking — Per-user activity, watch time
- Multi-Tautulli Instance Support — Manage multiple Plex servers from one dashboard
- HTTP Basic Authentication — Secure access to dashboard
- Server Health/Status — Is each Tautulli instance reachable?

**Should have (competitive):**
- Stale Library Insights (Horizon Watch) — Unique differentiator; identify long-unwatched content
- Merged History View (Broadside Range) — Unified history from multiple instances
- Sonarr/Radarr/Overseerr Integration — Actionable insights for stale content
- Unified Live Activity (Deck Watch) — Single pane showing all streams

**Defer (v2+):**
- WebSocket real-time updates (polling suffices per constraints)
- Mobile native app (web-first approach)
- Account sharing detection (not target market)
- PWA (low value vs effort)

### Architecture Approach

The recommended architecture follows a **layered adapter pattern** with clear component boundaries:

1. **Source Adapters (Tautulli Connectors)** — Handle all communication with individual Tautulli instances. Expose standardized interface regardless of Tautulli version.
2. **Normalization Layer** — Transform heterogeneous API responses into unified canonical schema (Pydantic models). Handle field name variations, date formats.
3. **Aggregation Engine** — Combine normalized data from multiple sources into unified views. Handle merging, deduplication, sorting, filtering.
4. **Caching Layer** — TTL-based cache with per-source invalidation. Serve stale data on upstream failure (graceful degradation).
5. **API Layer (FastAPI)** — Expose HTTP endpoints for the UI with appropriate cache headers.

### Critical Pitfalls

1. **No graceful degradation when upstream fails** — One offline server takes down entire dashboard. Avoid with circuit breakers, partial results, async `asyncio.gather(return_exceptions=True)`.

2. **Ignoring rate limits across multiple instances** — Polling too aggressively triggers 429 errors. Avoid with per-server rate limiting, token bucket algorithm, tracking `X-RateLimit-Remaining` headers.

3. **No stale-data fallback strategy** — Dashboard shows nothing when API temporarily unavailable. Implement stale-while-revalidate pattern with visual indicators.

4. **Synchronous sequential API calls** — Latency equals sum of all response times. Use async/await with httpx for parallel fetching — total latency becomes "max" not "sum".

5. **Inconsistent data schema across instances** — Different Tautulli versions return different field names. Implement explicit normalization layer with schema validation and fallback defaults.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Foundation & Resilience
**Rationale:** The architecture cannot function reliably without addressing the critical pitfalls. This phase establishes the foundation all other work depends on.

**Delivers:**
- httpx-based Tautulli client wrapper (async, with timeouts)
- Per-source circuit breakers
- Graceful degradation: return partial results when some servers are down
- Stale-while-revalidate caching layer
- Per-server rate limiting

**Avoids:** Pitfalls 1, 2, 3, 4

### Phase 2: Normalization & Aggregation
**Rationale:** With reliable source communication in place, this phase implements the data transformation layer that enables unified views.

**Delivers:**
- Pydantic schema definitions for unified activity, history, library data
- Normalizer implementations for each data type
- Field mapper handling variations across Tautulli versions
- ActivityAggregator, HistoryAggregator, StaleLibraryFinder

**Uses:** Stack elements: httpx, Pydantic, SQLAlchemy
**Implements:** Architecture components: Normalization Layer, Aggregation Engine

**Avoids:** Pitfall 5 (schema inconsistency)

### Phase 3: API Layer & Health Monitoring
**Rationale:** Expose the aggregation layer via HTTP endpoints and add visibility into source health.

**Delivers:**
- FastAPI endpoints: `/api/activity`, `/api/history`, `/api/libraries`, `/api/stale`, `/api/sources`
- Request validation, error handling
- Health check endpoint with per-server status
- Metrics: success rate, latency percentiles, error counts per server

**Avoids:** Pitfall 6 (no health monitoring)

### Phase 4: UI/UX Enhancements
**Rationale:** Now that the backend is robust, enhance the frontend with HTMX for interactivity and better error states.

**Delivers:**
- HTMX integration for live activity refresh, history filtering
- Alpine.js for UI state (modals, dropdowns, toggles)
- Per-server loading indicators
- Visual status indicators for server health
- "Last updated" timestamps and cached data badges

**Addresses:** Features: Deck Watch improvements, Better error states

### Phase 5: Integration Extensions
**Rationale:** Optional differentiation layer. Per PROJECT.md, Sonarr/Radarr integration is a potential enhancement.

**Delivers:**
- Sonarr/Radarr/Overseerr API integration for Horizon Watch actions
- Rate-limited action endpoints to prevent accidental bulk operations

**Addresses:** Differentiators from FEATURES.md

### Phase Ordering Rationale

- **Phase 1 first:** The critical pitfalls (especially #1 graceful degradation and #4 async conversion) affect everything downstream. Can't build reliable aggregation on broken foundations.
- **Phases 2-3 follow logically:** Normalization requires working source adapters; API layer requires normalized data to expose.
- **Phase 4 UI:** Frontend work is cleaner when backend is stable and error handling is in place.
- **Phase 5 last:** Integration work is optional and depends on all preceding phases working.

This ordering follows the architecture's own build dependencies: Source Adapters → Normalization → Aggregation → Caching → API → Resilience.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 5 (Integration Extensions):** Sonarr/Radarr/Overseerr API details need verification. May need `/gsd-research-phase` if integration complexity is high.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** Circuit breaker, rate limiting, caching patterns are well-documented. Use established libraries (e.g., `pybreaker`).
- **Phase 2 (Normalization):** Pydantic modeling is standard FastAPI practice.
- **Phase 3 (API):** FastAPI endpoints are straightforward.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Technologies are industry-standard with excellent documentation. Existing stack is already well-chosen. |
| Features | HIGH | Feature landscape well-understood from Tautulli ecosystem. Existing implementation matches table stakes + differentiators. |
| Architecture | MEDIUM | Fan-out/fan-in pattern is proven for multi-source aggregation. Tautulli-specific implementation details require verification against actual API responses. |
| Pitfalls | MEDIUM | Patterns identified from community discussions and best practices. Need validation that current implementation exhibits these issues. |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

1. **Current implementation gaps:** Need to verify which of the 6 critical pitfalls the current codebase exhibits. Run tests with one server offline to confirm graceful degradation status.

2. **Tautulli API schema validation:** Normalization layer assumes field name variations. Need to test against actual Tautulli instances (different versions) to confirm schema differences exist.

3. **Rate limit configuration:** Research identified rate limits as a concern but didn't find specific Tautulli rate limit values. May need to empirically determine during Phase 1.

## Sources

### Primary (HIGH confidence)
- Tautulli Official (tautulli.com) — Core feature reference, API endpoints
- Tautulli API Reference (github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference) — Official API documentation
- FastAPI GitHub — Version requirements, Pydantic compatibility
- httpx Documentation (python-httpx.org) — Async HTTP client patterns

### Secondary (MEDIUM confidence)
- HTMX Documentation (htmx.org) — Server-side interactivity patterns
- Multi-source dashboard patterns: BIX Tech "Best Practices for Building Grafana Dashboards with Multiple Data Sources" (2025)
- Tautulli_Combined (github.com/jsgiacomi) — Reference implementation for multi-instance

### Tertiary (LOW confidence)
- Tracearr (tracearr.com) — Emerging features, competitive landscape (1.6k GitHub stars)
- PlixMetrics (github.com/plix-labs/PlixMetrics) — UI/visualization patterns, needs validation against actual usage

---

*Research completed: 2026-03-29*
*Ready for roadmap: yes*
