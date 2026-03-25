"""FastAPI entrypoint for Insecpectarr."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from inspectarr.auth_middleware import BasicAuthMiddleware
from inspectarr.dashboard_config import upload_dir
from inspectarr.limiter import limiter
from inspectarr.routes_configuration import router as configuration_router
from inspectarr.routes_dashboard import router as dashboard_router
from inspectarr.routes_stale_library import router as stale_library_router
from inspectarr.routes_plex_auth import router as plex_auth_router
from inspectarr.security_middleware import CsrfMiddleware, SecurityHeadersMiddleware
from inspectarr.settings import _settings_from_env, get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Insecpectarr", version="0.1.0")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    uploads_path = upload_dir(_settings_from_env())
    uploads_path.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")

    app.include_router(dashboard_router)
    app.include_router(stale_library_router)
    app.include_router(plex_auth_router)
    app.include_router(configuration_router)

    return app


app = create_app()


def run() -> None:
    """Run local development server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("inspectarr.main:app", host=settings.host, port=settings.port, reload=True)
