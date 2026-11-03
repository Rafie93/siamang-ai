"""ProgrammingPolicy: fixed defaults, hard caps, no override, final tasks never programmable."""

import pytest

from siamang.programmer import DEFAULT_POLICY, ProgrammingPolicy
from siamang.programmer.errors import ProgrammingPolicyError
from siamang.programmer.policy import LIMITS, NOT_PROGRAMMABLE
from siamang.tasks.lifecycle import TaskStatus


def test_defaults():
    p = DEFAULT_POLICY
    assert (p.max_changes, p.max_file_size, p.max_total_output) == (20, 256 * 1024, 1024 * 1024)
    assert (p.max_warnings, p.max_unknowns) == (50, 50)
    assert p.programmable_statuses == {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING, TaskStatus.FAILED}
    d = p.to_dict()
    assert d["model_calls_per_request"] == 1 and d["retries"] == 0
    assert d["writes"] is False and d["applies_patches"] is False and d["executes"] is False


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_every_limit_has_a_hard_cap(name):
    cap = LIMITS[name][1]
    for bad in (0, -1, cap + 1, 10**12, True, 1.5, "10", None, float("inf")):
        with pytest.raises(ValueError):
            ProgrammingPolicy(**{name: bad})
    if name != "max_file_size":
        assert ProgrammingPolicy(**{name: cap}).to_dict()[name] == cap


def test_file_size_within_total():
    with pytest.raises(ValueError):
        ProgrammingPolicy(max_file_size=512 * 1024, max_total_output=256 * 1024)


@pytest.mark.parametrize("statuses", [{TaskStatus.COMPLETED}, {TaskStatus.RUNNING, TaskStatus.CANCELLED},
                                      set(), {"bogus"}, "running"])
def test_final_tasks_are_never_programmable(statuses):
    with pytest.raises(ValueError):
        ProgrammingPolicy(programmable_statuses=statuses)


def test_checks():
    p = ProgrammingPolicy(programmable_statuses={"running"})
    p.check_task(TaskStatus.RUNNING)
    for status in (TaskStatus.QUEUED, *NOT_PROGRAMMABLE):
        with pytest.raises(ProgrammingPolicyError):
            p.check_task(status)


def test_no_environment_override(monkeypatch):
    for key in ("SIAMANG_PROGRAMMER_MAX_CHANGES", "SIAMANG_PROGRAMMING_MAX_CHANGES", "MAX_CHANGES"):
        monkeypatch.setenv(key, "100000")
    assert ProgrammingPolicy().max_changes == 20 and ProgrammingPolicy().to_dict() == DEFAULT_POLICY.to_dict()
