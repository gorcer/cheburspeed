"""XSpeed FastAPI application package.

Re-exports :func:`create_app` so that ``uvicorn xspeed_server.app:create_app``
keeps working after the factory moved into :mod:`xspeed_server.app.main`.
"""

from xspeed_server.app.main import create_app

__all__ = ["create_app"]
