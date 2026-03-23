# Decisions

This file tracks lightweight ADR-style decisions.

## 2026-03-22 - Use FastAPI + server-rendered templates for MVP

Status: accepted

Context:

- Goal is a small operational dashboard, not a full frontend platform.
- Need quick iteration with minimal build tooling overhead.

Decision:

- Use FastAPI with Jinja2 templates for initial delivery.
- Keep optional htmx support available for incremental live refresh behavior.

Consequences:

- Faster MVP implementation with fewer moving parts.
- Easier deployment footprint for internal tooling.
- If UI complexity grows significantly, a SPA can be introduced later.

## 2026-03-22 - Keep `server_id` on every merged row

Status: accepted

Context:

- Data is aggregated from multiple Tautulli instances and identities may drift over time.

Decision:

- Preserve `server_id` and display context in activity/history rows.

Consequences:

- Better traceability for operations and debugging.
- Enables future per-server actions and reconciliation workflows.
