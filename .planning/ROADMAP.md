# Roadmap: Scoparr

**Project:** Multi-instance Tautulli monitoring dashboard  
**Granularity:** fine  
**Created:** 2026-03-29

---

## Phases

- [ ] **Phase 1: Foundation & Resilience** - Core infrastructure for reliable multi-source monitoring
- [ ] **Phase 2: Monitoring & Health** - Health endpoints and metrics for operational visibility
- [ ] **Phase 3: Stale Library Improvements** - Performance and error handling for Horizon Watch
- [ ] **Phase 4: UI/UX Enhancements** - Loading states, status indicators, and HTMX integration

---

## Phase Details

### Phase 1: Foundation & Resilience

**Goal:** Enable reliable multi-source Tautulli monitoring with graceful degradation when upstream servers fail

**Depends on:** Nothing (first phase)

**Requirements:** FOUN-01, FOUN-02, FOUN-03, FOUN-04, FOUN-05

**Success Criteria** (what must be TRUE):
1. Dashboard remains functional when one or more Tautulli servers are offline (shows cached/partial data)
2. API requests to each Tautulli instance execute asynchronously in parallel
3. Circuit breaker trips after repeated failures, preventing cascade to healthy instances
4. Rate limiting prevents 429 errors from any single Tautulli server
5. Stale data is served while revalidating in background, with visual indicator

**Plans:** TBD

---

### Phase 2: Monitoring & Health

**Goal:** Provide operational visibility into each Tautulli instance status and upstream health

**Depends on:** Phase 1

**Requirements:** MON-01, MON-02

**Success Criteria** (what must be TRUE):
1. Health check endpoint (`/health`) returns status for each configured Tautulli server
2. Dashboard displays per-server health status clearly (online/offline/degraded)
3. Failed upstream requests are logged with server identification and failure reason
4. Metrics collection tracks request success rate and latency per server

**Plans:** TBD

---

### Phase 3: Stale Library Improvements

**Goal:** Improve Horizon Watch reliability and performance for stale-library insights

**Depends on:** Phase 2

**Requirements:** STAL-01, STAL-02

**Success Criteria** (what must be TRUE):
1. Stale library data refresh completes within acceptable time regardless of Sonarr availability
2. Sonarr API failures don't crash the stale library view - graceful fallback to cached Tautulli data
3. Error messages for Sonarr failures are actionable (clear indication of what failed and why)

**Plans:** TBD

---

### Phase 4: UI/UX Enhancements

**Goal:** Create smooth, responsive user experience with clear status communication

**Depends on:** Phase 3

**Requirements:** UX-01, UX-02, UX-03, UX-04, UX-05

**Success Criteria** (what must be TRUE):
1. Users see loading indicators during data fetches (not blank screens)
2. Each page shows server status indicators (which servers are online/offline)
3. Error states display actionable messages (not technical jargon)
4. HTMX enables smooth page interactions without full reloads
5. Visual badges indicate when data is cached vs. live

**Plans:** TBD

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Resilience | 0/1 | Not started | - |
| 2. Monitoring & Health | 0/1 | Not started | - |
| 3. Stale Library Improvements | 0/1 | Not started | - |
| 4. UI/UX Enhancements | 0/1 | Not started | - |

---

## Coverage

**v1 Requirements:** 14 total  
**Mapped to phases:** 14 ✓  
**Unmapped:** 0 ✓

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUN-01: httpx for async HTTP | Phase 1 | Pending |
| FOUN-02: Circuit breaker pattern | Phase 1 | Pending |
| FOUN-03: Rate limiting per server | Phase 1 | Pending |
| FOUN-04: Graceful degradation | Phase 1 | Pending |
| FOUN-05: Stale-while-revalidate | Phase 1 | Pending |
| MON-01: Enhanced health check | Phase 2 | Pending |
| MON-02: Metrics/logging | Phase 2 | Pending |
| STAL-01: Stale library performance | Phase 3 | Pending |
| STAL-02: Sonarr error handling | Phase 3 | Pending |
| UX-01: Loading states | Phase 4 | Pending |
| UX-02: Per-server health status | Phase 4 | Pending |
| UX-03: Error states | Phase 4 | Pending |
| UX-04: HTMX integration | Phase 4 | Pending |
| UX-05: Server status indicators | Phase 4 | Pending |

---

*Roadmap created: 2026-03-29*
