"""Admin HTTP API package (DAT-325.8).

Exposes the JWT-protected routes used by the SvelteKit admin panel:

* ``auth.py``        — ``POST /api/v1/admin/login``
* ``probes.py``      — CRUD over ``probes``
* ``destinations.py`` — CRUD over ``destinations``
* ``tasks.py``       — read-only filtered listing of ``tasks``
* ``stats.py``       — dashboard aggregate

The :func:`build_admin_router` helper aggregates every sub-router so
``app.main.create_app`` only needs a single ``include_router`` call.
"""

from __future__ import annotations

from fastapi import APIRouter

from xspeed_server.app.api.admin import auth, destinations, probes, stats, tasks

__all__ = ["auth", "build_admin_router", "destinations", "probes", "stats", "tasks"]


def build_admin_router() -> APIRouter:
    """Aggregate every admin sub-router under a single ``/api/v1`` parent.

    The login endpoint must stay public (no ``Depends(get_admin_user)``
    in :mod:`auth`); every other sub-router self-applies the
    dependency in its own ``APIRouter(dependencies=[...])`` line.
    """
    router = APIRouter()
    router.include_router(auth.router, prefix="/api/v1")
    router.include_router(destinations.router, prefix="/api/v1")
    router.include_router(probes.router, prefix="/api/v1")
    router.include_router(tasks.router, prefix="/api/v1")
    router.include_router(stats.router, prefix="/api/v1")
    return router
