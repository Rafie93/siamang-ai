"""ReviewerPolicy: fixed defaults, hard caps, no override, final tasks never reviewable, never approves."""

import dataclasses

import pytest

from siamang.reviewer import DEFAULT_POLICY, ReviewerPolicy
from siamang.reviewer.errors import ReviewPolicyError
from siamang.reviewer.policy import LIMITS, NOT_REVIEWABLE
from siamang.tasks.lifecycle import TaskStatus


def test_defaults():
    d = DEFAULT_POLICY.to_dict()
    assert {k: d[k] for k in LIMITS} == {"max_changes": 20, "max_total_bytes": 1024 * 1024, "max_findings": 100,
                                         "broad_change_count": 10, "max_directories": 3, "max_new_files": 5,
                                         "max_file_lines": 400}
    for flag in ("reads_project", "executes_code", "writes", "uses_models", "approves", "applies", "repairs"):
        assert d[flag] is False, flag
    assert d["retries"] == 0
    assert set(d["reviewable_statuses"]) == {"queued", "running", "waiting", "failed"}


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_every_limit_has_a_hard_cap(name):
    cap = LIMITS[name][1]
    for bad in (0, -1, cap + 1, 10**12, True, 1.5, "10", None):
        with pytest.raises(ValueError):
            ReviewerPolicy(**{name: bad})
    assert ReviewerPolicy(**{name: cap}).to_dict()[name] == cap


@pytest.mark.parametrize("statuses", [{TaskStatus.COMPLETED}, {"running", "cancelled"}, set(), {"bogus"}, "running"])
def test_final_tasks_never_reviewable(statuses):
    with pytest.raises(ValueError):
        ReviewerPolicy(reviewable_statuses=statuses)


def test_check_task():
    p = ReviewerPolicy(reviewable_statuses={"running"})
    p.check_task(TaskStatus.RUNNING)
    for status in (TaskStatus.QUEUED, *NOT_REVIEWABLE):
        with pytest.raises(ReviewPolicyError):
            p.check_task(status)


def test_policy_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_POLICY.max_findings = 10**6
    statuses = {"running", "queued"}
    p = ReviewerPolicy(reviewable_statuses=statuses)
    statuses.add("completed")  # the caller's set is copied
    assert isinstance(p.reviewable_statuses, frozenset) and TaskStatus.COMPLETED not in p.reviewable_statuses
    d = p.to_dict()
    d["approves"] = True
    assert p.to_dict()["approves"] is False


def test_no_environment_override(monkeypatch):
    for key in ("SIAMANG_REVIEWER_MAX_CHANGES", "REVIEWER_MAX_FINDINGS", "MAX_CHANGES", "SIAMANG_REVIEWER_APPROVES"):
        monkeypatch.setenv(key, "100000")
    assert ReviewerPolicy().to_dict() == DEFAULT_POLICY.to_dict()
