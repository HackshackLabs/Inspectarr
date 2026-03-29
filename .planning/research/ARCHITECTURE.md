# Architecture Patterns: Multi-Source Monitoring Aggregation

**Project:** Tautulli Inspector (Scoparr)
**Researched:** 2026-03-29
**Domain:** Multi-source monitoring/aggregation dashboard

## Executive Summary

Multi-source monitoring aggregation systems follow a well-established pattern of **fan-out polling** across multiple backends, followed by **data normalization** and **fan-in aggregation** to present a unified view. For Tautulli aggregation specifically, the architecture must handle heterogeneous data sources (multiple Tautulli instances with potentially different configurations), implement resilience patterns to prevent cascade failures, and provide caching for graceful degradation when upstream services are unavailable.

The recommended architecture for this project follows a **layered adapter pattern** with clear component boundaries: source adapters → normalization layer → aggregation engine → caching layer → API/presentation layer.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client (Browser)                            │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    API Layer (FastAPI)                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
│  │ Activity     │ │ History      │ │ Library      │               │
│  │ Endpoints    │ │ Endpoints    │ │ Endpoints    │               │
│  └──────────────┘ └──────────────┘ └──────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Aggregation Engine                               │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  - Merges normalized data from all sources                    │  │
│  │  - Handles sorting, filtering, pagination                      │  │
│  │  - Deduplication logic                                         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Caching Layer                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  - TTL-based cache with per-source invalidation              │  │
│  │  - Fallback to cached data on upstream failure                │  │
│  │  - Cache warming on startup                                   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 Normalization Layer                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  - Maps heterogeneous API responses to unified schema         │  │
│  │  - Handles field name variations                              │  │
│  │  - Data type coercion                                         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
             ┌──────────┐  ┌──────────┐  ┌──────────┐
             │ Tautulli │  │ Tautulli │  │ Tautulli │
             │ Server 1 │  │ Server 2 │  │ Server N │
             └──────────┘  └──────────┘  └──────────┘
```

## Component Boundaries

### 1. Source Adapters (Tautulli Connectors)

**Responsibility:** Handle all communication with individual Tautulli instances.

| Component | Responsibility | Public Interface |
|-----------|----------------|------------------|
| `TautulliClient` | HTTP client wrapper with auth, rate limiting | `get_activity()`, `get_history()`, `get_libraries()` |
| `SourceRegistry` | Manages configured Tautulli instances | `add_source()`, `remove_source()`, `get_sources()` |
| `HealthChecker` | Monitors source availability | `check_health()`, `get_status()` |

**Communication:** Source adapters communicate only with their assigned Tautulli instance. They expose a standardized interface regardless of the underlying Tautulli version or configuration.

### 2. Normalization Layer

**Responsibility:** Transform heterogeneous API responses into a unified canonical schema.

| Component | Responsibility | Public Interface |
|-----------|----------------|------------------|
| `ActivityNormalizer` | Maps activity responses to unified schema | `normalize(activity_data, source_id)` |
| `HistoryNormalizer` | Maps history responses to unified schema | `normalize(history_data, source_id)` |
| `LibraryNormalizer` | Maps library responses to unified schema | `normalize(library_data, source_id)` |
| `FieldMapper` | Handles field name variations | `map_field(field_name)` |

**Canonical Schema Example:**
```python
class UnifiedActivity(BaseModel):
    source_id: str              # Identifier for source Tautulli
    session_id: str             # Unique session identifier
    user: str                  # Plex username
    media_title: str            # Title of media playing
    media_type: Literal["movie", "episode", "track"]
    state: Literal["playing", "paused", "buffering"]
    progress_percent: float     # 0-100
    started_at: datetime        # When session started
    ip_address: str             # Client IP
```

### 3. Aggregation Engine

**Responsibility:** Combine normalized data from multiple sources into unified views.

| Component | Responsibility | Public Interface |
|-----------|----------------|------------------|
| `ActivityAggregator` | Merges live activity from all sources | `aggregate()`, `get_unified_activity()` |
| `HistoryAggregator` | Merges history with deduplication | `aggregate()`, `get_unified_history()` |
| `StaleLibraryFinder` | Identifies unwatched content | `find_stale()`, `get_stale_by_server()` |

**Key Operations:**
- **Merging:** Combine results from N sources into sorted, paginated views
- **Deduplication:** Handle same content played on multiple servers
- **Sorting:** Unified sorting across heterogeneous sources
- **Filtering:** Apply filters post-aggregation

### 4. Caching Layer

**Responsibility:** Provide fast reads with graceful degradation on upstream failure.

| Component | Responsibility | Public Interface |
|-----------|----------------|------------------|
| `CacheManager` | TTL management, invalidation | `get()`, `set()`, `invalidate()` |
| `SourceCache` | Per-source caching with independent TTL | `cache_source_data()`, `get_cached()` |
| `FallbackHandler` | Serve stale cache on upstream failure | `get_with_fallback()` |

**Caching Strategy:**
- **Cache-aside pattern:** Check cache first, fetch from source on miss
- **Per-source TTL:** Different cache durations per data type (activity: 15s, history: 5m, libraries: 1h)
- **Stale-while-revalidate:** Serve stale content while refreshing in background
- **Graceful degradation:** Return cached data when source is unavailable

### 5. API Layer (FastAPI)

**Responsibility:** Expose HTTP endpoints for the UI.

| Endpoint | Returns | Cache TTL |
|----------|---------|-----------|
| `GET /api/activity` | Unified live activity from all servers | 15 seconds |
| `GET /api/history` | Unified play history | 5 minutes |
| `GET /api/libraries` | Combined library stats | 1 hour |
| `GET /api/stale` | Unwatched content recommendations | 1 hour |
| `GET /api/sources` | Source status and health | 30 seconds |

## Data Flow

### Primary Flow: Activity Monitoring

```
1. UI requests: GET /api/activity
2. API Layer checks cache (CacheManager)
3. Cache hit? → Return cached unified data
4. Cache miss? → 
   a. ActivityAggregator dispatches parallel requests to all SourceAdapters
   b. Each TautulliClient fetches from its assigned server
   c. Raw responses pass through ActivityNormalizer
   d. Normalized data returns to ActivityAggregator
   e. ActivityAggregator merges, sorts, deduplicates
   f. Result cached and returned to client
```

### Alternative Flow: Source Failure

```
1. UI requests: GET /api/activity
2. Cache miss triggers fetch
3. Fan-out to SourceAdapters:
   - Source 1: SUCCESS → normalized data
   - Source 2: TIMEOUT → circuit breaker opens
   - Source 3: SUCCESS → normalized data
4. Aggregation with partial results + cached data for Source 2
5. Return partial success with source_status indicating issues
```

### Stale Library Analysis Flow

```
1. UI requests: GET /api/stale
2. Cache check
3. On cache miss:
   a. Fetch libraries from all sources (fan-out)
   b. Fetch play history from all sources (fan-out)
   c. Normalize both datasets
   d. StaleLibraryFinder computes:
      - Content added > threshold ago
      - Never played OR last played > threshold ago
      - Optionally: linked to Sonarr for metadata
   e. Aggregate stale content across servers
4. Cache and return
```

## Build Order & Dependencies

Based on the component architecture, the following build order minimizes integration friction:

### Phase 1: Source Adapter Layer
**Priority: HIGH** — Foundation for all other components

- `TautulliClient` with async HTTP and auth
- `SourceRegistry` for configuration management
- Basic health checking

**Dependencies:** None (ground floor)
**Rationale:** Must have reliable source communication before normalization or aggregation can be tested.

### Phase 2: Normalization Layer
**Priority: HIGH** — Depends on source adapters

- Schema definitions (Pydantic models)
- Field mappers for each data type
- Normalizer implementations

**Dependencies:** Source adapters (for raw data input)
**Rationale:** Normalization transforms source-specific data into the canonical format needed by aggregation.

### Phase 3: Aggregation Engine
**Priority: HIGH** — Depends on normalization

- ActivityAggregator
- HistoryAggregator  
- StaleLibraryFinder

**Dependencies:** Normalization layer (for unified data format)
**Rationale:** Aggregation operates on normalized data; this is where deduplication and sorting happen.

### Phase 4: Caching Layer
**Priority: MEDIUM** — Can be added after core logic

- CacheManager with TTL
- Per-source invalidation
- Fallback handling

**Dependencies:** Aggregation engine (cache operates on aggregated results)
**Rationale:** Caching is an optimization; core functionality works without it but benefits significantly.

### Phase 5: API Layer
**Priority: MEDIUM** — Connects to existing UI

- FastAPI endpoints
- Request validation
- Error handling

**Dependencies:** All above layers (consumer of everything)
**Rationale:** The API layer orchestrates everything; build after the components exist.

### Phase 6: Resilience Patterns
**Priority: MEDIUM** — Enhances robustness

- Circuit breakers per source
- Retry logic with exponential backoff
- Timeout handling

**Dependencies:** All network communication (SourceAdapter, Caching, API)
**Rationale:** Resilience wraps existing operations; can be added incrementally.

## Scalability Considerations

| Concern | At 3 Servers | At 10 Servers | At 50 Servers |
|---------|--------------|---------------|---------------|
| **Polling load** | 3 concurrent requests | 10 concurrent requests | Batch polling, consider dedicated workers |
| **Cache efficiency** | Simple in-memory cache | Redis or SQLite recommended | Redis cluster required |
| **Response time** | < 500ms typical | < 1s typical | Async aggregation with streaming |
| **Circuit breaker** | Per-source simple | Per-source with shared state | Distributed circuit breaker |

### Horizontal Scaling Notes

For larger deployments (>20 Tautulli instances):
- Move from synchronous fan-out to async task queue (Celery/BackgroundTasks)
- Implement result streaming (Server-Sent Events) for long-polling operations
- Consider read replicas for cache layer
- Add dedicated health monitoring for source availability

## Anti-Patterns to Avoid

### 1. Direct API Aggregation
**What:** Calling all Tautulli instances sequentially in a single request
**Why bad:** Response time scales linearly with source count; one slow source blocks all
**Instead:** Parallel async fetching with configurable timeouts

### 2. No Normalization Layer
**What:** Trying to aggregate raw responses directly
**Why bad:** Different Tautulli versions may have field variations; handling in aggregation layer creates spaghetti code
**Instead:** Explicit normalization step with schema validation

### 3. Aggressive Caching Without Invalidation
**What:** Long TTLs without proper invalidation strategy
**Why bad:** Stale data feels broken; users lose trust
**Instead:** TTL-based with manual invalidation on explicit actions (refresh button)

### 4. Single Point of Failure
**What:** No fallback when source is down
**Why bad:** One bad server renders dashboard unusable
**Instead:** Graceful degradation; show available sources with clear status indicators

## Data Model Relationships

```
UnifiedActivity ─────┬─── SourceRegistry
                      │        │
                      │        ▼
                      │   TautulliClient
                      │        │
                      │        ▼
                      │   Tautulli API
                      │
UnifiedHistory  ─────┤
                      │
                      │
UnifiedLibrary ──────┘
```

## Sources

- OpenTelemetry Collector Gateway Patterns (2026) — Multi-source aggregation architecture
- Fan-Out/Fan-In Pattern — Microsoft Azure Durable Functions documentation
- FastAPI Best Practices — Modern FastAPI Architecture Patterns (2025)
- Circuit Breaker in Python — OneUptime Technical Guides (2026)
- Tautulli_Combined — Reference implementation (GitHub: jsgiacomi/Tautulli_Combined)
- pytulli — Python Tautulli client library (nwithan8/pytulli)
- Data Normalization Patterns — DEV Community technical documentation

---

**Confidence Level:** MEDIUM
**Rationale:** Architecture patterns are well-established for multi-source aggregation. Tautulli-specific implementation details require verification against actual API responses from the target Tautulli versions. The general fan-out/fan-in pattern is a proven approach for this use case.
