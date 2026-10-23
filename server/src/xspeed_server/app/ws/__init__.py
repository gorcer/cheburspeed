"""WebSocket package — populated by the DAT-325.4 task."""

from xspeed_server.app.ws.dispatcher import (
    PROBE_TIMEOUT_ERROR,
    TASK_TTL_SECONDS,
    ClaimedTask,
    dispatch_to_probe,
    expire_incomplete_tasks,
    start_sweeper,
    sweep_stale_running_tasks,
)
from xspeed_server.app.ws.handlers import (
    handle_online_message,
    handle_result_message,
    lookup_probe,
    register_websocket_route,
)
from xspeed_server.app.ws.hub import Hub
from xspeed_server.app.ws.protocol import (
    MIN_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    WS_CLOSE_INVALID_TOKEN,
    WS_CLOSE_UPGRADE_REQUIRED,
    OnlineMessage,
    ResultMessage,
    TaskAssignedMessage,
    parse_probe_message,
)

__all__ = [
    "ClaimedTask",
    "Hub",
    "MIN_PROTOCOL_VERSION",
    "OnlineMessage",
    "PROBE_TIMEOUT_ERROR",
    "PROTOCOL_VERSION",
    "ResultMessage",
    "TASK_TTL_SECONDS",
    "TaskAssignedMessage",
    "WS_CLOSE_INVALID_TOKEN",
    "WS_CLOSE_UPGRADE_REQUIRED",
    "dispatch_to_probe",
    "expire_incomplete_tasks",
    "handle_online_message",
    "handle_result_message",
    "lookup_probe",
    "parse_probe_message",
    "register_websocket_route",
    "start_sweeper",
    "sweep_stale_running_tasks",
]
