"""Test helpers for the Agent Interface."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from siamang.agents import Agent, AgentCapability, AgentDefinition, AgentRequest, AgentResult
from siamang.agents import implementations as impl_mod
from siamang.agents import registry as registry_mod
from siamang.contracts.schema import F
from siamang.projects import ProjectRegistry
from siamang.tasks import TaskEngine, TaskRequest
from tests.support.projects import Clock


@dataclass
class World:
    projects: ProjectRegistry
    engine: TaskEngine
    project: object
    task: object


def make_world(db, *, start: bool = True, root_path: str = "/work/toko") -> World:
    projects = ProjectRegistry(db)
    project = projects.register(name="Toko", root_path=root_path)
    engine = TaskEngine(db, projects, clock=Clock())
    task = engine.create(TaskRequest(project_id=project.project_id, description="Tambahkan fitur inventory",
                                     source="test")).task
    if start:
        task = engine.start(task.task_id)
    return World(projects, engine, project, task)


def definition(**over) -> AgentDefinition:
    data = dict(
        agent_id="agent.probe",
        name="Probe",
        description="Test-only agent",
        version="1",
        role="probe",
        implementation="test.probe",
        capabilities=frozenset({AgentCapability.STRUCTURED_ANALYSIS}),
        input_schema={"message": F("text", required=True)},
        output_schema={"message": F("text", required=True)},
    )
    data.update(over)
    return AgentDefinition(**data)


def request(world: World, **over) -> AgentRequest:
    data = dict(task_id=world.task.task_id, project_id=world.project.project_id, instruction="Ulangi pesan",
                input={"message": "halo"})
    data.update(over)
    return AgentRequest(**data)


class ProbeAgent(Agent):
    """Test-only implementation. ``behavior(agent, request, context)`` decides what ``run`` does."""

    __slots__ = ()
    behavior = None

    def run(self, request, context):
        if type(self).behavior is not None:
            return type(self).behavior(self, request, context)
        return AgentResult.completed(self.agent_id, context.task_id, {"message": request.input["message"]})


def allow_probe(monkeypatch, behavior=None, cls=ProbeAgent, impl_id="test.probe"):
    """Add a test implementation to the allowlist for one test (the real table stays fixed)."""
    table = MappingProxyType({**impl_mod.KNOWN_IMPLEMENTATIONS, impl_id: cls})
    monkeypatch.setattr(impl_mod, "KNOWN_IMPLEMENTATIONS", table)
    monkeypatch.setattr(registry_mod, "KNOWN_IMPLEMENTATIONS", table)
    monkeypatch.setattr(ProbeAgent, "behavior", staticmethod(behavior) if behavior else None)
