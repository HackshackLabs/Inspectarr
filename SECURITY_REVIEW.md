# Security review: Insecpectarr

**Scope:** Application code under `src/inspectarr/`, configuration patterns, and deployment assumptions.  
**Method:** Trust-boundary review, OWASP-oriented risk categories, and concrete remediation guidance (not exploit steps).

---

## System overview (threat model sketch)

| Item | Notes |
|------|--------|
| **Architecture** | Insecpectarr: Python FastAPI app; server-side templates (Jinja2); JSON file + SQLite caches for config and data. |
| **Trust boundaries** | Browser ↔ app (HTTP Basic optional); app ↔ Tautulli / Sonarr / Plex / Plex.tv (admin-configured URLs and secrets). |
| **Sensitive data** | Tautulli and Sonarr API keys, Plex tokens, Basic auth password, dashboard JSON at `DASHBOARD_CONFIG_PATH`, optional SQLite DB paths. |
| **Attackers** | Unauthenticated network clients; authenticated users of the app (treated as admins); anyone who can read the host filesystem or backups. |

---

## Positive controls (already in place)

1. **Timing-safe Basic auth comparison** — `secrets.compare_digest` on username and password bytes in `auth_middleware.py` reduces user enumeration and timing leaks versus naive string compare.
2. **Basic auth credentials not overridable from JSON** — `dashboard_config.apply_dashboard_overrides` strips `basic_auth_*` from file overrides so stored config cannot weaken env-only credentials.
3. **Plex delete `rating_key` validation** — Numeric pattern check in `plex_client.py` before calling PMS reduces injection-style URL manipulation for that segment.
4. **Upstream error sanitization** — `tautulli_client._sanitize_error_message` limits what upstream failures surface as user-visible errors.
5. **Secrets file hygiene** — `.gitignore` excludes `data/dashboard_config.json` and local env files; project docs call out not committing secrets.
6. **Jinja2 autoescaping** — Presentation fields and error banners use `{{ ... }}` / `| e` patterns that avoid obvious reflected/stored HTML injection in typical FastAPI+Jinja setups.

---

## Findings (prioritized)

Severity uses **Critical / High / Medium / Low / Informational**. Each item includes **remediation**.

### High — Default Basic auth password in source and example env

**Risk:** `Settings.basic_auth_password` defaults to a known string (`settings.py`), and `.env.example` documents the same value. Anyone who deploys without overriding it exposes the app with a trivially guessable password.

**Remediation:**

- Remove hardcoded default passwords from code; require `BASIC_AUTH_PASSWORD` when `BASIC_AUTH_ENABLED=true` (fail fast at startup), or generate a random password once and print/log instructions on first run (without logging the secret itself).
- In `.env.example`, use a placeholder only (e.g. `BASIC_AUTH_PASSWORD=change_me_generate_strong_secret`) and document minimum length/complexity.

---

### High — Stateful side effect on GET (`/settings/plex-auth/check`)

**Risk:** Saving Plex tokens on **GET** violates safe-method semantics (RFC 9110). Proxies, prefetchers, browser extensions, or cached links could theoretically trigger unintended writes; it also complicates auditing and caching policies.

**Remediation:**

- Change the “check pin and persist token” flow to **POST** (or PATCH) with `pin_id` and `profile` in the body; keep GET idempotent (e.g. read-only status).
- Update the settings page `fetch` poll to use POST + CSRF token (see below).

---

### Medium — No CSRF protection for state-changing requests

**Risk:** All destructive or sensitive operations are plain form POSTs or `fetch` without anti-CSRF tokens: `/settings`, Sonarr actions, Plex delete, Plex auth start. With **HTTP Basic**, browser behavior for cross-site submissions is inconsistent across versions; relying on that alone is fragile. Defense in depth favors explicit tokens or SameSite session cookies (not applicable as-is to Basic-only flows).

**Remediation:**

- Issue a random **CSRF token** per session (signed cookie or server-side session) and require it on every mutating POST (forms + JSON APIs used from the UI).
- For JSON endpoints, accept `X-CSRF-Token` (or double-submit cookie) in addition to Basic auth.
- Prefer **POST** for all mutations; avoid GET writes entirely.

---

### Medium — Missing standard security headers (no reverse proxy assumed)

**Risk:** The FastAPI app does not set `Content-Security-Policy`, `X-Frame-Options` / `frame-ancestors`, `X-Content-Type-Options`, `Referrer-Policy`, or `Permissions-Policy`. Clickjacking and some XSS-class abuse are easier if HTML is ever introduced with weaker escaping or third-party scripts.

**Remediation:**

- Add headers via middleware or document **mandatory** reverse proxy headers (see `SecurityHeadersMiddleware` in `security_middleware.py` for app defaults). Start with `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` minimal defaults, and `Content-Security-Policy` appropriate to inline styles/scripts currently used on `/settings`.

---

### Medium — Uploaded logo as SVG (stored XSS if opened as document)

**Risk:** `.svg` uploads are allowed and served under `/uploads/`. In an `<img>` context, script execution is generally suppressed by browsers, but **opening the SVG URL directly** (same origin) can execute script in some configurations. Any authenticated admin can upload; impact is mainly against other users of the same origin.

**Remediation:**

- Disallow SVG uploads, **or** rasterize server-side, **or** serve user uploads from a **separate cookieless origin** with `Content-Disposition: attachment` for non-image-safe types, **or** sanitize SVG with a strict allowlist (no `<script>`, no event handlers, no `foreignObject`).

---

### Medium — Admin-configured upstream URLs (SSRF-by-design)

**Risk:** `TautulliServer.base_url`, `Sonarr`, and `PlexServer.base_url` are chosen by whoever controls `/settings` or env. A compromised admin account (or insider) can point the server at cloud metadata endpoints or internal IPs, subject to where the app runs.

**Remediation:**

- Document that the app must run in a network segment without sensitive internal reachability, or implement optional **URL allowlists** / blocklists (e.g. deny RFC1918, link-local, metadata IPs) behind a feature flag.
- Run the container/VM without metadata credentials where possible.

---

### Low — Unauthenticated `/healthz`

**Risk:** By design, `/healthz` bypasses Basic auth (`auth_middleware.py`). This aids orchestration but reveals liveness and app identity to scanners.

**Remediation:**

- Accept the tradeoff for Kubernetes, **or** protect with a separate lightweight secret query param or network policy, **or** expose health only on an admin bind address.

---

### Low — Brute force and abuse: no rate limiting

**Risk:** No application-level throttling on Basic auth failures or heavy endpoints (history crawl, exports). Enables credential guessing and resource exhaustion against a small deployment.

**Remediation:**

- Add rate limiting (e.g. `slowapi` or reverse proxy limits) per IP on `/` and auth-protected routes; stricter limits on `/settings` POST and upstream-heavy routes.

---

### Low — Error and upstream detail in API responses

**Risk:** Some handlers return slices of upstream response bodies (e.g. Plex HTTP errors in `routes_library_plex.py`). That can leak internal paths or implementation details to any authenticated client.

**Remediation:**

- Log full upstream errors server-side; return generic messages to clients with a correlation id.

---

### Informational — Plex validate endpoint returns username/email

**Risk:** `/settings/plex-auth/validate` returns `username` and `email` in JSON. This is acceptable for a single-admin tool but is mild **information disclosure** if credentials are shared.

**Remediation:**

- Gate detailed fields behind a debug flag or omit email by default.

---

### Informational — No automated security pipeline in repo

**Gap:** Beyond `.github/workflows/security.yml`, there is room to expand CI with SAST (Semgrep), dependency scanning (Trivy/pip-audit), or secret scanning (Gitleaks).

**Remediation:**

- Add a `Security` workflow on pull requests: `pip-audit` or `uv pip audit`, optional Semgrep rulesets (`p/owasp-top-ten`), and secret scanning on the tracked paths.

---

## Summary table

| Severity | Topic | Primary remediation |
|----------|--------|---------------------|
| High | Known default password | Require strong secret at deploy; no default in code |
| High | GET mutates Plex token store | POST-only token persistence |
| Medium | CSRF | Tokens on all state-changing requests |
| Medium | Security headers | Middleware or reverse proxy CSP + framing policy |
| Medium | SVG uploads | Disallow, sanitize, or isolate serving |
| Medium | Upstream URL trust | Network segmentation and/or URL policy |
| Low | `/healthz` exposure | Document or restrict by network |
| Low | Rate limiting | Proxy or app-level limits |
| Low | Verbose errors | Generic client errors + server logs |
| Info | Validate response fields | Minimize PII in JSON |
| Info | CI security | pip-audit + SAST + secrets scan |

---

## References

- OWASP: [CSRF](https://owasp.org/www-community/attacks/csrf), [SSRF](https://owasp.org/www-community/attacks/Server_Side_Request_Forgery), secure headers cheat sheets.

---

*This document is a point-in-time review; re-run after major feature or dependency changes.*
