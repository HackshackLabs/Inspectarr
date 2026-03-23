"""Plex.tv PIN sign-in: obtain tokens for primary/secondary profiles (stored in dashboard JSON)."""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tautulli_inspector.dashboard_config import ensure_plex_client_identifier, load_raw_config, save_raw_config
from tautulli_inspector.limiter import limiter
from tautulli_inspector.plex_client import plex_auth_app_url, plex_check_pin, plex_create_pin
from tautulli_inspector.settings import _settings_from_env, get_settings, plex_token_for_profile

logger = logging.getLogger(__name__)

router = APIRouter()


class PlexAuthStartBody(BaseModel):
    profile: Literal["primary", "secondary"] = Field(default="primary")


class PlexAuthCheckBody(BaseModel):
    pin_id: int = Field(..., ge=1)
    profile: Literal["primary", "secondary"] = Field(default="primary")


@router.post("/settings/plex-auth/start", tags=["configuration"])
@limiter.limit("30/minute")
async def plex_auth_start(request: Request, body: PlexAuthStartBody) -> dict:
    """
    Create a Plex PIN. Open `auth_url` in a browser (same account as your servers), then POST to
    `/settings/plex-auth/check` with the same pin_id.
    """
    base = _settings_from_env()
    try:
        client_id = ensure_plex_client_identifier(base)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        pin = await plex_create_pin(client_identifier=client_id, timeout_seconds=30.0)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Plex pin create HTTP error: %s %s",
            exc.response.status_code,
            (exc.response.text or "")[:500],
        )
        raise HTTPException(status_code=502, detail="Could not create Plex sign-in PIN (upstream error).") from exc
    except httpx.RequestError as exc:
        logger.warning("Plex pin create request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Plex.tv to create a sign-in PIN.") from exc
    pin_id = pin.get("id")
    code = pin.get("code")
    if pin_id is None or code is None:
        raise HTTPException(status_code=502, detail="Plex pin response missing id or code")
    try:
        pin_id_int = int(pin_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Plex pin id is not an integer") from exc
    auth_url = plex_auth_app_url(client_identifier=client_id, pin_code=str(code))
    return {
        "profile": body.profile,
        "pin_id": pin_id_int,
        "code": str(code),
        "client_identifier": client_id,
        "auth_url": auth_url,
        "message": "Open auth_url in a browser, sign in, then POST /settings/plex-auth/check with the same pin_id.",
    }


@router.post("/settings/plex-auth/check", tags=["configuration"])
@limiter.limit("60/minute")
async def plex_auth_check(request: Request, body: PlexAuthCheckBody) -> dict:
    """Poll after sign-in. On success, saves token into dashboard overrides and returns ok."""
    base = _settings_from_env()
    client_id = ensure_plex_client_identifier(base)
    try:
        data = await plex_check_pin(pin_id=body.pin_id, client_identifier=client_id, timeout_seconds=30.0)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Plex pin check HTTP error: %s %s",
            exc.response.status_code,
            (exc.response.text or "")[:500],
        )
        raise HTTPException(status_code=502, detail="Could not verify Plex sign-in (upstream error).") from exc
    except httpx.RequestError as exc:
        logger.warning("Plex pin check request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Plex.tv to verify sign-in.") from exc

    token = str(data.get("authToken") or data.get("auth_token") or "").strip()
    if not token:
        return {"status": "pending", "profile": body.profile, "message": "Not linked yet; finish sign-in in the browser."}

    key = "plex_token_primary" if body.profile == "primary" else "plex_token_secondary"
    raw = load_raw_config(base)
    ov = raw.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
    ov = {**ov, key: token}
    out = {**raw, "overrides": ov}
    try:
        save_raw_config(base, out)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "complete", "profile": body.profile, "message": "Token saved to dashboard configuration."}


@router.get("/settings/plex-auth/validate", tags=["configuration"])
@limiter.limit("120/minute")
async def plex_auth_validate(
    request: Request,
    profile: Literal["primary", "secondary"] = Query(..., description="Which token to check"),
) -> dict:
    """
    Call Plex.tv with the effective token to confirm it is accepted (does not hit your PMS).
    """
    settings = get_settings()
    token = plex_token_for_profile(settings, profile)
    if not token:
        return {
            "ok": False,
            "profile": profile,
            "message": "No token for this profile. Sign in above, or paste a token and click Save configuration.",
        }
    cid = str(settings.plex_client_identifier or "").strip() or "insecpectarr"
    headers = {
        "X-Plex-Token": token,
        "Accept": "application/json",
        "X-Plex-Client-Identifier": cid,
        "X-Plex-Product": "Insecpectarr",
        "X-Plex-Version": "0.1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get("https://plex.tv/api/v2/user", headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Plex.tv validate request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Plex.tv to validate the token.") from exc

    if response.status_code == 401:
        return {
            "ok": False,
            "profile": profile,
            "message": "Plex.tv rejected this token (401). Sign in again or replace the token.",
        }
    if response.status_code >= 400:
        return {
            "ok": False,
            "profile": profile,
            "message": f"Plex.tv returned HTTP {response.status_code}.",
        }

    username = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            user = data.get("user")
            if isinstance(user, dict):
                username = str(user.get("username") or user.get("title") or "").strip()
            if not username:
                username = str(data.get("username") or data.get("title") or "").strip()
    except Exception:
        pass

    msg = "Token is valid at Plex.tv."
    if username:
        msg = f"Token is valid (Plex user: {username})."
    return {
        "ok": True,
        "profile": profile,
        "message": msg,
    }
