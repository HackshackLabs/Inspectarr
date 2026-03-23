# Deployment Guide

This document describes safe deployment patterns for `inspectarr` outside localhost.

## Recommended pattern

Use:

- `inspectarr` bound on a private interface or localhost
- Reverse proxy for TLS termination
- Authentication: **built-in HTTP Basic** (`.env`: `BASIC_AUTH_*`, see `docs/CONFIGURATION.md`) and/or at the **reverse proxy** (basic auth or SSO)

Do not expose the app directly to the internet without auth (change default Basic credentials at minimum).

## Reverse proxy auth options

### Option A: Basic auth (quickest)

- Suitable for small private operations.
- Implement in Nginx/Caddy/Traefik.
- Store credentials outside repo; rotate periodically.

### Option B: SSO forward-auth (preferred for teams)

- Use OAuth2/OIDC provider (for example Authentik, Authelia, Cloudflare Access).
- Reverse proxy performs auth and forwards trusted identity headers.
- Keep app internal-only and trust only proxy network path.

## Network recommendations

- Restrict inbound access by IP/VPN whenever possible.
- Keep outbound access only to configured Tautulli servers.
- Monitor reverse proxy access logs for unusual patterns.

## Container deployment

1. Build image:
   - `docker build -t inspectarr:latest .`
2. Run with environment file:
   - `docker run --rm -p 8000:8000 --env-file .env.local inspectarr:latest`
3. Put container behind reverse proxy for TLS and auth.

## Security reminders

- Never bake API keys into image layers.
- Never commit `.env`, `.env.local`, or equivalent secret files.
- Keep `TAUTULLI_SERVERS_JSON` values in runtime env or secret store.
- If you enable Sonarr integration, treat `SONARR_API_KEY` like other secrets; it can unmonitor series and delete files on disk via Sonarr (see `docs/SONARR.md`).
- Persist `DASHBOARD_CONFIG_PATH` and the adjacent `uploads/` directory if operators use `/settings` (see `docs/CONFIGURATION.md`).
