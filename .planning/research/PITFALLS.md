# Pitfalls Research

**Domain:** Multi-source Tautulli/Plex monitoring dashboards
**Researched:** 2026-03-29
**Confidence:** MEDIUM

Research gathered from multi-source dashboard patterns, API aggregation best practices, and Tautulli/Plex community discussions.

---

## Critical Pitfalls

### Pitfall 1: No Graceful Degradation When Upstream Fails

**What goes wrong:**
Dashboard becomes completely unusable when any single Tautulli server is unreachable. One offline server takes down the entire "single pane of glass."

**Why it happens:**
Developers assume happy-path where all APIs respond. Missing error handling means one timeout blocks the entire response aggregation. Sequential API calls amplify this — if one server is slow, the whole dashboard hangs.

**How to avoid:**
- Implement circuit breaker pattern for each Tautulli instance
- Return partial results when some servers are down (aggregate available data, show degraded state)
- Use concurrent async fetching with `asyncio.gather(*tasks, return_exceptions=True)`
- Add timeout per-server (don't let one slow server block the dashboard)

**Warning signs:**
- Dashboard "spinning" indefinitely when any Tautulli instance is slow
- No visual indication of which servers are online/offline
- All data disappears if one server returns an error

**Phase to address:** Initial UI/UX improvements (when addressing error states and offline handling)

---

### Pitfall 2: Ignoring Rate Limits Across Multiple Tautulli Instances

**What goes wrong:**
Polling too aggressively against multiple Tautulli servers triggers rate limiting, causing 429 errors and data gaps. Each Tautulli instance has independent rate limits that can be exhausted.

**Why it happens:**
Dashboard refreshes too frequently or makes redundant requests. Developers don't track rate limit budgets per server. Each instance's API has independent limits — exhausting one doesn't affect others, but you need to manage each separately.

**How to avoid:**
- Implement per-server rate limiting using token bucket or leaky bucket algorithm
- Cache responses with appropriate TTL (not too fresh, not too stale)
- Track `X-RateLimit-Remaining` headers from Tautulli responses
- Spread requests across time windows rather than burst polling

**Warning signs:**
- Intermittent 429 errors in logs
- Data "disappearing" for certain servers at predictable intervals
- Dashboard becomes unreliable during high-usage periods

**Phase to address:** Foundation/backend work (rate limiting and caching infrastructure)

---

### Pitfall 3: No Stale-Data Fallback Strategy

**What goes wrong:**
When Tautulli API is temporarily unavailable, dashboard shows nothing instead of serving cached data. Users lose visibility entirely during brief outages.

**Why it happens:**
Caching is either not implemented or only serves fresh data. No concept of "acceptable staleness." When the upstream fails, there's no fallback.

**How to avoid:**
- Implement stale-while-revalidate pattern: serve cached data immediately while fetching fresh in background
- Store both "fresh" and "stale" cache copies with different TTLs
- Show visual indicator when displaying stale data (timestamp, "cached" badge)
- Set different TTLs based on data volatility (live activity = shorter TTL, history = longer TTL)

**Warning signs:**
- Dashboard goes blank during brief network hiccups
- No timestamp showing when data was last updated
- Users can't tell if they're seeing current or old data

**Phase to address:** Foundation/backend work (caching layer)

---

### Pitfall 4: Synchronous Sequential API Calls

**What goes wrong:**
Dashboard latency equals sum of all Tautulli response times. With 5 servers averaging 300ms each, page takes 1.5 seconds to load — just for data retrieval.

**Why it happens:**
Using sequential HTTP requests instead of concurrent fetching. Each API call waits for the previous to complete before starting.

**How to avoid:**
- Use async/await with httpx or aiohttp for concurrent fetching
- Fire all requests in parallel with `asyncio.gather()`
- Total latency becomes "max(response_times)" not "sum(response_times)"
- Set per-request timeouts to prevent one slow server from blocking

**Warning signs:**
- Dashboard load time scales linearly with number of configured servers
- Adding a new Tautulli instance makes dashboard noticeably slower
- No loading states visible (appears frozen until all data arrives)

**Phase to address:** Foundation/backend work (async conversion)

---

### Pitfall 5: Inconsistent Data Schema Across Tautulli Instances

**What goes wrong:**
Different Tautulli versions or configurations return slightly different field names, causing display bugs or missing data on some servers.

**Why it happens:**
Tautulli API isn't perfectly stable across versions. Field names may vary (`rating_key` vs `key`, different date formats). Developers hardcode expected schema.

**How to avoid:**
- Implement response normalization layer — transform each Tautulli's response to internal schema
- Handle multiple date formats (Unix timestamp, ISO 8601, etc.)
- Add schema validation with fallback defaults for missing fields
- Log warnings when unexpected fields appear (helps catch version differences)

**Warning signs:**
- Some servers show blank fields that others populate correctly
- Date/time displays inconsistently across servers
- Field names differ between servers in debug/log output

**Phase to address:** Foundation/backend work (response normalization)

---

### Pitfall 6: No API Health Monitoring

**What goes wrong:**
No visibility into API reliability. Don't know which Tautulli instances are healthy, which have been failing, or trends in response times.

**Why it happens:**
Dashboard focuses only on happy-path data display. No metrics collection on API health, latency, or error rates.

**How to avoid:**
- Track per-server metrics: success rate, latency percentiles, error counts
- Generate health report showing status of each configured server
- Log warnings when server success rate drops below threshold (e.g., 95%)
- Surface this in UI: show server status indicators, last successful contact time

**Warning signs:**
- No way to tell from UI which servers are online
- Can't diagnose why dashboard seems "slow" (which server is the culprit?)
- No alerting when a server has been down for extended period

**Phase to address:** Foundation/health checks (monitoring infrastructure)

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip response normalization | Faster initial implementation | Schema changes break display in subtle ways | Never — normalize from day one |
| No caching | Simpler code | Rate limit issues, slow dashboard | Only for MVP with single server |
| Hardcode refresh intervals | No configuration needed | Can't tune for different use cases | MVP only, must add settings |
| Ignore HTTP errors | Fewer code branches | Silent failures, confusing UI | Never — always handle errors explicitly |
| Single global timeout | Simpler concurrency | One slow server blocks all | Never — per-server timeouts required |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Tautulli API | Not handling API key changes/rotations | Store API keys securely, validate on config save, handle 401 gracefully |
| Tautulli API | Ignoring SSL certificate errors | Validate certs in development; allow configurable verification for self-hosted |
| Sonarr/Radarr | Not handling partial availability | If Sonarr isn't configured, hide Sonarr-dependent features gracefully |
| Plex API | Token expiration not handled | Refresh tokens before expiry, handle 401 with re-auth flow |
| Multiple Tautulli | Treating all servers equally | Respect that servers may have different versions, network latency, data volume |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| No pagination on history | Loading thousands of rows per server | Request limited/paginated history | At ~10k+ history items per server |
| N+1 queries for metadata | Each stream triggers separate API call | Batch metadata fetching, cache library info | With >20 concurrent streams |
| Re-fetching static data | Library names, user list on every refresh | Cache metadata with long TTL (hours) | Every page load becomes slow |
| No query optimization | Requesting full fields when only IDs needed | Request only needed fields from API | With limited bandwidth or many servers |
| Blocking UI on data fetch | Server-side rendering waits for all APIs | Use progressive loading, show available data first | With >3 servers or slow network |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing API keys in plaintext | Exposed if config file is leaked | Encrypt API keys at rest, use secrets manager |
| No authentication on dashboard | Public exposure of viewing habits | Require HTTP Basic auth or reverse proxy auth |
| Allowing actions without confirmation | Accidental Sonarr/Radarr operations | Require confirmation for write operations |
| Logging API keys | Keys appear in server logs | Sanitize all logged requests, never log raw API keys |
| No rate limiting on actions | Abuse of Sonarr/Plex operations | Rate limit write actions (already implemented in project) |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No server identification | Can't tell which server data comes from | Show server name/label prominently in each data section |
| All metrics look identical | No visual hierarchy, don't know where to look | Use color coding by server, size differentiation for important metrics |
| No "last updated" timestamp | Users don't know if data is fresh | Show relative and absolute timestamps |
| Loading state is invisible | Appears frozen during data fetch | Show loading indicators per-server, not just global spinner |
| Error messages are technical | Users can't diagnose problems | Show friendly messages with actionable next steps |
| No way to exclude servers | Can't hide problematic servers temporarily | Allow enabling/disabling servers without deleting config |

---

## "Looks Done But Isn't" Checklist

- [ ] **Multi-server aggregation:** Often missing per-server error handling — verify each server failure is isolated
- [ ] **Live activity view:** Often missing real-time updates — verify polling works and doesn't miss events
- [ ] **History merge:** Often missing deduplication — verify same item played on multiple servers isn't duplicated
- [ ] **Stale library detection:** Often missing Sonarr integration validation — verify when Sonarr is unavailable, feature degrades gracefully
- [ ] **Settings page:** Often missing config validation — verify invalid API keys are caught before save
- [ ] **Authentication:** Often missing session timeout — verify auth re-prompt happens appropriately

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Server down temporarily | LOW | Data auto-recovers when server returns; users may need manual refresh |
| Rate limit exceeded | MEDIUM | Wait for rate limit window; implement backoff to prevent recurrence |
| API key invalidated | MEDIUM | User must re-enter key in settings; prompt clearly when auth fails |
| Cache corruption | LOW | Clear cache; data refetches from source |
| Schema mismatch | HIGH | Update normalization layer; may need server-specific transformations |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| No graceful degradation | Initial UI/UX improvements | Test with one server offline; remaining data still visible |
| Rate limit issues | Foundation (backend work) | Monitor 429 errors; verify caching reduces API calls |
| No stale-data fallback | Foundation (caching) | Test with Tautulli offline; verify stale data displays |
| Sequential API calls | Foundation (async) | Measure load time with multiple servers; should be ~max not sum |
| Schema inconsistency | Foundation (normalization) | Verify all servers return same field names in UI |
| No health monitoring | Foundation/health checks | Verify server status indicators work |
| Performance at scale | Future optimization | Test with many servers, large history |

---

## Sources

- Multi-source dashboard patterns: BIX Tech's "Best Practices for Building Grafana Dashboards with Multiple Data Sources" (2025)
- API aggregation pitfalls: Tim Derzhavets "Aggregating Data from Multiple APIs: Patterns and Pitfalls" (Feb 2026)
- Dashboard mistakes: Data Never Lies "Top 7 Dashboard Mistakes of 2025"
- Tautulli community: GitHub issues on multi-server support, Plex forum discussions
- General monitoring best practices: Prometheus/Grafana ecosystem patterns

---

*Pitfalls research for: Multi-source Tautulli/Plex monitoring dashboard*
*Researched: 2026-03-29*
