# Requirements: Scoparr

**Defined:** 2026-03-29
**Core Value:** Multi-instance Tautulli monitoring with unified activity view and stale-library insights for Plex media server operators managing multiple servers.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Foundation

- [ ] **FOUN-01**: Use httpx for async HTTP requests to Tautulli servers
- [ ] **FOUN-02**: Implement circuit breaker pattern per Tautulli instance
- [ ] **FOUN-03**: Add rate limiting per server to prevent 429 errors
- [ ] **FOUN-04**: Implement graceful degradation when upstream fails (show cached/partial data)
- [ ] **FOUN-05**: Add stale-while-revalidate caching pattern

### UI/UX

- [ ] **UX-01**: Show loading states during data fetches
- [ ] **UX-02**: Display per-server health status clearly
- [ ] **UX-03**: Show error states with actionable messages
- [ ] **UX-04**: Add HTMX for smoother page interactions
- [ ] **UX-05**: Add server status indicators in UI

### Monitoring

- [ ] **MON-01**: Enhanced health check endpoint with per-server status
- [ ] **MON-02**: Add metrics/logging for upstream request failures

### Stale Library

- [ ] **STAL-01**: Improve stale library refresh performance
- [ ] **STAL-02**: Better error handling for Sonarr API failures

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Visualization

- **VIS-01**: Add Chart.js for viewing trends and history graphs
- **VIS-02**: Dashboard widgets for key metrics

### Real-time

- **REAL-01**: SSE (Server-Sent Events) for near-real-time activity updates
- **REAL-02**: Configurable refresh intervals

### Integration

- **INT-01**: Additional Sonarr/Radarr actions from Horizon Watch
- **INT-02**: Enhanced Overseerr integration

## Out of Scope

| Feature | Reason |
|---------|--------|
| WebSocket support | Polling suffices for current use case |
| Mobile native app | Web-first approach, defer mobile |
| Public unauthenticated deployment | Security risk, requires reverse proxy with TLS |
| Full server management | Read-only monitoring is core value |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUN-01 | Phase 1 | Pending |
| FOUN-02 | Phase 1 | Pending |
| FOUN-03 | Phase 1 | Pending |
| FOUN-04 | Phase 1 | Pending |
| FOUN-05 | Phase 1 | Pending |
| UX-01 | Phase 2 | Pending |
| UX-02 | Phase 2 | Pending |
| UX-03 | Phase 2 | Pending |
| UX-04 | Phase 2 | Pending |
| UX-05 | Phase 2 | Pending |
| MON-01 | Phase 3 | Pending |
| MON-02 | Phase 3 | Pending |
| STAL-01 | Phase 4 | Pending |
| STAL-02 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-29 after initial definition*
