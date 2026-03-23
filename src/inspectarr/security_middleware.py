"""Security headers and CSRF cookie / API CSRF enforcement."""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from inspectarr.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Paths where unsafe methods validate CSRF in the route (e.g. multipart form) instead of here.
_FORM_CSRF_PATHS: frozenset[tuple[str, str]] = frozenset({("/settings", "POST")})


def _csrf_exempt_path(path: str) -> bool:
    if path == "/healthz":
        return True
    if path.startswith("/uploads"):
        return True
    if path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
        return True
    return False


def _csrf_cookie_value(request: Request) -> tuple[str, str | None]:
    """
    Return (token_for_state, pending_set_cookie_value).

    If the client has no CSRF cookie, generate a token and schedule Set-Cookie.
    """
    existing = (request.cookies.get(CSRF_COOKIE_NAME) or "").strip()
    if existing:
        return existing, None
    token = secrets.token_urlsafe(32)
    return token, token


def _build_csrf_set_cookie(token: str, *, secure: bool) -> str:
    """Set-Cookie value for the CSRF double-submit token (HttpOnly; JS uses meta tag)."""
    bits = [
        f"{CSRF_COOKIE_NAME}={token}",
        "Path=/",
        "SameSite=Lax",
        "HttpOnly",
    ]
    if secure:
        bits.append("Secure")
    return "; ".join(bits)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';",
        )
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    """
    Issue CSRF cookie; for mutating non-form routes require ``X-CSRF-Token`` matching the cookie.

    ``POST /settings`` validates the token from the form body in the route handler.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        token, pending = _csrf_cookie_value(request)
        request.state.csrf_token = token

        method = request.method.upper()
        path = request.url.path
        if method not in _SAFE_METHODS and not _csrf_exempt_path(path):
            key = (path, method)
            if key not in _FORM_CSRF_PATHS:
                submitted = (request.headers.get(CSRF_HEADER_NAME) or "").strip()
                if not submitted or not secrets.compare_digest(
                    token.encode("utf-8"),
                    submitted.encode("utf-8"),
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF validation failed"},
                    )

        response = await call_next(request)
        if pending:
            secure = request.url.scheme == "https"
            response.headers.append("set-cookie", _build_csrf_set_cookie(pending, secure=secure))
        return response
