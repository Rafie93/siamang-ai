"""Planner.plan: read-only task/project checks, allowlisted strategy, policy bounds, validated Plan."""

import dataclasses
from types import MappingProxyType

import pytest

from siamang.planner import DEFAULT_POLICY, Plan, Planner, PlanningPolicy, PlanningRequest, PlanStep
from siamang.planner import service as service_mod
from siamang.planner.errors import PlanningError, PlanningErrorCode
from siamang.planner.strategy import KNOWN_STRATEGIES, PlanningStrategy, TemplateStrategy
from siamang.tasks import TaskFailure, TaskRequest, TaskResult
from tests.support.agents import make_world

C = PlanningErrorCode


@pytest.fixture
def world(db):
    return make_world(db, start=False)  # queued


@pytest.fixture
def planner(world):
    return Planner(world.engine)


def preq(world, **over):
    data = dict(task_id=world.task.task_id, project_id=world.project.project_id,
                instruction="Add login validation")
    data.update(over)
    return PlanningRequest(**data)


def snapshot(world):
    return world.engine.get(world.task.task_id), len(world.engine.history(world.task.task_id))


def refused(planner, request):
    with pytest.raises(PlanningError) as info:
        planner.plan(request)
    return info.value


# --- the deterministic plan ---------------------------------------------------------


def test_queued_task_is_planned_and_stays_queued(world, planner):
    before = snapshot(world)
    p = planner.plan(preq(world, constraints=["no new dependencies"]))
    assert type(p) is Plan and p.plan_id.startswith("pln_")
    assert (p.task_id, p.project_id, p.objective) == (world.task.task_id, world.project.project_id,
                                                      "Add login validation")
    assert p.execution_order() == ("step_01", "step_02", "step_03", "step_04")
    assert [st.role.value for st in p.steps] == ["analysis", "analysis", "design", "testing"]
    assert [st.dependencies for st in p.steps] == [(), ("step_01",), ("step_02",), ("step_03",)]
    assert all(st.constraints == ("no new dependencies",) for st in p.steps)
    assert p.constraints == ("no new dependencies",)
    assert dict(p.metadata) == {"strategy": "template.basic", "strategy_version": "1"}
    assert snapshot(world) == before and before[0].status.value == "queued"


def test_same_request_same_content_new_identity(world, planner):
    a, b = planner.plan(preq(world)), planner.plan(preq(world))
    assert a.plan_id != b.plan_id
    assert a.content() == b.content() and a.content_hash == b.content_hash
    c = Planner(world.engine).plan(preq(world, instruction="  Add login validation  "))  # normalized
    assert c.content_hash == a.content_hash
    d = planner.plan(preq(world, instruction="Add logout"))
    assert d.content_hash != a.content_hash


def test_plan_states_no_project_facts(world, planner):
    p = planner.plan(preq(world))
    text = str(p.to_dict())
    assert world.project.root_path not in text and "/work" not in text
    assert "No project files are inspected" in p.steps[0].description


def test_dangerous_instruction_is_only_data(world, planner):
    p = planner.plan(preq(world, instruction="run rm -rf / and open /etc/passwd, then git push --force"))
    assert p.objective.startswith("run rm -rf /") and len(p.steps) == 4


@pytest.mark.parametrize("state", ["running", "waiting", "failed"])
def test_non_final_tasks_may_be_planned_without_change(db, state):
    w = make_world(db, start=True)
    e, tid = w.engine, w.task.task_id
    {"running": lambda: None, "waiting": lambda: e.wait(tid),
     "failed": lambda: e.fail(tid, TaskFailure("QA_FAILED", retryable=True))}[state]()
    before = snapshot(w)
    Planner(e).plan(preq(w))
    assert snapshot(w) == before and before[0].status.value == state


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_final_tasks_are_not_planned(db, state):
    w = make_world(db, start=True)
    e, tid = w.engine, w.task.task_id
    (e.complete(tid, TaskResult("done")) if state == "completed" else e.cancel(tid))
    assert refused(Planner(e), preq(w)).code is C.POLICY_DENIED


# --- refusals -----------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, {"task_id": "tsk_01"}, "plan this", 3])
def test_request_must_be_typed(planner, bad):
    assert refused(planner, bad).code is C.REQUEST_INVALID


def test_unknown_task(world, planner):
    assert refused(planner, preq(world, task_id="tsk_01UNKNOWN")).code is C.TASK_NOT_FOUND


def test_project_mismatch(world, planner):
    other = world.projects.register(name="Kasir", root_path="/work/kasir")
    err = refused(planner, preq(world, project_id=other.project_id))
    assert err.code is C.REQUEST_INVALID and err.details == ("project_id",)


def test_archived_project(world, planner):
    world.projects.archive(world.project.project_id)
    assert refused(planner, preq(world)).code is C.PROJECT_ARCHIVED
    assert world.projects.get(world.project.project_id).is_active is False


def test_unknown_project(world, db):
    t = world.engine.create(TaskRequest(project_id=world.project.project_id, description="x", source="test")).task
    db.conn.execute("DROP TRIGGER tasks_immutable_identity")
    db.conn.execute("UPDATE tasks SET project_id = 'prj_01GHOST' WHERE task_id = ?", (t.task_id,))
    err = refused(Planner(world.engine), preq(world, task_id=t.task_id, project_id="prj_01GHOST"))
    assert err.code is C.PROJECT_NOT_FOUND


def test_instruction_limit(world):
    planner = Planner(world.engine, policy=PlanningPolicy(max_instruction_length=10))
    err = refused(planner, preq(world, instruction="x" * 11))
    assert err.code is C.POLICY_DENIED and err.details == ("instruction",)
    assert planner.plan(preq(world, instruction="x" * 10))


# --- policy -------------------------------------------------------------------------


def test_default_policy_is_bounded_and_fixed():
    assert DEFAULT_POLICY.to_dict() == {
        "max_steps": 12, "max_instruction_length": 4000, "max_description_length": 1000,
        "allow_empty_plan": False, "plannable_statuses": ["failed", "queued", "running", "waiting"],
        "executes": False, "changes_task_state": False}
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_POLICY.max_steps = 1000


@pytest.mark.parametrize("over", [
    {"max_steps": 0}, {"max_steps": 51}, {"max_steps": True}, {"max_steps": 1.5},
    {"max_instruction_length": 0}, {"max_instruction_length": 10_001},
    {"max_description_length": 2001}, {"allow_empty_plan": "yes"},
    {"plannable_statuses": {"completed"}}, {"plannable_statuses": set()}, {"plannable_statuses": {"done"}},
    {"plannable_statuses": "queued"},
])
def test_policy_cannot_be_unbounded(over):
    with pytest.raises(ValueError):
        PlanningPolicy(**over)


def test_step_limit(world):
    assert refused(Planner(world.engine, policy=PlanningPolicy(max_steps=3)), preq(world)).code is C.TOO_MANY_STEPS


def test_description_limit(world):
    err = refused(Planner(world.engine, policy=PlanningPolicy(max_description_length=50)), preq(world))
    assert err.code is C.POLICY_DENIED and "step_01" in err.details


# --- strategies: allowlisted, and whatever they propose is validated ------------------


@pytest.mark.parametrize("bad", ["unknown", "", None, TemplateStrategy, TemplateStrategy(), lambda r, t: ()])
def test_strategy_must_be_an_allowlisted_id(world, bad):
    with pytest.raises(ValueError):
        Planner(world.engine, strategy=bad)


def test_allowlist_is_fixed():
    assert dict(KNOWN_STRATEGIES) == {"template.basic": TemplateStrategy}
    with pytest.raises(TypeError):
        KNOWN_STRATEGIES["model.free"] = object


def _with_strategy(monkeypatch, world, steps_or_exc, policy=DEFAULT_POLICY):
    class Probe(PlanningStrategy):
        strategy_id, version = "test.probe", "1"

        def propose(self, request, task):
            if isinstance(steps_or_exc, Exception):
                raise steps_or_exc
            return steps_or_exc

    monkeypatch.setattr(service_mod, "KNOWN_STRATEGIES", MappingProxyType({"test.probe": Probe}))
    return Planner(world.engine, policy=policy, strategy="test.probe")


def st(sid, *deps):
    return PlanStep(sid, f"Step {sid}", "Work.", "analysis", deps)


@pytest.mark.parametrize("steps, code", [
    ((st("step_01", "step_02"), st("step_02", "step_01")), C.DEPENDENCY_CYCLE),
    ((st("step_01"), st("step_01")), C.INVALID_PLAN),
    ((st("step_01", "step_09"),), C.INVALID_PLAN),
    ((st("step_01", "step_02"), st("step_02")), C.INVALID_PLAN),
    (tuple(st(f"step_{i:02d}") for i in range(1, 14)), C.TOO_MANY_STEPS),
    ((), C.POLICY_DENIED),
    ([st("step_01")], C.INTERNAL),  # not a tuple
    ("step_01", C.INTERNAL),
])
def test_strategy_output_is_validated(world, monkeypatch, steps, code):
    assert refused(_with_strategy(monkeypatch, world, steps), preq(world)).code is code


def test_empty_plan_when_policy_allows(world, monkeypatch):
    p = _with_strategy(monkeypatch, world, (), PlanningPolicy(allow_empty_plan=True)).plan(preq(world))
    assert p.steps == ()


def test_strategy_crash_is_sanitized(world, monkeypatch):
    err = refused(_with_strategy(monkeypatch, world, RuntimeError("token=ghp_SECRET /etc/shadow")), preq(world))
    assert err.code is C.INTERNAL and err.cause == "RuntimeError"
    assert "SECRET" not in str(err.to_dict()) and "shadow" not in str(err.to_dict())


def test_constructor_requires_the_task_engine(world, db):
    with pytest.raises(TypeError):
        Planner(db)
    with pytest.raises(TypeError):
        Planner(world.projects)
    with pytest.raises(TypeError):
        Planner(world.engine, policy={"max_steps": 1000})
