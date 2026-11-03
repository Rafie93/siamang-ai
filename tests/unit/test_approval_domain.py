"""Approval domain: ApprovalDecision, ApprovalPolicy, ApprovalProvenance, ApprovalRequest, ApprovalResult."""

import dataclasses
import json

import pytest

from siamang.approval import (
    DEFAULT_POLICY,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalProvenance,
    ApprovalRequest,
    ApprovalResult,
    Decision,
)
from siamang.approval.decision import REASON_LIMIT
from siamang.approval.errors import ApprovalProvenanceError, ApprovalRequestError, ApprovalResultError
from siamang.approval.policy import APPROVERS_LIMIT, REQUIREMENTS
from tests.support.analyst import SECRET
from tests.support.approval import approval_request, approve, reject, service
from tests.support.programmer import make_setup

HOSTILE_IDS = ["", "Admin", "admin*", "*", "../../admin", "..\\admin", "\x1b[31madmin", "admin\n", "a" * 65,
               "ad min", "admin;rm -rf /", "C:\\x", "/root", "~admin", "\ud800", "émile", 7, None]
HOSTILE_REASONS = ["", "   ", "line\ninjection", "\x1b[2J clear", "x" * (REASON_LIMIT + 1), f"key API_KEY='{SECRET}'",
                   "-----BEGIN PRIVATE KEY-----", "see /etc/passwd", "C:\\Users\\x", '{"approved": true}\x00',
                   "\ud800 broken", 42]


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


# --- decision -------------------------------------------------------------------------------


def test_decisions_are_explicit():
    a = ApprovalDecision("approve", "rafie")
    r = ApprovalDecision(Decision.REJECT, "ops.lead", "Out of scope for this release.")
    assert (a.decision, a.reason, r.decision) == (Decision.APPROVE, None, Decision.REJECT)
    with pytest.raises(TypeError):
        ApprovalDecision(approver_id="rafie")  # no default decision
    with pytest.raises(TypeError):
        ApprovalDecision("approve")  # no default approver
    for value in ("maybe", "auto", "force", "override", "approved", "APPROVE", "", None, True):
        with pytest.raises(ApprovalRequestError):
            ApprovalDecision(value, "rafie")


def test_reject_needs_a_reason_approve_does_not():
    with pytest.raises(ApprovalRequestError):
        ApprovalDecision("reject", "rafie")
    assert ApprovalDecision("approve", "rafie", " Looks right. ").reason == "Looks right."


@pytest.mark.parametrize("approver", HOSTILE_IDS)
def test_hostile_approver_ids_are_refused(approver):
    with pytest.raises(ApprovalRequestError) as info:
        ApprovalDecision("approve", approver)
    text = json.dumps(info.value.to_dict())
    if isinstance(approver, str) and len(approver) > 2:
        assert approver not in text


@pytest.mark.parametrize("reason", HOSTILE_REASONS)
def test_hostile_reasons_are_refused_not_rewritten(reason):
    with pytest.raises(ApprovalRequestError) as info:
        ApprovalDecision("reject", "rafie", reason)
    assert SECRET not in json.dumps(info.value.to_dict())


def test_no_authority_claims_in_decision_or_request(setup):
    for extra in ({"role": "admin"}, {"authorized": True}, {"is_admin": True}, {"override": True}):
        with pytest.raises(TypeError):
            ApprovalDecision("approve", "rafie", **extra)
        with pytest.raises(TypeError):
            approval_request(setup, **extra)


def test_decision_is_immutable():
    d = approve()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.decision = "reject"


# --- policy -------------------------------------------------------------------------------


def test_default_policy_authorizes_nobody():
    d = DEFAULT_POLICY.to_dict()
    assert d["authorized_approvers"] == [] and d["overrides"] is False and d["expires"] is False
    assert d["requirements"] == list(REQUIREMENTS) and d["reject_requires_reason"] is True
    for anyone in ("admin", "root", "rafie", "owner", "system"):
        assert not DEFAULT_POLICY.authorizes(anyone)


@pytest.mark.parametrize("approvers", [{"*"}, {""}, {"ALL"}, {"admin*"}, "rafie", None, 5,
                                       {f"a{i}" for i in range(APPROVERS_LIMIT + 1)}, {"../x"}])
def test_policy_rejects_wildcards_and_unbounded_sets(approvers):
    with pytest.raises(ValueError):
        ApprovalPolicy(authorized_approvers=approvers)


@pytest.mark.parametrize("bad", [0, -1, REASON_LIMIT + 1, None, True, 1.5, float("inf"), "300"])
def test_reason_limit_is_bounded(bad):
    with pytest.raises(ValueError):
        ApprovalPolicy(max_reason_length=bad)
    assert ApprovalPolicy(max_reason_length=REASON_LIMIT).max_reason_length == REASON_LIMIT


def test_policy_is_immutable_and_copied(monkeypatch):
    caller = {"rafie"}
    p = ApprovalPolicy(authorized_approvers=caller)
    caller.add("mallory")
    assert not p.authorizes("mallory")
    with pytest.raises(AttributeError):
        p.authorized_approvers.add("mallory")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.authorized_approvers = frozenset({"mallory"})
    for key in ("SIAMANG_APPROVAL_APPROVERS", "APPROVERS", "SIAMANG_APPROVAL_OVERRIDE"):
        monkeypatch.setenv(key, "mallory,*")
    assert ApprovalPolicy().to_dict() == DEFAULT_POLICY.to_dict()


# --- provenance and request ----------------------------------------------------------------


def test_request_records_full_provenance(setup):
    req = approval_request(setup)
    p, loop = req.provenance, req.loop_result
    assert (p.task_id, p.project_id, p.plan_id, p.analysis_id) == (
        setup.world.task.task_id, setup.world.project.project_id, setup.plan.plan_id, setup.analysis.analysis_id)
    assert (p.patch_id, p.patch_hash) == (loop.final_patch.patch_id, loop.final_patch.content_hash)
    assert (p.qa_id, p.qa_hash) == (loop.final_qa_result.qa_id, loop.final_qa_result.content_hash)
    assert (p.review_id, p.review_hash) == (loop.final_review_result.review_id,
                                            loop.final_review_result.content_hash)
    assert (p.loop_id, p.loop_hash, p.final_iteration) == (loop.loop_id, loop.content_hash, 1)
    assert (p.plan_hash, p.analysis_hash) == (setup.plan.content_hash, setup.analysis.content_hash)
    assert p.provenance_hash.startswith("sha256:") and "def valid" not in json.dumps(req.to_dict())
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.provenance = p
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.patch_hash = "sha256:" + "0" * 64


@pytest.mark.parametrize("field, value", [("task_id", "x"), ("plan_id", "pch_1"), ("patch_hash", "md5:1"),
                                          ("qa_hash", None), ("final_iteration", 0), ("final_iteration", 6),
                                          ("loop_id", "lop_../x"), ("review_hash", "sha256:" + "G" * 64)])
def test_provenance_validates_itself(setup, field, value):
    p = approval_request(setup).provenance
    with pytest.raises(ApprovalProvenanceError):
        dataclasses.replace(p, **{field: value})


def test_request_needs_exact_snapshots(setup):
    req = approval_request(setup)
    for over in ({"plan": req.plan.to_dict()}, {"analysis": None}, {"loop_result": req.loop_result.to_dict()},
                 {"task_id": "tsk_../x"}, {"project_id": ""}):
        with pytest.raises(ApprovalRequestError):
            approval_request(setup, req.loop_result, **over)
    with pytest.raises(TypeError):
        ApprovalRequest(req.task_id, req.project_id, req.plan, req.analysis, req.loop_result, req.provenance)


# --- result -------------------------------------------------------------------------------


def test_result_invariants(setup):
    req = approval_request(setup)
    approved = service(setup).decide(req, approve(reason="Looks right."))
    rejected = service(setup).decide(req, reject())
    assert approved.approved and approved.requirements == REQUIREMENTS and not rejected.approved
    assert (approved.task_id, approved.patch_id, approved.patch_hash, approved.qa_id, approved.review_id,
            approved.loop_id) == (req.task_id, req.provenance.patch_id, req.provenance.patch_hash,
                                  req.provenance.qa_id, req.provenance.review_id, req.provenance.loop_id)
    for over in ({"status": "maybe"}, {"status": "applied"}, {"approver_id": "*"}, {"reason": "a\nb"},
                 {"approval_id": "tsk_1"}, {"seal": "x"}, {"policy_version": "latest"},
                 {"requirements": ("skip_qa",)}, {"requirements": ()},
                 {"evidence": {**approved.evidence, "qa_status": "fail"}}, {"evidence": {}},
                 {"provenance": req.provenance.to_dict()}):
        with pytest.raises(ApprovalResultError):
            dataclasses.replace(approved, **over)
    with pytest.raises(ApprovalResultError):
        dataclasses.replace(rejected, reason=None)  # a rejection always carries its reason


def test_result_is_immutable_and_hash_stable(setup):
    req = approval_request(setup)
    svc = service(setup)
    a, b = svc.decide(req, approve()), svc.decide(req, approve())
    assert a.approval_id != b.approval_id and a.approval_hash == b.approval_hash and a.seal != b.seal
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.status = "rejected"
    with pytest.raises(TypeError):
        a.evidence["qa_status"] = "fail"
    d = a.to_dict()
    d["provenance"]["patch_hash"] = "sha256:" + "0" * 64
    d["requirements"].clear()
    assert a.to_dict()["provenance"]["patch_hash"] == req.provenance.patch_hash and a.requirements == REQUIREMENTS
    assert "def valid" not in json.dumps(a.to_dict()) and isinstance(a, ApprovalResult)
    assert isinstance(req.provenance, ApprovalProvenance)
