# Stack Research

**Domain:** Python Web Dashboard with Tautulli/Plex Monitoring Integration
**Researched:** 2026-03-29
**Confidence:** HIGH

## Executive Summary

The existing stack (Python 3.11+, FastAPI, Jinja2, SQLite) is well-suited for this project. Recommendations focus on enhancing the current architecture with HTMX/Alpine.js for interactivity without SPA complexity, proper async HTTP clients for Tautulli API communication, and visualization libraries for dashboard enhancements.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Python** | 3.11+ | Runtime | Already in use. Python 3.11 provides significant performance improvements (25% faster than 3.10) and better async support. |
| **FastAPI** | >=0.115.0 | Web Framework | Already in use. Industry standard for Python web APIs. Excellent async support, automatic OpenAPI docs, and native Pydantic integration. |
| **Jinja2** | (bundled with FastAPI) | Template Engine | Already in use. Server-rendered UI keeps deployment simple. Pair with HTMX for dynamic updates. |
| **httpx** | >=0.28.0 | HTTP Client | **Critical addition.** Provides sync AND async HTTP calls. Replaces direct `requests` usage for Tautulli API calls. Supports connection pooling and timeouts. |
| **Pydantic** | >=2.9.0 | Data Validation | Already in use with FastAPI. Use for Tautulli API response modeling. |

### Tautulli API Integration

| Library | Version | Purpose | Why Recommended |
|---------|---------|---------|-----------------|
| **pytulli** | >=4.6.8 | Tautulli Python Client | Official Python client for Tautulli API. Handles authentication, endpoint mapping, and response parsing. However, has maintenance concerns (see below). |
| **httpx** (direct) | >=0.28.0 | Direct API Calls | **Recommended over pytulli.** More control over requests, better maintenance, no额外 dependency. Simple wrapper around Tautulli API. |

**Recommendation:** Build a thin wrapper around httpx for Tautulli API calls rather than using pytulli. The pytulli library has limited maintenance (6 stars, last update 2020) and may not track Tautulli's evolving API. A custom wrapper gives full control.

### Frontend Enhancements

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **HTMX** | >=1.9.10 | Server-side interactivity | **Highly recommended.** Enables dynamic updates (live activity refresh, history filtering) without JavaScript complexity. Works perfectly with Jinja2/FastAPI. |
| **Alpine.js** | >=3.14.0 | Client-side interactivity | Use for simple UI state (modals, dropdowns, toggles). Lightweight (~15KB) alternative to React/Vue. |
| **Chart.js** | >=4.4.0 | Data Visualization | Use for history trends, viewing patterns. Canvas-based, good performance. |
| **Tailwind CSS** | >=3.4.0 | Styling | Optional but recommended for maintainable CSS. CDN version works for simple deployments. |

**Why HTMX + Alpine.js:** This combination with FastAPI/Jinja2 creates a "modern server-rendered" architecture. You get SPA-like interactivity without the complexity of a separate frontend build system, client-side state management, or API layer between frontend and backend.

### Database & Caching

| Technology | Version | Purpose | When to Use |
|------------|---------|---------|-------------|
| **SQLite** | (built-in) | Local Cache | Already in use. Good for single-instance deployments, embedded use cases. |
| **PostgreSQL** | >=16.0 | Production Database | Use if multiple users, need concurrent access, or want robust backup options. SQLAlchemy 2.0+ handles both seamlessly. |
| **SQLAlchemy** | >=2.0.0 | ORM | Use for database abstraction. Supports both SQLite and PostgreSQL with same code. Async support. |
| **cachetools** | >=5.5.0 | In-memory Caching | Use for Tautulli API response caching. TTL-based expiration. |

**Recommendation:** Keep SQLite for now. PostgreSQL only needed if: (1) multi-user concurrent writes, (2) need for robust replication/backup, (3) deployment on platforms with ephemeral filesystems.

### Data Visualization

| Library | Purpose | When to Use |
|---------|---------|-------------|
| **Chart.js** | Line/bar charts | History trends, viewing statistics |
| **Apache ECharts** | Advanced charts | Heatmaps, geographic data (Plex server locations) |
| **Tabulator** | Data tables | History tables, server lists with sorting/filtering |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| **uv** | Package Manager | **Recommended over pip.** 10-100x faster, better dependency resolution. Drop-in replacement: `uv pip install -r requirements.txt` |
| **pytest** | Testing | Already in use. Add `pytest-asyncio` for async tests. |
| **ruff** | Linting/Formatting | 10-100x faster than flake8/black. Replace both with single tool. |
| **HTTPX** | Test Client | Use `httpx.AsyncClient` for testing FastAPI apps (built-in support). |

## Installation

```bash
# Core runtime (already have)
# Python 3.11+

# Recommended additions
uv pip install httpx>=0.28.0
uv pip install pydantic>=2.9.0
uv pip install sqlalchemy>=2.0.0
uv pip install cachetools>=5.5.0

# Development
uv pip install -D pytest>=8.0.0
uv pip install -D pytest-asyncio>=0.24.0
uv pip install -D ruff>=0.8.0

# Frontend (via CDN or npm)
# HTMX: https://unpkg.com/htmx.org@1.9.10
# Alpine.js: https://unpkg.com/alpinejs@3.14.0
# Chart.js: https://cdn.jsdelivr.net/npm/chart.js
# Tailwind: https://cdn.tailwindcss.com
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| FastAPI + Jinja2 + HTMX | Streamlit | Only if dashboard is pure data viz with no custom UI needs. Streamlit limits customization. |
| FastAPI + Jinja2 + HTMX | Dash (Plotly) | Only if building data-science focused app. Dash has steeper learning curve for custom UIs. |
| httpx (direct) | pytulli library | pytulli if you want quickstart and don't need advanced Tautulli features. Build custom wrapper for control. |
| SQLite | PostgreSQL | PostgreSQL only if need concurrent writes, replication, or complex queries. SQLite simpler for single-user. |
| Chart.js | Plotly.py | Plotly has better Python integration but larger bundle. Chart.js more flexible for custom JS. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **requests library** | Blocking only, no async support. Poor fit for FastAPI's async model. | httpx (supports both sync and async) |
| **pytulli** (as primary) | Limited maintenance (last update 2020, 6 stars). May not track Tautulli API changes. | Custom httpx wrapper |
| **React/Vue/Angular** | Adds significant complexity: build system, client-side routing, state management, separate API layer. | HTMX + Alpine.js |
| **Django** | Overkill for this use case. Heavy ORM, complex URL routing, template system less flexible than Jinja2. | FastAPI (keep current) |
| **Flask** | Less async-native than FastAPI. Requires more boilerplate for same functionality. | FastAPI (keep current) |
| **WebSockets** | Out of scope per PROJECT.md. Polling suffices for current needs. | HTMX polling or SSE if real-time needed later |

## Stack Patterns by Variant

**If adding real-time live activity later:**
- Add Server-Sent Events (SSE) endpoint in FastAPI
- Use HTMX's `hx-trigger="sse"` for automatic updates
- No need for full WebSocket complexity

**If adding user authentication later:**
- Use `fastapi-users` library (supports OAuth, JWT, database backends)
- Already integrates with FastAPI and Pydantic

**If adding complex data aggregation:**
- Use SQLAlchemy 2.0 with async sessions
- Consider Celery for background tasks if processing is heavy

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| FastAPI >=0.115.0 | Pydantic >=2.9.0 | FastAPI requires Pydantic v2. Check pyproject.toml. |
| httpx >=0.28.0 | Python 3.8+ | Supports both sync and async. |
| SQLAlchemy >=2.0.0 | Python 3.9+ | Major rewrite with async support. |
| Pydantic >=2.9.0 | Python 3.9+ | Pydantic v2 has different API than v1. |
| HTMX >=1.9.10 | Any browser | No Python dependency. |
| Alpine.js >=3.14.0 | Any browser | No Python dependency. |

## Sources

- **FastAPI GitHub** (https://github.com/fastapi/fastapi) — Version requirements, Pydantic compatibility
- **Tautulli API Reference** (https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference) — Official API documentation
- **pytulli PyPI** (https://pypi.org/project/tautulli/) — Python client, version info
- **httpx Documentation** (https://www.python-httpx.org/) — Async HTTP client
- **HTMX Documentation** (https://htmx.org/) — Server-side interactivity
- **Web search: FastAPI HTMX best practices 2026** — Architecture patterns
- **Web search: Python monitoring dashboard stack 2025** — Industry standards

---

*Stack research for: Tautulli/Plex Monitoring Dashboard*
*Researched: 2026-03-29*
*Confidence: HIGH*
