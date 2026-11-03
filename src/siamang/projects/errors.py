"""Typed Project Registry errors.

Messages never include ``root_path`` values or metadata contents, so an
error that ends up in a log does not leak local paths or project data.
"""

from __future__ import annotations


class ProjectError(Exception):
    """Base class for project registry errors."""


class InvalidProjectError(ProjectError, ValueError):
    """A field value is invalid. ``field`` names it."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"{field}: {message}")


class ProjectNotFoundError(ProjectError, LookupError):
    pass


class DuplicateProjectError(ProjectError):
    """Would collide with an existing project on ``field`` (project_id, name, or root_path)."""

    def __init__(self, field: str, existing_project_id: str):
        self.field = field
        self.existing_project_id = existing_project_id
        super().__init__(f"{field} is already used by project {existing_project_id}")


class ProjectArchivedError(ProjectError):
    """The operation needs an active project; restore it first."""
