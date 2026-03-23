"""Shim for legacy ``uvicorn tautulli_inspector.main:app``. Prefer ``inspectarr.main``."""

from inspectarr.main import app, create_app, run

__all__ = ["app", "create_app", "run"]
