"""Plan: identity, immutability, and dependency-graph validation (unique, known, acyclic, ordered)."""

import dataclasses

import pytest

from siamang.planner import Plan, PlanStep
from siamang.planner.errors import DependencyCycleError, InvalidPlanError, PlanningErrorCode, TooManyStepsError
from siamang.planner.plan import MAX_STEPS_LIMIT
from siamang.planner.strategy import template_steps

TSK, PRJ = "tsk_01ABC", "prj_01ABC"


def s(sid, *deps, role="analysis"):
    return PlanStep(sid, f"Step {sid}", "Describe the work.", role, deps)


def plan(steps=(), **over):
    data = dict(plan_id="pln_01ABC", task_id=TSK, project_id=PRJ, objective="Add login validation", steps=steps)
    data.update(over)
    return Plan(**data)


def test_valid_plan():
    p = plan([s("step_01"), s("step_02", "step_01"), s("step_03", "step_01"), s("step_04", "step_02", "step_03")])
    assert p.execution_order() == ("step_01", "step_02", "step_03", "step_04")
    assert p.step("step_04").dependencies == ("step_02", "step_03")
    d = p.to_dict()
    assert d["plan_id"] == "pln_01ABC" and d["content_hash"].startswith("sha256:") and len(d["steps"]) == 4


def test_plan_and_steps_are_immutable():
    p = plan([s("step_01")], metadata={"strategy": "x"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.steps = ()
    with pytest.raises(TypeError):
        p.steps[0] = s("step_09")  # tuple
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.steps[0].title = "changed"
    with pytest.raises(TypeError):
        p.metadata["strategy"] = "y"
    steps = [s("step_01")]
    p = plan(steps)
    steps.append(s("step_02", "step_01"))  # the caller's list is not the plan's
    assert len(p.steps) == 1


def test_content_excludes_identity():
    a = plan(template_steps(), plan_id="pln_A")
    b = plan(template_steps(), plan_id="pln_B")
    assert a != b and a.content() == b.content() and a.content_hash == b.content_hash
    assert "plan_id" not in a.content()
    c = plan(template_steps(("only stdlib",)), plan_id="pln_A")
    assert c.content_hash != a.content_hash


@pytest.mark.parametrize("over", [
    {"plan_id": "pln_"}, {"plan_id": "orc_01ABC"}, {"plan_id": "pln_01\n"}, {"plan_id": None},
    {"task_id": "tsk_x\n"}, {"task_id": PRJ}, {"project_id": "/work"}, {"project_id": TSK},
    {"objective": ""}, {"objective": None},
    {"steps": "step_01"}, {"steps": [{"step_id": "step_01"}]}, {"steps": None},
    {"constraints": "x"}, {"metadata": {"secret": "x"}}, {"metadata": {"workflow": "x"}},
])
def test_invalid_identity_and_fields(over):
    with pytest.raises(InvalidPlanError):
        plan(**{"steps": [s("step_01")], **over})


def test_duplicate_step_ids():
    with pytest.raises(InvalidPlanError) as info:
        plan([s("step_01"), s("step_01")])
    assert info.value.details == ("step_01",)


def test_missing_dependency():
    with pytest.raises(InvalidPlanError) as info:
        plan([s("step_01"), s("step_02", "step_07")])
    assert info.value.details == ("step_02", "step_07")


def test_self_dependency():
    with pytest.raises(InvalidPlanError):
        plan([s("step_01", "step_01")])


@pytest.mark.parametrize("steps, cycle", [
    ([s("step_01", "step_02"), s("step_02", "step_01")], ("step_01", "step_02")),
    ([s("step_01", "step_03"), s("step_02", "step_01"), s("step_03", "step_02")], ("step_01", "step_02", "step_03")),
    ([s("step_01"), s("step_02", "step_01", "step_04"), s("step_03", "step_02"), s("step_04", "step_03")],
     ("step_02", "step_03", "step_04")),
])
def test_dependency_cycles(steps, cycle):
    with pytest.raises(DependencyCycleError) as info:
        plan(steps)
    assert info.value.code is PlanningErrorCode.DEPENDENCY_CYCLE and info.value.details == cycle


def test_long_chain_without_recursion_problems():
    steps = [s("step_01")] + [s(f"step_{i:02d}", f"step_{i - 1:02d}") for i in range(2, MAX_STEPS_LIMIT + 1)]
    assert len(plan(steps).steps) == MAX_STEPS_LIMIT


def test_dependency_listed_after_its_dependent():
    """Acyclic but impossible in list order: the list must itself be a valid execution order."""
    with pytest.raises(InvalidPlanError) as info:
        plan([s("step_01", "step_02"), s("step_02")])
    assert info.value.code is PlanningErrorCode.INVALID_PLAN and info.value.details == ("step_01", "step_02")


def test_hard_step_cap():
    steps = [s(f"step_{i:02d}") for i in range(1, MAX_STEPS_LIMIT + 2)]
    with pytest.raises(TooManyStepsError):
        plan(steps)


def test_empty_plan_is_a_policy_question():
    assert plan([]).steps == ()
