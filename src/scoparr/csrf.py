"""CSRF double-submit validation (cookie + header or form field)."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def verify_csrf_double_submit(request: Request, submitted: str | None) -> None:
    """Require a token matching the CSRF cookie (timing-safe)."""
    expected = (request.cookies.get(CSRF_COOKIE_NAME) or "").strip()
    got = (submitted or "").strip()
    if not expected or not got:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
    try:
        ok = secrets.compare_digest(expected.encode("utf-8"), got.encode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        ) from exc
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
