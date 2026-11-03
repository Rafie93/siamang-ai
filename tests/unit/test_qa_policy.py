"""QAPolicy: fixed defaults, hard caps, no override, final tasks never verifiable."""

import pytest

from siamang.qa import DEFAULT_POLICY, QAPolicy
from siamang.qa.errors import QAPolicyError
from siamang.qa.policy import LIMITS, NOT_VERIFIABLE
from siamang.tasks.lifecycle import TaskStatus


def test_defaults():
    d = DEFAULT_POLICY.to_dict()
    assert (d["max_changes"], d["max_file_size"], d["max_total_bytes"], d["max_findings"]) == (
        20, 256 * 1024, 1024 * 1024, 200)
    assert d["executes_code"] is False and d["writes"] is False and d["uses_models"] is False and d["retries"] == 0
    assert set(d["verifiable_statuses"]) == {"queued", "running", "waiting", "failed"}


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_every_limit_has_a_hard_cap(name):
    cap = LIMITS[name][1]
    for bad in (0, -1, cap + 1, 10**12, True, 1.5, "10", None):
        with pytest.raises(ValueError):
            QAPolicy(**{name: bad})
    if name != "max_file_size":
        assert QAPolicy(**{name: cap}).to_dict()[name] == cap


def test_file_size_within_total():
    with pytest.raises(ValueError):
        QAPolicy(max_file_size=512 * 1024, max_total_bytes=256 * 1024)


@pytest.mark.parametrize("statuses", [{TaskStatus.COMPLETED}, {"running", "cancelled"}, set(), {"bogus"}, "running"])
def test_final_tasks_never_verifiable(statuses):
    with pytest.raises(ValueError):
        QAPolicy(verifiable_statuses=statuses)


def test_check_task():
    p = QAPolicy(verifiable_statuses={"running"})
    p.check_task(TaskStatus.RUNNING)
    for status in (TaskStatus.QUEUED, *NOT_VERIFIABLE):
        with pytest.raises(QAPolicyError):
            p.check_task(status)


def test_no_environment_override(monkeypatch):
    for key in ("SIAMANG_QA_MAX_CHANGES", "QA_MAX_CHANGES", "MAX_CHANGES"):
        monkeypatch.setenv(key, "100000")
    assert QAPolicy().to_dict() == DEFAULT_POLICY.to_dict()
