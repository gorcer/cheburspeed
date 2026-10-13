"""SQLAlchemy ORM models for XSpeed server."""

from xspeed_server.models.admin import Admin
from xspeed_server.models.base import Base
from xspeed_server.models.probe import Probe
from xspeed_server.models.region import Region
from xspeed_server.models.result import TaskResult, TaskResultArchive
from xspeed_server.models.task import Task

__all__ = [
    "Admin",
    "Base",
    "Probe",
    "Region",
    "Task",
    "TaskResult",
    "TaskResultArchive",
]