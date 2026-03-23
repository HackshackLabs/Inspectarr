"""HTTP Basic authentication middleware (credentials from environment only)."""

from __future__ import annotations

import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tautulli_inspector.settings import _settings_from_env

_UNAUTH = JSONResponse(
    status_code=401,
    content={"detail": "Not authenticated"},
    headers={"WWW-Authenticate": 'Basic realm="Tautulli Inspector"'},
)


def _basic_credentials_ok(header_value: str, expected_user: str, expected_password: str) -> bool:
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return False
    try:
        raw = base64.b64decode(parts[1].strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if ":" not in decoded:
        return False
    username, password = decoded.split(":", 1)
    try:
        user_ok = secrets.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
        pass_ok = secrets.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
    except Exception:
        return False
    return bool(user_ok and pass_ok)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require valid Basic credentials when ``basic_auth_enabled`` is true (see Settings)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = _settings_from_env()
        if not settings.basic_auth_enabled:
            return await call_next(request)

        path = request.scope.get("path") or ""
        if path == "/healthz":
            return await call_next(request)

        auth = request.headers.get("Authorization")
        if not auth or not _basic_credentials_ok(auth, settings.basic_auth_username, settings.basic_auth_password):
            return _UNAUTH

        return await call_next(request)
