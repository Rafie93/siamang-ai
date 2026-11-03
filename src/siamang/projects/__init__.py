"""Project Registry (Phase 07): identity, metadata, and lifecycle of projects.

Does not manage Git, tasks, models, tools, or agents.
"""

from siamang.projects.definition import Project, ProjectStatus, ProjectType
from siamang.projects.errors import (
    DuplicateProjectError,
    InvalidProjectError,
    ProjectArchivedError,
    ProjectError,
    ProjectNotFoundError,
)
from siamang.projects.registry import ProjectRegistry

__all__ = [
    "DuplicateProjectError",
    "InvalidProjectError",
    "Project",
    "ProjectArchivedError",
    "ProjectError",
    "ProjectNotFoundError",
    "ProjectRegistry",
    "ProjectStatus",
    "ProjectType",
]
