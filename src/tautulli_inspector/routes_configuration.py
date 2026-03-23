"""Browser configuration UI for dashboard JSON and presentation options."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from tautulli_inspector.csrf import verify_csrf_double_submit
from tautulli_inspector.dashboard_config import (
    SETTINGS_EDITOR_FIELDS,
    THEME_CHOICES,
    PresentationConfig,
    build_template_globals,
    config_file_path,
    load_overrides_dict,
    load_presentation,
    load_raw_config,
    save_raw_config,
    upload_dir,
)
from tautulli_inspector.limiter import limiter
from tautulli_inspector.settings import (
    PlexServer,
    TautulliServer,
    _settings_from_env,
    get_settings,
    plex_mapped_tautulli_server_ids,
)
from tautulli_inspector.url_safety import validate_upstream_base_url

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _coerce_field(raw: str, typ: str) -> Any:
    s = (raw or "").strip()
    if typ == "text":
        return s
    if typ == "int":
        return int(s) if s else 0
    if typ == "float":
        return float(s) if s else 0.0
    raise ValueError(typ)


def _effective_to_form_dict(settings: Settings) -> dict[str, Any]:
    return settings.model_dump()


def _q(msg: str) -> str:
    return quote(msg[:500], safe="")


def _plex_token_row_ui(
    env_key: str,
    effective: Settings,
    env_base: Settings,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Human-readable Plex token state for the settings page (never expose full token)."""
    val = str(getattr(effective, env_key) or "").strip()
    in_json = bool(str(overrides.get(env_key) or "").strip())
    env_only = bool(str(getattr(env_base, env_key) or "").strip())
    preview = ""
    if val:
        preview = ("…" + val[-4:]) if len(val) >= 4 else "(short token)"
    if not val:
        return {
            "has_value": False,
            "headline": "Not configured",
            "detail": "Sign in saves the token to this JSON file. Or paste below and click Save configuration.",
            "preview": "",
            "css_class": "plex-token-missing",
        }
    if in_json:
        detail = "Stored in dashboard JSON (Sign in writes here automatically)."
    elif env_only:
        detail = "Loaded from environment (.env). It does not appear in the JSON textarea until you Save a paste."
    else:
        detail = "Token is present."
    return {
        "has_value": True,
        "headline": "Configured",
        "detail": detail,
        "preview": preview,
        "css_class": "plex-token-ok",
    }


@router.get("/settings", response_class=HTMLResponse, tags=["configuration"])
async def settings_page(
    request: Request,
    saved: str | None = Query(default=None),
    error: str | None = Query(default=None),
    plex_saved: str | None = Query(default=None),
) -> HTMLResponse:
    env_base = _settings_from_env()
    effective = get_settings()
    pres = load_presentation(env_base)
    form_values = _effective_to_form_dict(effective)
    servers_json = json.dumps(
        [s.model_dump() for s in effective.tautulli_servers],
        indent=2,
        ensure_ascii=False,
    )
    plex_servers_json = json.dumps(
        [s.model_dump() for s in effective.plex_servers],
        indent=2,
        ensure_ascii=False,
    )
    ov = load_overrides_dict(env_base)
    plex_saved_ok = plex_saved in ("primary", "secondary")
    ctx = build_template_globals(
        "Inspector's Clipboard", csrf_token=getattr(request.state, "csrf_token", "") or ""
    )
    ctx.update(
        {
            "request": request,
            "nav_current": "settings",
            "theme_choices": THEME_CHOICES,
            "presentation": pres,
            "form_values": form_values,
            "tautulli_servers_json": servers_json,
            "plex_servers_json": plex_servers_json,
            "editor_fields": SETTINGS_EDITOR_FIELDS,
            "saved_banner": saved == "1",
            "error_message": error or "",
            "config_path_display": str(config_file_path(env_base)),
            "plex_token_primary_ui": _plex_token_row_ui("plex_token_primary", effective, env_base, ov),
            "plex_token_secondary_ui": _plex_token_row_ui("plex_token_secondary", effective, env_base, ov),
            "plex_servers_count": len(effective.plex_servers),
            "plex_client_identifier_configured": bool(str(effective.plex_client_identifier or "").strip()),
            "plex_chaining_server_count": len(plex_mapped_tautulli_server_ids(effective)),
            "plex_saved_banner": plex_saved_ok,
            "plex_saved_profile": plex_saved if plex_saved_ok else "",
        }
    )
    return templates.TemplateResponse(request, name="settings.html", context=ctx)


@router.post("/settings", tags=["configuration"])
@limiter.limit("30/minute")
async def settings_save(request: Request) -> RedirectResponse:
    env_base = _settings_from_env()
    prev = load_raw_config(env_base)
    prev_ov = prev.get("overrides") if isinstance(prev.get("overrides"), dict) else {}

    form = await request.form()
    scalar: dict[str, str] = {}
    csrf_form: str | None = None
    for key, val in form.multi_items():
        if key == "csrf_token" and isinstance(val, str):
            csrf_form = val
            continue
        if hasattr(val, "read"):
            continue
        if isinstance(val, str):
            scalar[key] = val
    verify_csrf_double_submit(request, csrf_form)
    block_private = env_base.block_private_upstream_urls

    theme = scalar.get("theme") or "slate"
    if theme not in {t[0] for t in THEME_CHOICES}:
        theme = "slate"

    prev_pres = load_presentation(env_base)
    logo_name: str | None = prev_pres.logo_file
    remove = scalar.get("remove_logo") in ("1", "on", "true", "yes")
    if remove:
        if logo_name:
            old = upload_dir(env_base) / logo_name
            if old.is_file():
                try:
                    old.unlink()
                except OSError:
                    pass
        logo_name = None

    logo = form.get("logo")
    if logo is not None and hasattr(logo, "filename") and getattr(logo, "filename", None):
        ext = Path(logo.filename).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            return RedirectResponse(
                url=f"/settings?error={_q('Logo must be png, jpg, gif, or webp.')}",
                status_code=303,
            )
        ud = upload_dir(env_base)
        ud.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}{ext}"
        dest = ud / fname
        try:
            content = await logo.read()
            if len(content) > 2_000_000:
                return RedirectResponse(
                    url=f"/settings?error={_q('Logo file too large (max 2 MB).')}",
                    status_code=303,
                )
            dest.write_bytes(content)
            logo_name = fname
        except OSError as exc:
            return RedirectResponse(url=f"/settings?error={_q(str(exc))}", status_code=303)

    presentation = {
        "theme": theme,
        "site_title": (scalar.get("site_title") or "Insecpectarr").strip()[:200],
        "footer_text": (scalar.get("footer_text") or "")[:500],
        "custom_nav_note": (scalar.get("custom_nav_note") or "")[:300],
        "logo_file": logo_name,
    }

    new_ov: dict[str, Any] = dict(prev_ov)
    for name, _label, typ in SETTINGS_EDITOR_FIELDS:
        if name not in scalar:
            continue
        raw = scalar[name]
        try:
            new_ov[name] = _coerce_field(raw, typ)
        except ValueError:
            return RedirectResponse(
                url=f"/settings?error={_q(f'Invalid value for {name}')}",
                status_code=303,
            )

    raw_servers = scalar.get("tautulli_servers_json") or "[]"
    try:
        parsed = json.loads(raw_servers)
        if not isinstance(parsed, list):
            raise ValueError("not a list")
        servers = [TautulliServer.model_validate(row) for row in parsed]
        if block_private:
            for s in servers:
                try:
                    validate_upstream_base_url(s.base_url, block_private_hosts=True)
                except ValueError as exc:
                    return RedirectResponse(
                        url=f"/settings?error={_q(f'Tautulli server URL: {exc}')}",
                        status_code=303,
                    )
        new_ov["tautulli_servers"] = [s.model_dump() for s in servers]
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        return RedirectResponse(
            url=f"/settings?error={_q(f'Tautulli servers JSON: {exc}')}",
            status_code=303,
        )

    for sonarr_field, sonarr_typ in (
        ("sonarr_base_url", "text"),
        ("sonarr_request_timeout_seconds", "float"),
    ):
        if sonarr_field in scalar:
            try:
                new_ov[sonarr_field] = _coerce_field(scalar[sonarr_field], sonarr_typ)
            except ValueError:
                return RedirectResponse(
                    url=f"/settings?error={_q(f'Invalid value for {sonarr_field}')}",
                    status_code=303,
                )

    if "plex_request_timeout_seconds" in scalar:
        try:
            new_ov["plex_request_timeout_seconds"] = _coerce_field(
                scalar["plex_request_timeout_seconds"], "float"
            )
        except ValueError:
            return RedirectResponse(
                url=f"/settings?error={_q('Invalid value for plex_request_timeout_seconds')}",
                status_code=303,
            )

    raw_plex = scalar.get("plex_servers_json")
    if raw_plex is not None:
        raw_plex = raw_plex.strip() if isinstance(raw_plex, str) else ""
        try:
            if not raw_plex:
                new_ov["plex_servers"] = []
            else:
                plex_parsed = json.loads(raw_plex)
                if not isinstance(plex_parsed, list):
                    raise ValueError("not a list")
                plex_servers = [PlexServer.model_validate(row) for row in plex_parsed]
                if block_private:
                    for ps in plex_servers:
                        try:
                            validate_upstream_base_url(ps.base_url, block_private_hosts=True)
                        except ValueError as exc:
                            return RedirectResponse(
                                url=f"/settings?error={_q(f'Plex server URL: {exc}')}",
                                status_code=303,
                            )
                new_ov["plex_servers"] = [ps.model_dump() for ps in plex_servers]
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            return RedirectResponse(
                url=f"/settings?error={_q(f'Plex servers JSON: {exc}')}",
                status_code=303,
            )

    sk = (scalar.get("sonarr_api_key") or "").strip()
    if scalar.get("clear_sonarr_key") in ("1", "on", "true", "yes"):
        new_ov.pop("sonarr_api_key", None)
    elif sk:
        new_ov["sonarr_api_key"] = sk
    elif not new_ov.get("sonarr_api_key") and prev_ov.get("sonarr_api_key"):
        new_ov["sonarr_api_key"] = prev_ov["sonarr_api_key"]

    pk1 = (scalar.get("plex_token_primary") or "").strip()
    if scalar.get("clear_plex_token_primary") in ("1", "on", "true", "yes"):
        new_ov.pop("plex_token_primary", None)
    elif pk1:
        new_ov["plex_token_primary"] = pk1
    elif not new_ov.get("plex_token_primary") and prev_ov.get("plex_token_primary"):
        new_ov["plex_token_primary"] = prev_ov["plex_token_primary"]

    pk2 = (scalar.get("plex_token_secondary") or "").strip()
    if scalar.get("clear_plex_token_secondary") in ("1", "on", "true", "yes"):
        new_ov.pop("plex_token_secondary", None)
    elif pk2:
        new_ov["plex_token_secondary"] = pk2
    elif not new_ov.get("plex_token_secondary") and prev_ov.get("plex_token_secondary"):
        new_ov["plex_token_secondary"] = prev_ov["plex_token_secondary"]

    effective_sonarr = str(new_ov.get("sonarr_base_url") or "").strip()
    if not effective_sonarr:
        effective_sonarr = str(env_base.sonarr_base_url or "").strip()
    if block_private and effective_sonarr:
        try:
            validate_upstream_base_url(effective_sonarr, block_private_hosts=True)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/settings?error={_q(f'Sonarr URL: {exc}')}",
                status_code=303,
            )

    out = {
        "presentation": presentation,
        "overrides": new_ov,
    }
    try:
        PresentationConfig.model_validate(presentation)
    except ValidationError as exc:
        return RedirectResponse(url=f"/settings?error={_q(str(exc))}", status_code=303)

    try:
        save_raw_config(env_base, out)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RedirectResponse(url="/settings?saved=1", status_code=303)
