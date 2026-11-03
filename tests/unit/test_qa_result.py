"""QAFinding and QAResult: fixed vocabularies, derived status, deep immutability, deterministic hash."""

import dataclasses

import pytest

from siamang.qa import QAFinding, QAResult, QAStatus
from siamang.qa.errors import QAResultError
from siamang.qa.result import CHECKS, FINDINGS_LIMIT, NOT_VERIFIED, derive_status

T, P, PCH, H = "tsk_01ABC", "prj_01ABC", "pch_01ABC", "sha256:" + "a" * 64


def finding(code="patch.hash_mismatch", severity="error", category="patch", message="Mismatch.", **kw):
    return QAFinding(code, severity, category, message, **kw)


def result(findings=(), status=None, **over):
    findings = tuple(findings)
    derived = derive_status(findings) if all(isinstance(f, QAFinding) for f in findings) else "pass"
    data = dict(qa_id="qa_A1", task_id=T, project_id=P, patch_id=PCH, patch_hash=H,
                status=status or derived, findings=findings,
                policy={"max_changes": 20}, snapshot={"files": [{"path": "a.py", "current": "observed"}]},
                metadata={"strategy": "qa.static"})
    data.update(over)
    return QAResult(**data)


# --- findings ---------------------------------------------------------------------------


def test_finding_fields():
    f = finding(path="src/a.py", metadata={"step_id": "step_03"})
    assert f.blocking and f.to_dict()["path"] == "src/a.py" and f.metadata["step_id"] == "step_03"
    w = finding("project.state_unverified", "warning", "consistency", "Unknown.", inconclusive=True)
    assert w.inconclusive and not w.blocking


@pytest.mark.parametrize("kw", [
    {"code": "Bad Code"}, {"code": "nodot"}, {"code": "a.b.c"}, {"severity": "fatal"}, {"severity": "critical"},
    {"category": "style"}, {"message": ""}, {"message": "x" * 301}, {"message": "a\x00b"},
    {"message": "API_KEY=abcd1234efgh5678 found"}, {"message": "see /Users/someone/web/x"}, {"message": "at ~/x"},
    {"message": "C:\\Windows"}, {"message": "token ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
    {"path": "/etc/passwd"}, {"path": "../x"}, {"path": "a\nb"}, {"path": "C:/x"}, {"path": 5},
    {"inconclusive": True}, {"inconclusive": "yes"}, {"metadata": {"command": "rm"}}, {"metadata": "x"},
])
def test_finding_rejects_unsafe_or_unknown(kw):
    with pytest.raises(QAResultError):
        finding(**kw)


def test_finding_is_deeply_immutable():
    f = finding(metadata={"nested": {"list": [1, 2]}})
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.severity = "info"
    for mutate in (lambda: f.metadata.__setitem__("x", 1), lambda: f.metadata["nested"].__setitem__("x", 1),
                   lambda: f.metadata["nested"]["list"].append(3)):
        with pytest.raises((TypeError, AttributeError)):
            mutate()


# --- status -----------------------------------------------------------------------------


def test_status_is_derived_from_findings():
    info = finding("scope.outside_analysis", "warning", "scope", "Outside.")
    inconclusive = finding("project.state_unverified", "warning", "consistency", "Unknown.", inconclusive=True)
    error = finding()
    assert result().status is QAStatus.PASS and result().passed
    assert result([info]).status is QAStatus.PASS  # a plain warning does not block
    assert result([info, inconclusive]).status is QAStatus.INCONCLUSIVE
    assert result([inconclusive, error]).status is QAStatus.FAIL and result([error]).errors == (error,)


@pytest.mark.parametrize("findings, status", [
    ((), "fail"), ((), "inconclusive"), ((finding(),), "pass"), ((finding(),), "inconclusive"),
    ((finding("scope.xx", "warning", "scope", "x", inconclusive=True),), "pass"), ((), "approved"), ((), None),
])
def test_status_cannot_disagree_with_findings(findings, status):
    with pytest.raises(QAResultError):
        result(findings, status=status or "x")


def test_missing_patch_hash_must_fail():
    with pytest.raises(QAResultError):
        result(patch_hash=None)
    assert result([finding("patch.unhashable", message="Cannot hash.")], patch_hash=None).status is QAStatus.FAIL


@pytest.mark.parametrize("over", [
    {"qa_id": "qar_1"}, {"qa_id": ""}, {"task_id": "x y"}, {"patch_id": "patch_1"}, {"patch_hash": "sha256:x"},
    {"findings": [{"code": "patch.x"}]}, {"findings": "x"}, {"findings": (finding(),) * (FINDINGS_LIMIT + 1)},
    {"checks": "all"}, {"checks": ["x" * 121]}, {"not_verified": [1]}, {"policy": "x"},
    {"policy": {"bad": object()}}, {"snapshot": [1]}, {"metadata": {"model_id": "m"}}, {"metadata": {"path": "/x"}},
])
def test_result_validation(over):
    with pytest.raises(QAResultError):
        result(**over)


# --- immutability and hashing ---------------------------------------------------------------


def test_result_is_deeply_immutable():
    r = result([finding(metadata={"a": {"b": [1]}})])
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = "pass"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.findings = ()
    for mutate in (lambda: r.findings.append(finding()), lambda: r.policy.__setitem__("x", 1),
                   lambda: r.snapshot["files"][0].__setitem__("current", "absent"),
                   lambda: r.snapshot["files"].append({}), lambda: r.metadata.__setitem__("x", 1),
                   lambda: r.findings[0].metadata["a"]["b"].append(2)):
        with pytest.raises((TypeError, AttributeError)):
            mutate()
    assert isinstance(r.checks, tuple) and r.checks == CHECKS and r.not_verified == NOT_VERIFIED


def test_mutable_inputs_are_copied():
    policy, files = {"max_changes": 20}, [{"path": "a.py"}]
    r = result(policy=policy, snapshot={"files": files})
    before = r.content_hash
    policy["max_changes"] = 1
    files[0]["path"] = "evil.py"
    assert r.content_hash == before and r.snapshot["files"][0]["path"] == "a.py"


def test_hash_excludes_identity_and_tracks_content():
    a = result(qa_id="qa_ONE", patch_id="pch_ONE")
    b = result(qa_id="qa_TWO", patch_id="pch_TWO")
    assert a.content_hash == b.content_hash and a.content() == b.content()
    assert "qa_ONE" not in str(a.content()) and "pch_ONE" not in str(a.content())
    assert result(patch_hash="sha256:" + "b" * 64).content_hash != a.content_hash
    assert result([finding()]).content_hash != a.content_hash
    d = a.to_dict()
    assert d["qa_id"] == "qa_ONE" and d["patch_id"] == "pch_ONE" and d["content_hash"] == a.content_hash
    assert a.not_verified and "approval" in " ".join(a.not_verified)
