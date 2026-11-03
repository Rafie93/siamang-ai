"""ReviewFinding and ReviewResult: fixed vocabularies, derived status, QA evidence honoured, deep immutability, hash."""

import dataclasses
import json

import pytest

from siamang.reviewer import ReviewFinding, ReviewResult, ReviewStatus
from siamang.reviewer.errors import ReviewResultError
from siamang.reviewer.result import DIMENSIONS, FINDINGS_LIMIT, NOT_VERIFIED, derive_status

H1, H2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def finding(code="alignment.unrelated_patch", severity="error", category="alignment", message="Unrelated.", **kw):
    return ReviewFinding(code, severity, category, message, **kw)


WARN = finding("risk.deletion", "warning", "risk", "Deletes a file.", path="src/a.py")
INFO = finding("qa.passed", "info", "verification", "QA passed.")
INCONCLUSIVE = finding("qa.inconclusive", "warning", "verification", "Incomplete.", inconclusive=True)


def result(findings=(), status=None, qa_status="pass", **over):
    findings = tuple(findings)
    derived = derive_status(findings) if all(isinstance(f, ReviewFinding) for f in findings) else "pass"
    data = dict(review_id="rev_A1", task_id="tsk_01ABC", project_id="prj_01ABC", plan_id="pln_01ABC",
                analysis_id="ana_01ABC", patch_id="pch_01ABC", patch_hash=H1, qa_id="qa_01ABC", qa_hash=H2,
                qa_status=qa_status, status=status or derived, findings=findings, policy={"max_changes": 20},
                metadata={"strategy": "review.static", "operations": {"create": 1}})
    data.update(over)
    return ReviewResult(**data)


# --- findings ---------------------------------------------------------------------------


def test_finding_fields():
    f = finding(path="src/a.py", metadata={"qa_codes": ["patch.hash_mismatch"]})
    assert f.blocking and f.to_dict()["path"] == "src/a.py" and f.metadata["qa_codes"] == ("patch.hash_mismatch",)
    assert INCONCLUSIVE.inconclusive and not INCONCLUSIVE.blocking


@pytest.mark.parametrize("kw", [
    {"code": "Bad Code"}, {"code": "nodot"}, {"code": "a.b.c"}, {"severity": "fatal"}, {"severity": "blocker"},
    {"category": "style"}, {"category": "approval"}, {"message": ""}, {"message": "x" * 301},
    {"message": "a\x00b"}, {"message": "line\nbreak"}, {"message": "API_KEY=abcd1234efgh5678 found"},
    {"message": "see /Users/someone/web/x"}, {"message": "at ~/x"}, {"message": "C:\\Windows"},
    {"message": "token ghp_abcdefghijklmnopqrstuvwxyz0123456789"}, {"message": "-----BEGIN PRIVATE KEY-----"},
    {"path": "/etc/passwd"}, {"path": "../x"}, {"path": "a\nb"}, {"path": "C:/x"}, {"path": "\\\\srv\\x"},
    {"path": 5}, {"inconclusive": True}, {"inconclusive": "yes"}, {"metadata": {"command": "rm"}},
    {"metadata": "x"}, {"metadata": {"blob": "x" * 2000}},
])
def test_finding_rejects_unsafe_or_unknown(kw):
    with pytest.raises(ReviewResultError):
        finding(**kw)


def test_finding_is_deeply_immutable():
    meta = {"nested": {"list": [1, 2]}}
    f = finding(metadata=meta)
    meta["nested"]["list"].append(3)
    assert f.metadata["nested"]["list"] == (1, 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.severity = "info"
    for mutate in (lambda: f.metadata.__setitem__("x", 1), lambda: f.metadata["nested"].__setitem__("x", 1),
                   lambda: f.metadata["nested"]["list"].append(3)):
        with pytest.raises((TypeError, AttributeError)):
            mutate()


# --- status -----------------------------------------------------------------------------


@pytest.mark.parametrize("findings, expected", [
    ((), "pass"), ((INFO,), "pass"), ((WARN,), "concern"), ((INFO, WARN), "concern"),
    ((INCONCLUSIVE,), "inconclusive"), ((WARN, INCONCLUSIVE), "inconclusive"), ((finding(),), "fail"),
    ((finding(), INCONCLUSIVE, WARN), "fail"),
])
def test_status_is_derived(findings, expected):
    assert derive_status(findings).value == expected
    qa = "inconclusive" if expected == "inconclusive" else ("fail" if expected == "fail" else "pass")
    assert result(findings, qa_status=qa).status.value == expected


@pytest.mark.parametrize("findings, claimed", [((finding(),), "pass"), ((WARN,), "pass"), ((), "concern"),
                                               ((INCONCLUSIVE,), "concern"), ((), "approved"), ((), "rejected"),
                                               ((), "accepted"), ((), "merged")])
def test_status_cannot_disagree_or_use_approval_words(findings, claimed):
    with pytest.raises(ReviewResultError):
        result(findings, status=claimed)


def test_qa_evidence_is_honoured():
    with pytest.raises(ReviewResultError):  # QA fail -> the review can never pass or merely raise a concern
        result((), qa_status="fail")
    with pytest.raises(ReviewResultError):
        result((WARN,), qa_status="fail")
    with pytest.raises(ReviewResultError):  # QA inconclusive -> never pass or concern
        result((WARN,), qa_status="inconclusive")
    with pytest.raises(ReviewResultError):
        result((), qa_status="maybe")
    assert result((INCONCLUSIVE,), qa_status="inconclusive").status is ReviewStatus.INCONCLUSIVE
    assert result((finding(),), qa_status="inconclusive").status is ReviewStatus.FAIL
    assert result((WARN,), qa_status="pass").status is ReviewStatus.CONCERN  # QA pass != review pass


def test_vocabulary():
    assert {s.value for s in ReviewStatus} == {"pass", "concern", "fail", "inconclusive"}
    assert DIMENSIONS == ("verification", "alignment", "scope", "maintainability", "consistency", "risk",
                          "feasibility")
    assert {"runtime_behavior", "semantic_correctness", "integration_behavior", "production_behavior",
            "approval"} <= set(NOT_VERIFIED)


def test_not_verified_cannot_be_dropped():
    with pytest.raises(ReviewResultError):
        result(not_verified=("approval",))
    assert result(not_verified=(*NOT_VERIFIED, "extra")).not_verified[-1] == "extra"


@pytest.mark.parametrize("over", [
    {"review_id": "qa_A1"}, {"review_id": "rev_"}, {"task_id": "tsk_../x"}, {"project_id": ""},
    {"plan_id": "pch_x"}, {"analysis_id": "ana x"}, {"patch_id": None}, {"qa_id": "qa_../x"},
    {"patch_hash": None}, {"patch_hash": "sha256:x"}, {"qa_hash": "md5:" + "0" * 64},
    {"findings": "x"}, {"findings": [{"code": "a.b"}]}, {"findings": [INFO] * (FINDINGS_LIMIT + 1)},
    {"dimensions": "x"}, {"dimensions": ["x" * 121]}, {"policy": "x"}, {"policy": {"k": object()}},
    {"metadata": {"command": "x"}}, {"metadata": {"blob": "x" * 20_000}},
])
def test_invalid_result_fields(over):
    with pytest.raises(ReviewResultError):
        result(**over)


def test_result_is_deeply_immutable():
    policy = {"max_changes": 20, "nested": {"list": [1]}}
    r = result((WARN,), policy=policy, metadata={"operations": {"create": 1}})
    policy["nested"]["list"].append(2)
    assert r.policy["nested"]["list"] == (1,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = ReviewStatus.PASS
    for mutate in (lambda: r.findings.append(INFO), lambda: r.policy.__setitem__("x", 1),
                   lambda: r.policy["nested"]["list"].append(3), lambda: r.metadata["operations"].__setitem__("x", 1),
                   lambda: r.dimensions.append("x"), lambda: r.not_verified.append("x")):
        with pytest.raises((TypeError, AttributeError)):
            mutate()
    d = r.to_dict()
    d["findings"].clear()
    d["policy"]["max_changes"] = 0
    assert len(r.findings) == 1 and r.policy["max_changes"] == 20


def test_findings_list_is_copied():
    items = [WARN]
    r = dataclasses.replace(result((WARN,)), findings=items)  # a caller-owned list goes in
    items.append(finding())
    assert isinstance(r.findings, tuple) and r.findings == (WARN,) and r.status is ReviewStatus.CONCERN


def test_content_hash_excludes_random_identities():
    a = result((WARN,))
    b = result((WARN,), review_id="rev_B2", plan_id="pln_X", analysis_id="ana_X", patch_id="pch_X", qa_id="qa_X")
    assert a.content_hash == b.content_hash and a.content_hash.startswith("sha256:")
    assert result((WARN,), patch_hash="sha256:" + "c" * 64).content_hash != a.content_hash
    assert result((WARN,), qa_hash="sha256:" + "c" * 64).content_hash != a.content_hash
    assert result((INFO,)).content_hash != a.content_hash
    d = a.to_dict()
    assert d["review_id"] == "rev_A1" and d["content_hash"] == a.content_hash
    assert json.loads(json.dumps(d)) == d
