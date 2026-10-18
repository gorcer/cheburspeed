"""Authentication and authorization primitives.

This module hosts the small set of helpers shared by the HTTP API
(slowapi rate limiting, current user resolution, …) and the future
``/admin`` JWT-protected routes (DAT-325.8).

The :class:`JWTService` is implemented end-to-end so that the upcoming
admin task can pick it up without churn, but it is **not** wired into
any route in this milestone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext

from xspeed_server.config import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash for ``plain``."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return ``True`` if ``plain`` matches the bcrypt ``hashed`` value."""
    return _pwd_context.verify(plain, hashed)


class TokenError(Exception):
    """Raised when a JWT cannot be decoded or fails validation."""


@dataclass(slots=True)
class JWTService:
    """Encode and decode short-lived JWT tokens for the admin panel.

    Replaced :mod:`jose` with :mod:`PyJWT` (DAT-459, item V) because
    python-jose has not seen a release since 2022 and has open CVE
    backlog; PyJWT has an active maintainer, fewer transitive deps and
    a cleaner ``InvalidTokenError`` hierarchy.
    """

    secret: str
    algorithm: str
    expires_minutes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> JWTService:
        return cls(
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expires_minutes=settings.jwt_expires_minutes,
        )

    def create_access_token(self, subject: str, extra: dict[str, Any] | None = None) -> str:
        """Return a signed JWT for ``subject`` (admin id/email)."""
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": subject,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=self.expires_minutes)).timestamp()),
        }
        if extra:
            payload.update(extra)
        return cast(
            str,
            jwt.encode(payload, self.secret, algorithm=self.algorithm),
        )

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode ``token`` and return its payload or raise :class:`TokenError`."""
        try:
            return cast(
                dict[str, Any],
                jwt.decode(token, self.secret, algorithms=[self.algorithm]),
            )
        except InvalidTokenError as exc:
            raise TokenError(str(exc)) from exc


__all__ = [
    "JWTService",
    "TokenError",
    "hash_password",
    "verify_password",
]
