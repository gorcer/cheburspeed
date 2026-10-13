"""XSpeed probe entry point."""

from __future__ import annotations

import logging
import signal
from types import FrameType
from typing import Any

from xspeed_probe.config import get_settings

logger = logging.getLogger(__name__)


def run() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("xspeed-probe starting", extra={"alias": settings.alias})

    stop_event = _install_signal_handlers()
    try:
        _run_forever(stop_event)
    except KeyboardInterrupt:
        logger.info("interrupted, exiting")
    finally:
        logger.info("xspeed-probe stopped")


def _run_forever(stop_event: Any) -> None:  # placeholder loop, replaced in DAT-327
    while not stop_event.is_set():
        try:
            stop_event.wait(timeout=1.0)
        except Exception:
            logger.exception("probe loop iteration failed")
            break


def _install_signal_handlers() -> Any:
    import threading

    stop = threading.Event()

    def _handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("received signal %s, stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    return stop


if __name__ == "__main__":
    run()