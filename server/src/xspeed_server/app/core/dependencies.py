"""FastAPI dependencies shared by the admin v1 routers.

The application stores a :class:`Database` instance on
``app.state.db`` during the lifespan startup. The :func:`get_admin_user`
helper validates the ``Authorization: Bearer <jwt>`` header, looks up
the matching ``admins`` row and returns a small :class:`AdminUser`
record for the route handler. Any failure short-circuits with a
``401`` response so handlers can stay focused on the happy path.
"""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from xspeed_server.app.config import Settings, get_settings
from xspeed_server.app.core.security import JWTService, TokenError
from xspeed_server.app.deps import get_pool

__all__ = [
    "AdminUser",
    "SettingsDep",
    "get_admin_user",
    "get_jwt_service",
    "get_settings_dep",
]


class AdminUser(BaseModel):
    """Authenticated administrator record passed to admin routes.

    Mirrors the ``admins`` table (id, email). The password hash is
    intentionally omitted so handlers can never accidentally leak it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


def _bearer_token(authorization: str | None) -> str:
    """Extract the token portion of a ``Bearer <token>`` header.

    Returns an empty string when the header is missing or malformed so
    the caller raises the same 401 in every case.
    """
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def get_settings_dep() -> Settings:
    """FastAPI dependency wrapper around :func:`get_settings`."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_jwt_service(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> JWTService:
    """Build a :class:`JWTService` from the current application settings."""
    return JWTService.from_settings(settings)


async def get_admin_user(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    jwt_service: Annotated[JWTService, Depends(get_jwt_service)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AdminUser:
    """Validate the bearer token and return the matching admin record.

    The JWT ``sub`` claim holds the admin's email; the lookup is
    performed against the ``admins`` table. Any decoding, missing-claim
    or unknown-email failure is reported as ``401`` with a stable
    ``detail`` string so the frontend can render a single error path.
    """
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt_service.decode_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email FROM admins WHERE email = $1",
            str(subject),
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AdminUser.model_validate({"id": int(row["id"]), "email": str(row["email"])})
