"""Test helpers for the Programmer: a registered sample project, a real Plan and SystemAnalysis, a fake model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.models import Capability, ModelRegistry, ModelRouter
from siamang.planner import Planner, PlanningRequest
from siamang.programmer import Programmer, ProgrammingRequest
from tests.support.agents import World, make_world
from tests.support.analyst import sample_project
from tests.support.fake_models import FakeProvider, fake_model

INSTRUCTION = "Add login validation to auth"
STEP = "step_03"  # "Define implementation approach" in the template plan
AUTH = "backend/routes/auth.py"
AUTH_TEXT = "def login():\n    pass\n"


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def change(path: str = AUTH, operation: str = "modify", content: str | None = "def login(user):\n    return bool(user)\n",
           step_id: str = STEP, reason: str = "Add login validation required by the plan.", **extra) -> dict:
    out: dict[str, Any] = {"path": path, "operation": operation, "step_id": step_id, "reason": reason}
    if content is not None:
        out["content"] = content
    out.update(extra)
    return out


def reply(changes: list | None = None, *, status: str = "completed", summary: str = "Validate login input.",
          warnings: list | None = None, unknowns: list | None = None, **extra) -> str:
    data = {"status": status, "summary": summary, "changes": [change()] if changes is None else changes,
            "warnings": warnings or [], "unknowns": unknowns or []}
    data.update(extra)
    return json.dumps(data)


@dataclass
class Model:
    """A scripted, offline model behind the real ModelRegistry/ModelRouter."""

    router: ModelRouter
    provider: FakeProvider
    requests: list = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.provider.calls)


def make_model(answer: str | Any = None, *, structured: bool = False, fail_with: Exception | None = None) -> Model:
    caps = {Capability.TEXT_GENERATION} | ({Capability.STRUCTURED_OUTPUT} if structured else set())
    holder: dict[str, Model] = {}

    def respond(model, request):
        holder["m"].requests.append(request)
        if callable(answer):
            return answer(model, request)
        return reply() if answer is None else answer

    provider = FakeProvider(models=[fake_model(caps=caps)], reply=respond, fail_with=fail_with)
    registry = ModelRegistry()
    registry.register_provider(provider)
    holder["m"] = Model(ModelRouter(registry), provider)
    return holder["m"]


@dataclass
class Setup:
    world: World
    root: Path
    plan: Any
    analysis: Any

    def request(self, **over) -> ProgrammingRequest:
        data = dict(task_id=self.world.task.task_id, project_id=self.world.project.project_id,
                    instruction=INSTRUCTION, plan=self.plan, analysis=self.analysis)
        data.update(over)
        return ProgrammingRequest(**data)

    def programmer(self, model: Model | None = None, **kw) -> Programmer:
        return Programmer(self.world.engine, models=None if model is None else model.router, **kw)


def make_setup(db, tmp_path: Path, *, start: bool = False, root: Path | None = None) -> Setup:
    root = root or sample_project(tmp_path / "banuva")
    world = make_world(db, start=start, root_path=str(root))
    ids = dict(task_id=world.task.task_id, project_id=world.project.project_id, instruction=INSTRUCTION)
    plan = Planner(world.engine).plan(PlanningRequest(**ids))
    analysis = SystemAnalyst(world.engine).analyze(AnalysisRequest(**ids, plan=plan))
    return Setup(world, root, plan, analysis)


def tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with its content hash: proves nothing was written."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}
