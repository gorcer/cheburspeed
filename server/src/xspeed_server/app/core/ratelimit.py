"""Rate-limiting primitives built on top of :mod:`slowapi`.

The :data:`limiter` instance is shared across the application. Routes opt
into a per-route rate limit via the ``@limiter.limit(...)`` decorator,
which inspects ``request.client.host`` by default (see
:func:`get_remote_address`).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

__all__ = ["limiter"]
