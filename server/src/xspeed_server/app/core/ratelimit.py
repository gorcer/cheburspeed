"""Rate-limiting primitives built on top of :mod:`slowapi`.

The :data:`limiter` instance is shared across the application. Routes opt
into a per-route rate limit via the ``@limiter.limit(...)`` decorator,
which inspects ``request.client.host`` by default (see
:func:`get_remote_address`).

Since DAT-637 the quotas are not hardcoded in the decorators anymore:
every route uses a *dynamic* limit provider (:func:`limit_provider`)
which resolves the quota from :class:`xspeed_server.config.Settings` on
each request. Changing a quota therefore only requires changing the
matching environment variable — no source change.
"""

from __future__ import annotations

from collections.abc import Callable

from slowapi import Limiter
from slowapi.util import get_remote_address

from xspeed_server.config import Settings, get_settings

limiter = Limiter(key_func=get_remote_address)

__all__ = ["limiter", "limit_provider"]


def limit_provider(quota: Callable[[Settings], int], window: str) -> Callable[[], str]:
    """Build a slowapi limit provider for ``quota`` requests per ``window``.

    ``quota`` receives the current application settings and returns the
    number of requests allowed inside the window. The returned callable is
    invoked by slowapi on every request, so the effective quota always
    follows the current settings instead of the value that happened to be
    configured when the module was imported.

    ``window`` is one of the slowapi windows (``second``, ``minute``,
    ``hour``, ``day``). It is part of the limit key, so changing the
    window resets the counters.
    """

    def provider() -> str:
        return f"{quota(get_settings())}/{window}"

    return provider
