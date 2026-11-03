"""Test helpers for the Task Engine."""

from __future__ import annotations

from siamang.projects import ProjectRegistry
from siamang.tasks import TaskEngine, TaskRequest
from tests.support.projects import Clock


def make_engine(db, *, clock=None, bus=None, project_name="Toko"):
    projects = ProjectRegistry(db)
    project = projects.register(name=project_name, root_path=f"/work/{project_name.lower()}")
    engine = TaskEngine(db, projects, bus=bus, clock=clock or Clock())
    return engine, project


def request(project, **over):
    data = dict(project_id=project.project_id, description="Tambahkan fitur inventory", source="test")
    data.update(over)
    return TaskRequest(**data)
