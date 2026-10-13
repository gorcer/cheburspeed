"""HTTP API package."""

from fastapi import APIRouter

from xspeed_server.app.api.v1 import probes, regions, tasks

__all__ = ["build_admin_router", "probes", "regions", "tasks"]


def build_admin_router() -> APIRouter:
    """Re-export so existing imports keep working."""
    from xspeed_server.app.api.admin import build_admin_router as _build

    return _build()
