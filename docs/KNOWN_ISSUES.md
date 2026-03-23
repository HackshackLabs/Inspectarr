# Known Issues and Risks

This document tracks identified potential issues for the multi-server dashboard, with expected impact and mitigation guidance.

Status values:

- `open`: risk exists and is not yet fully mitigated
- `mitigating`: partial mitigation exists, further work planned
- `accepted`: known trade-off accepted for current phase

## Issue register

| ID | Issue | Impact | Current mitigation | Status | Follow-up |
| --- | --- | --- | --- | --- | --- |
| KI-001 | Global pagination across multiple Tautulli servers can be incorrect when using identical `start`/`length` per server. | Missing or reordered events in combined history timeline. | Use global merge-sort strategy with over-fetch and trim. | mitigating | Implement and test merged pagination semantics in P1. |
| KI-002 | History timestamps may differ by field and format (`started`, `date`, timezone differences). | Incorrect ordering and misleading time display. | Normalize to canonical UTC epoch before merge and render. | mitigating | Continue broadening tests against real-world payload variants. |
| KI-003 | User identity can drift across servers (different usernames/ids for same person). | Filters and attribution can be inconsistent across merged views. | Always include `server_id`/`server_name`; treat cross-server identity filters as best-effort. | open | Add optional identity map and user reconciliation workflow in future phase. |
| KI-004 | Tautulli API key appears in query string parameters. | Secret leakage via logs, traces, or copied URLs. | Client logging redacts `apikey` in URLs and exception text; avoid raw URL logging. | mitigating | Add integration tests around representative HTTP client exception formats. |
| KI-005 | Frequent fan-out refreshes can overload low-power upstream hosts. | Increased latency/timeouts and reduced dashboard reliability. | Live activity uses stale-while-revalidate cache, history supports optional SQLite TTL cache, and TV inventory indexing runs incrementally in chunks. | mitigating | Tune cache TTL values and inventory chunk sizes by server performance profile. |
| KI-006 | Partial outage can be misread as "no activity" if health indicators are not obvious. | Operator confusion and delayed incident response. | Render prominent per-server health and last-success indicators. | open | Ensure health strip is visible on all dashboard pages and states. |
| KI-007 | History-only unwatched reports can miss never-played media. | Media never played and never present in history can be omitted from stale/removable candidates. | Added inventory-joined TV report (`/insights/library-unwatched`) that traverses shows/seasons/episodes and joins to history index window. | mitigating | Extend inventory join to movies and add broader inventory pagination controls for very large libraries. |

## Notes

- This register complements technical detail in `docs/ARCHITECTURE.md`.
- Security-specific concerns are also summarized in `README.md`.
