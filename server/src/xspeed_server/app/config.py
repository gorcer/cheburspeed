"""Application settings re-exported for the ``xspeed_server.app`` subpackage.

The actual :class:`Settings` class lives in :mod:`xspeed_server.config` so that
``pydantic-settings`` discovery (env files, ``BaseSettings`` meta-config) stays
in a single canonical place.
"""

from xspeed_server.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
