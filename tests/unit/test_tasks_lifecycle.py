"""Lifecycle table: exactly the allowed edges; everything else refused."""

import itertools

import pytest

from siamang.contracts import OUTBOUND
from siamang.tasks import TRANSITIONS, InvalidTransitionError, TaskAction, TaskStatus
from siamang.tasks.lifecycle import EDGES, EVENTS, FINAL, plan

S = TaskStatus
EXPECTED_EDGES = {
    (S.QUEUED, S.RUNNING), (S.QUEUED, S.CANCELLED), (S.QUEUED, S.FAILED),
    (S.RUNNING, S.COMPLETED), (S.RUNNING, S.FAILED), (S.RUNNING, S.WAITING), (S.RUNNING, S.CANCELLED),
    (S.WAITING, S.QUEUED), (S.WAITING, S.RUNNING), (S.WAITING, S.CANCELLED), (S.WAITING, S.FAILED),
    (S.FAILED, S.QUEUED),  # only via the explicit retry action
}


def test_edges_are_exactly_the_brief():
    assert EDGES == EXPECTED_EDGES


def test_every_action_has_an_outbound_event():
    assert set(EVENTS) == set(TaskAction) == set(TRANSITIONS)
    assert set(EVENTS.values()) <= OUTBOUND


@pytest.mark.parametrize("action, current", list(itertools.product(TaskAction, TaskStatus)))
def test_every_pair(action, current):
    sources, target = TRANSITIONS[action]
    if current in sources:
        assert plan(action, current) is target
    else:
        with pytest.raises(InvalidTransitionError) as info:
            plan(action, current, task_id="tsk_1")
        assert info.value.current == str(current) and info.value.task_id == "tsk_1"


@pytest.mark.parametrize("current, action", [
    (S.COMPLETED, TaskAction.START), (S.COMPLETED, TaskAction.REQUEUE), (S.COMPLETED, TaskAction.RETRY),
    (S.CANCELLED, TaskAction.START), (S.CANCELLED, TaskAction.RETRY), (S.FAILED, TaskAction.START),
    (S.FAILED, TaskAction.COMPLETE), (S.QUEUED, TaskAction.COMPLETE), (S.QUEUED, TaskAction.WAIT),
])
def test_examples_that_must_fail(current, action):
    with pytest.raises(InvalidTransitionError):
        plan(action, current)


def test_final_statuses_have_no_way_out():
    for status in FINAL:
        assert not any(src == status for src, _ in EDGES)
    # failed leaves only through retry
    assert [a for a, (srcs, _) in TRANSITIONS.items() if S.FAILED in srcs] == [TaskAction.RETRY]


def test_table_is_read_only():
    with pytest.raises(TypeError):
        TRANSITIONS[TaskAction.START] = (frozenset(TaskStatus), S.RUNNING)


def test_unknown_action_or_status():
    with pytest.raises(ValueError):
        plan("execute", "queued")
    with pytest.raises(ValueError):
        plan("start", "accepted")
