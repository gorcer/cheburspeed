"""``/api/v1/admin/login`` endpoint.

Exchanges ``{email, password}`` for a signed JWT that the rest of the
admin panel sends in the ``Authorization: Bearer <token>`` header. The
credential check is intentionally cheap (one asyncpg row lookup plus a
bcrypt verify) so the login can ride the same connection pool as the
data plane without a dedicated microservice.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xspeed_server.app.config import Settings, get_settings
from xspeed_server.app.core.ratelimit import limiter
from xspeed_server.app.core.security import JWTService, verify_password
from xspeed_server.app.deps import PoolDep

router = APIRouter(prefix="/admin", tags=["admin-auth"])

__all__ = ["router", "LoginRequest", "LoginResponse"]

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginRequest(BaseModel):
    """Payload accepted by ``POST /api/v1/admin/login``."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(
        ...,
        min_length=3,
        max_length=255,
        pattern=EMAIL_PATTERN,
        description="Admin email address.",
    )
    password: str = Field(..., min_length=1, max_length=512)

    @field_validator("email")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.strip().lower()


class LoginResponse(BaseModel):
    """Response returned by ``POST /api/v1/admin/login``."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type literal
    expires_in: int


def _build_jwt_service(request: Request) -> JWTService:
    """Build a :class:`JWTService` from the current application settings."""
    settings: Settings = get_settings()
    return JWTService.from_settings(settings)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Exchange admin credentials for a JWT.",
)
@limiter.limit("10/minute")
async def login(
    request: Request,  # required by slowapi to extract the client IP
    payload: LoginRequest,
    pool: PoolDep,
    jwt_service: Annotated[JWTService, Depends(_build_jwt_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """Verify the password and return a freshly signed JWT.

    The error path is deliberately a single ``401`` for both
    "unknown email" and "wrong password" so the endpoint does not
    leak which admin accounts exist.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, password_hash FROM admins WHERE email = $1",
            payload.email,
        )
    if row is None or not verify_password(payload.password, str(row["password_hash"])):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token = jwt_service.create_access_token(subject=str(row["email"]))
    return LoginResponse(
        access_token=token,
        token_type="bearer",  # noqa: S106 - OAuth token type literal
        expires_in=settings.jwt_expires_minutes * 60,
    )
