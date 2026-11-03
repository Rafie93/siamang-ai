"""PlanStep: one described unit of work; immutable; never an agent, tool, or command."""

import dataclasses

import pytest

from siamang.planner import PlanStep, StepRole
from siamang.planner.errors import InvalidPlanError
from siamang.planner.step import step_id


def step(**over):
    data = dict(step_id="step_02", title="Define approach", description="Choose how to make the change.",
                role="design", dependencies=("step_01",))
    data.update(over)
    return PlanStep(**data)


def test_valid_step_and_dict():
    s = step(constraints=["no new deps"], metadata={"estimate": "small"})
    assert s.role is StepRole.DESIGN and s.dependencies == ("step_01",)
    assert s.to_dict() == {"step_id": "step_02", "title": "Define approach",
                           "description": "Choose how to make the change.", "role": "design",
                           "dependencies": ["step_01"], "constraints": ["no new deps"],
                           "metadata": {"estimate": "small"}}


def test_step_is_immutable():
    s = step(metadata={"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.title = "x"
    with pytest.raises(TypeError):
        s.metadata["a"] = 2
    assert isinstance(s.dependencies, tuple) and isinstance(s.constraints, tuple)


def test_positional_ids():
    assert [step_id(i) for i in (1, 2, 10, 100)] == ["step_01", "step_02", "step_10", "step_100"]


@pytest.mark.parametrize("over", [
    {"step_id": "step_1"}, {"step_id": "01"}, {"step_id": "agent.echo"}, {"step_id": "step_01\n"},
    {"step_id": ""}, {"step_id": None}, {"step_id": "step_0001"},
    {"title": ""}, {"title": "x" * 121}, {"title": "a\nb"}, {"title": None},
    {"description": ""}, {"description": "x" * 2001}, {"description": "x\x07"},
    {"role": "agent.echo"}, {"role": "programmer"}, {"role": "shell"}, {"role": None},
    {"dependencies": "step_01"}, {"dependencies": ["step_1"]}, {"dependencies": ["step_01", "step_01"]},
    {"dependencies": ["step_02"]},  # self
    {"constraints": "x"}, {"constraints": [""]},
    {"metadata": {"password": "x"}}, {"metadata": {"agent_id": "agent.echo"}}, {"metadata": {"command": "x"}},
    {"metadata": {"tool": "git.status"}}, {"metadata": []},
])
def test_invalid_steps(over):
    with pytest.raises(InvalidPlanError):
        step(**over)


def test_role_is_not_an_agent():
    assert not any(r.value.startswith("agent") for r in StepRole)
    assert "agent_id" not in {f.name for f in dataclasses.fields(PlanStep)}
