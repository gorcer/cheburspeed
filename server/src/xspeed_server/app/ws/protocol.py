"""WebSocket protocol models.

The probe-to-server WebSocket channel is a small JSON envelope
protocol. Messages are validated against the Pydantic models defined
here so the dispatcher and handlers operate on typed objects rather
than raw dictionaries.

The current protocol version is ``1``. Probes that send a
``protocol_version`` below ``1`` are rejected with close code
``4002`` ("upgrade required").
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION: int = 1

MIN_PROTOCOL_VERSION: int = 1

WS_CLOSE_INVALID_TOKEN: int = 4001

WS_CLOSE_UPGRADE_REQUIRED: int = 4002


class TaskAssignedMessage(BaseModel):
    """Envelope sent from the server to a probe to hand off a new task."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["task.assigned"] = "task.assigned"
    task_id: int = Field(..., ge=1)
    link: str = Field(..., min_length=1, max_length=2048)
    destination_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description=(
            "Override for the probe's default speedtest URL. When set, the "
            "probe downloads from this address instead of ``settings.speedtest_url``."
        ),
    )
    protocol_version: int = Field(default=PROTOCOL_VERSION, ge=1)


class OnlineMessage(BaseModel):
    """First message a probe sends after connecting."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["online"] = "online"
    alias: str = Field(default="", max_length=255)
    region_code: str = Field(default="", max_length=64)
    version: str = Field(default="", max_length=64)
    netif: str = Field(default="", max_length=128)
    protocol_version: int = Field(default=PROTOCOL_VERSION, ge=1)


class ResultMessage(BaseModel):
    """Speedtest outcome reported by a probe for a specific task."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["result"] = "result"
    task_id: int = Field(..., ge=1)
    speed_mbps: float | None = Field(default=None)
    error: str | None = Field(default=None, max_length=4096)


ProbeMessage = Annotated[OnlineMessage | ResultMessage, Field(discriminator="type")]


class UnknownMessage(BaseModel):
    """Catch-all for messages we don't yet understand.

    Used by :func:`parse_probe_message` so the handler can decide how
    to react (currently: log + ignore) without us raising on every
    forward-compatible probe that adds new message types.
    """

    model_config = ConfigDict(extra="allow")

    type: str


def parse_probe_message(payload: dict[str, Any]) -> OnlineMessage | ResultMessage | UnknownMessage:
    """Validate a raw JSON payload into a typed protocol message.

    Returns an :class:`UnknownMessage` when ``type`` is missing or
    refers to a message family this version of the server does not
    implement; the dispatcher must not crash on such inputs.
    """
    msg_type = payload.get("type")
    if msg_type == "online":
        return OnlineMessage.model_validate(payload)
    if msg_type == "result":
        return ResultMessage.model_validate(payload)
    return UnknownMessage.model_validate(payload)


__all__ = [
    "MIN_PROTOCOL_VERSION",
    "OnlineMessage",
    "PROTOCOL_VERSION",
    "ResultMessage",
    "TaskAssignedMessage",
    "UnknownMessage",
    "WS_CLOSE_INVALID_TOKEN",
    "WS_CLOSE_UPGRADE_REQUIRED",
    "parse_probe_message",
]
