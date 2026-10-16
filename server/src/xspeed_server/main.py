"""XSpeed FastAPI application entry point."""

from __future__ import annotations

import logging

import uvicorn

logger = logging.getLogger(__name__)


def run() -> None:
    """Run the XSpeed server with uvicorn."""
    uvicorn.run(
        "xspeed_server.app:create_app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        factory=True,
        log_level=logging.INFO,
    )


if __name__ == "__main__":
    run()
