"""Phase 19.5: adversarial tests of the approval boundary over a real chain.

Authorization (magic IDs and caller claims), policy override attempts,
provenance tampering one layer at a time, replay across patch / task /
project / chain / service instance, and HMAC seal tampering. Every attack
must be refused; none may become an approval.
"""

import dataclasses
import json
from types import MappingProxyType

import pytest

from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.approval import (
    DEFAULT_POLICY,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResult,
    ApprovalService,
    ApprovalStatus,
)
from siamang.approval.errors import ApprovalError, ApprovalErrorCode
from siamang.approval.policy import REQUIREMENTS
from siamang.cli.main import main
from siamang.planner import Planner, PlanningRequest
from siamang.programmer.safety import deep_freeze, thaw
from siamang.reviewer import ReviewStatus
from siamang.storage import Database
from siamang.tasks import TaskRequest
from tests.support.approval import (
    APPROVER,
    CLEAN,
    CONCERN,
    KEY,
    POLICY,
    approval_request,
    approve,
    loop_result,
    reject,
    service,
)
from tests.support.programmer import INSTRUCTION, change, make_setup, reply, sha
from tests.support.reviewer import TEST_VALIDATION, VALIDATION
from tests.support.qa import forge

C = ApprovalErrorCode
CLEAN_B = reply([change(VALIDATION, "create", "def valid(user):\n    return user is not None\n"),
                 change(TEST_VALIDATION, "create", "def test_valid():\n    assert True\n")])  # another passing patch
ZERO = "sha256:" + "0" * 64


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def other(tmp_path):
    database = Database(tmp_path / "other.db")
    (tmp_path / "o").mkdir()
    yield make_setup(database, tmp_path / "o")
    database.close()


def refused(fn, *args):
    with pytest.raises(ApprovalError) as info:
        fn(*args)
    return info.value.code


def second_task(s):
    """Another task in the same project, with its own real Plan and SystemAnalysis."""
    engine = s.world.engine
    task = engine.create(TaskRequest(project_id=s.world.project.project_id, description="Second task",
                                     source="test")).task
    ids = dict(task_id=task.task_id, project_id=s.world.project.project_id, instruction=INSTRUCTION)
    plan = Planner(engine).plan(PlanningRequest(**ids))
    analysis = SystemAnalyst(engine).analyze(AnalysisRequest(**ids, plan=plan))
    return dataclasses.replace(s, world=dataclasses.replace(s.world, task=task), plan=plan, analysis=analysis)


# --- 13. approver authorization -----------------------------------------------------------------------------


@pytest.mark.parametrize("approver", ["mallory", "admin", "root", "owner", "system", "siamang", "approver",
                                      "everyone", "all", "any", "none", "null", "true", "dewi.admin", "dewi-"])
def test_unlisted_approver_is_refused_for_approve_and_reject(setup, approver):
    req = approval_request(setup)
    svc = service(setup)
    assert refused(svc.decide, req, approve(approver)) is C.UNAUTHORIZED
    assert refused(svc.decide, req, reject(approver)) is C.UNAUTHORIZED


@pytest.mark.parametrize("approver", ["*", "%", "?", ".*", "Rafie", "RAFIE", "dewi ", " dewi", "dewi\x00",
                                      "rаfie", "dewi\n", "", "../dewi", "dewi/admin", "@dewi", 1, None,
                                      ["dewi"], {"dewi"}])
def test_malformed_or_wildcard_approver_ids_are_refused(approver):
    with pytest.raises(ApprovalError) as info:
        ApprovalDecision("approve", approver)
    assert info.value.code is C.REQUEST_INVALID


@pytest.mark.parametrize("claim", ["authorized", "is_admin", "role", "override", "force", "admin", "approved"])
def test_caller_claims_are_not_accepted(setup, claim):
    req = approval_request(setup)
    with pytest.raises(TypeError):
        ApprovalDecision("approve", "mallory", **{claim: True})
    with pytest.raises(TypeError):
        ApprovalRequest(setup.world.task.task_id, setup.world.project.project_id, setup.plan, setup.analysis,
                        req.loop_result, **{claim: True})


@pytest.mark.parametrize("claim", [("authorized", True), ("is_admin", True), ("role", "admin"), ("override", True)])
def test_claims_forced_onto_objects_change_nothing(setup, claim):
    req = approval_request(setup)
    decision = approve("mallory")
    object.__setattr__(decision, *claim)  # forced past the frozen dataclass
    object.__setattr__(req, *claim)
    assert refused(service(setup).decide, req, decision) is C.UNAUTHORIZED


def test_decision_subclass_cannot_carry_authority(setup):
    class Privileged(ApprovalDecision):
        authorized = True
        is_admin = True

    assert refused(service(setup).decide, approval_request(setup), Privileged("approve", APPROVER)) \
        is C.REQUEST_INVALID


def test_admin_and_root_are_ordinary_ids(setup):
    req = approval_request(setup)
    only_admin = service(setup, policy=ApprovalPolicy(authorized_approvers={"admin"}))
    assert only_admin.decide(req, approve("admin")).approved  # authorized only because listed
    assert refused(only_admin.decide, req, approve("root")) is C.UNAUTHORIZED
    assert refused(only_admin.decide, req, approve(APPROVER)) is C.UNAUTHORIZED


def test_default_policy_authorizes_nobody(setup):
    req = approval_request(setup)
    assert DEFAULT_POLICY.authorized_approvers == frozenset()
    for who in (APPROVER, "admin", "root"):
        assert refused(ApprovalService(setup.world.engine).decide, req, approve(who)) is C.UNAUTHORIZED


# --- 14. policy attacks -------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["requirements", "overrides", "force", "skip_qa", "skip_review",
                                   "evidence_requirements", "seal", "provenance", "allow_inconclusive"])
def test_policy_has_no_switch_for_requirements(field):
    with pytest.raises(TypeError):
        ApprovalPolicy(authorized_approvers={APPROVER}, **{field: False})


@pytest.mark.parametrize("approvers", [["*"], ["admin*"], ["ALL"], [""], ["dewi", "*"], "dewi", b"dewi",
                                       [f"a{i}" for i in range(51)], [1], None, 5])
def test_policy_refuses_wildcards_and_malformed_sets(approvers):
    with pytest.raises(ValueError):
        ApprovalPolicy(authorized_approvers=approvers)


@pytest.mark.parametrize("length", [0, -1, 601, 10**6, True, 1.5, "300", None])
def test_policy_reason_bound_respects_the_hard_cap(length):
    with pytest.raises(ValueError):
        ApprovalPolicy(authorized_approvers={APPROVER}, max_reason_length=length)


def test_policy_claiming_to_contain_everyone_contains_no_one(setup):
    class Everyone:
        def __contains__(self, item):
            return True

        def __iter__(self):
            return iter(())

    svc = service(setup, policy=ApprovalPolicy(authorized_approvers=Everyone()))
    assert svc.policy.authorized_approvers == frozenset()
    assert refused(svc.decide, approval_request(setup), approve()) is C.UNAUTHORIZED


def test_policy_is_frozen_and_copies_caller_state():
    caller = {APPROVER}
    policy = ApprovalPolicy(authorized_approvers=caller)
    caller.add("mallory")
    assert policy.authorized_approvers == frozenset({APPROVER}) and not policy.authorizes("mallory")
    for name, value in (("authorized_approvers", frozenset({"mallory"})), ("max_reason_length", 600)):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(policy, name, value)
    with pytest.raises(AttributeError):
        policy.authorized_approvers.add("mallory")


def test_environment_cannot_change_the_policy(setup, monkeypatch, capsys):
    for key, value in (("SIAMANG_APPROVAL_AUTHORIZED_APPROVERS", "mallory"), ("SIAMANG_APPROVAL_OVERRIDE", "1"),
                       ("SIAMANG_APPROVAL_MAX_REASON_LENGTH", "10000"), ("SIAMANG_APPROVERS", "*"),
                       ("SIAMANG_APPROVAL_FORCE", "true")):
        monkeypatch.setenv(key, value)
    assert ApprovalPolicy().authorized_approvers == frozenset() and ApprovalPolicy().max_reason_length == 300
    assert refused(ApprovalService(setup.world.engine).decide, approval_request(setup), approve("mallory")) \
        is C.UNAUTHORIZED
    assert main(["approval", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)["policy"]
    assert shown["authorized_approvers"] == [] and shown["overrides"] is False


@pytest.mark.parametrize("argv", [["--approver", "mallory"], ["--policy", "x.toml"], ["--authorized", "mallory"],
                                  ["--override"], ["--skip-qa"], ["--force"], ["approve", "--yes"]])
def test_cli_cannot_change_the_policy(argv):
    with pytest.raises(SystemExit) as info:
        main(["approval", *argv])
    assert info.value.code == 2


def test_request_cannot_carry_a_policy(setup):
    req = approval_request(setup)
    with pytest.raises(TypeError):
        ApprovalRequest(req.task_id, req.project_id, req.plan, req.analysis, req.loop_result,
                        policy=ApprovalPolicy(authorized_approvers={"mallory"}))
    with pytest.raises(TypeError):
        ApprovalService(setup.world.engine, policy={"authorized_approvers": ["mallory"]})


def test_narrowed_approvers_after_approval_fail_verification(setup):
    req = approval_request(setup)
    approval = service(setup).decide(req, approve())
    narrowed = ApprovalService(setup.world.engine, policy=ApprovalPolicy(authorized_approvers={"ops.lead"}),
                               seal_key=KEY)
    assert refused(narrowed.verify, approval, req) is C.UNAUTHORIZED
    emptied = ApprovalService(setup.world.engine, policy=DEFAULT_POLICY, seal_key=KEY)
    assert refused(emptied.verify, approval, req) is C.UNAUTHORIZED


def test_narrowed_reason_bound_after_approval_fails_verification(setup):
    req = approval_request(setup)
    approval = service(setup).decide(req, reject(reason="x" * 100))
    narrowed = ApprovalService(setup.world.engine, policy=ApprovalPolicy(authorized_approvers={APPROVER},
                                                                         max_reason_length=50), seal_key=KEY)
    assert refused(narrowed.verify, approval, req) is C.REQUEST_INVALID


def test_widened_policy_does_not_invalidate_an_approval(setup):
    req = approval_request(setup)
    approval = service(setup).decide(req, approve())
    wider = ApprovalService(setup.world.engine, seal_key=KEY,
                            policy=ApprovalPolicy(authorized_approvers={APPROVER, "ops.lead", "qa.lead"}))
    wider.verify(approval, req)


@pytest.mark.parametrize("requirements", [(), REQUIREMENTS[:-1], tuple(r for r in REQUIREMENTS if r != "qa_pass"),
                                          ("approver_authorized",), (*REQUIREMENTS, "override")])
def test_an_approval_cannot_drop_or_add_requirements(setup, requirements):
    approval = service(setup).decide(approval_request(setup), approve())
    with pytest.raises(ApprovalError):
        dataclasses.replace(approval, requirements=requirements)


# --- 15. provenance: one layer at a time (the rest of the chain stays consistent) ----------------------------


def _freeze(mapping, **extra):
    return deep_freeze({**thaw(mapping), **extra})


def _new_patch_content(req):
    change = req.loop_result.final_patch.changes[0]
    object.__setattr__(change, "after_content", "def valid(user):\n    return user is not None\n")
    object.__setattr__(change, "after_hash", sha("def valid(user):\n    return user is not None\n"))


def _other_project(s):
    root = s.root.parent / "other_project"
    root.mkdir(exist_ok=True)
    return s.world.projects.register(name="Other", root_path=str(root)).project_id


def _final_round_swap(req, s):
    object.__setattr__(req, "loop_result", loop_result(s, CONCERN, CLEAN))  # valid, two rounds, same task


LAYERS = {
    # each mutates exactly one thing in the request after the approval was issued
    "01_task_id": lambda req, s: object.__setattr__(req, "task_id", second_task(s).world.task.task_id),
    "02_project_id": lambda req, s: object.__setattr__(req, "project_id", _other_project(s)),
    "03_plan_id": lambda req, s: object.__setattr__(req, "plan", dataclasses.replace(req.plan, plan_id="pln_01OTHER")),
    "04_analysis_id": lambda req, s: object.__setattr__(req, "analysis", dataclasses.replace(
        req.analysis, analysis_id="ana_01OTHER")),
    "05_patch_id": lambda req, s: object.__setattr__(req.loop_result.final_patch, "patch_id", "pch_01OTHER"),
    "06_patch_content": lambda req, s: _new_patch_content(req),
    "07_patch_hash": lambda req, s: object.__setattr__(req.loop_result.iterations[-1], "patch_hash", ZERO),
    "08_qa_id": lambda req, s: object.__setattr__(req.loop_result.final_qa_result, "qa_id", "qa_01OTHER"),
    "09_qa_hash": lambda req, s: object.__setattr__(req.loop_result.final_review_result, "qa_hash", ZERO),
    "10_review_result": lambda req, s: object.__setattr__(req.loop_result.final_review_result, "status",
                                                          ReviewStatus.CONCERN),
    "11_review_hash": lambda req, s: object.__setattr__(req.loop_result.final_review_result, "metadata", _freeze(
        req.loop_result.final_review_result.metadata, note="altered")),
    "12_loop_result": lambda req, s: object.__setattr__(req.loop_result, "metadata", _freeze(
        req.loop_result.metadata, note="altered")),
    "13_final_round": _final_round_swap,
}


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_each_provenance_layer_altered_after_approval_is_refused(setup, layer):
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve())
    svc.verify(approval, req)
    LAYERS[layer](req, setup)
    code = refused(svc.verify, approval, req)
    assert code in (C.PROVENANCE_INVALID, C.REQUEST_INVALID), layer
    assert refused(svc.decide, req, approve()) in (C.PROVENANCE_INVALID, C.REQUEST_INVALID), layer


# 11-13 alter non-semantic metadata or swap in another whole valid loop: before a request exists that is a
# different (self-consistent) chain, not a tampered one; it gets its own provenance, and re-derivation still
# applies. After approval they are refused (test above).
BROKEN_BEFORE = sorted(set(LAYERS) - {"11_review_hash", "12_loop_result", "13_final_round"})


@pytest.mark.parametrize("layer", BROKEN_BEFORE)
def test_each_provenance_layer_altered_before_the_request_is_refused(setup, layer):
    probe = approval_request(setup)  # a live, valid request to mutate, then rebuild from its parts
    LAYERS[layer](probe, setup)
    with pytest.raises(ApprovalError) as info:
        ApprovalRequest(probe.task_id, probe.project_id, probe.plan, probe.analysis, probe.loop_result)
    assert info.value.code in (C.PROVENANCE_INVALID, C.REQUEST_INVALID)


@pytest.mark.parametrize("layer", ["11_review_hash", "12_loop_result"])
def test_metadata_only_rewrite_before_the_request_binds_a_new_hash(setup, layer):
    """Accepted as a distinct chain: the new hash is what gets bound, and no prior approval covers it."""
    original = approval_request(setup)
    approval = service(setup).decide(original, approve())
    probe = approval_request(setup, original.loop_result)
    LAYERS[layer](probe, setup)
    rebuilt = ApprovalRequest(probe.task_id, probe.project_id, probe.plan, probe.analysis, probe.loop_result)
    assert rebuilt.provenance != approval.provenance
    assert refused(service(setup).verify, approval, rebuilt) is C.RESULT_INVALID


PROVENANCE_SWAPS = {
    "task_id": "tsk_01OTHERTASK000000000000000", "project_id": "prj_01OTHERPROJECT000000000000",
    "plan_id": "pln_01OTHER", "plan_hash": ZERO, "analysis_id": "ana_01OTHER", "analysis_hash": ZERO,
    "loop_id": "lop_01OTHER", "loop_hash": ZERO, "final_iteration": 2, "patch_id": "pch_01OTHER", "patch_hash": ZERO,
    "qa_id": "qa_01OTHER", "qa_hash": ZERO, "review_id": "rev_01OTHER", "review_hash": ZERO,
}


@pytest.mark.parametrize("field", sorted(PROVENANCE_SWAPS))
def test_each_provenance_field_altered_in_the_result_is_refused(setup, field):
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve())
    try:
        provenance = dataclasses.replace(approval.provenance, **{field: PROVENANCE_SWAPS[field]})
    except ApprovalError:
        provenance = forge(approval.provenance, **{field: PROVENANCE_SWAPS[field]})
    altered = forge(approval, provenance=provenance)
    assert refused(svc.verify, altered, req) is C.RESULT_INVALID, field


@pytest.mark.parametrize("evidence", [
    {"qa_status": "fail"}, {"review_status": "concern"}, {"loop_status": "inconclusive"},
    {"loop_stop_reason": "max_iterations"}, {"extra": "pass"},
])
def test_approval_evidence_altered_is_refused(setup, evidence):
    req = approval_request(setup)
    svc = service(setup)
    rejected = svc.decide(req, reject())
    altered = forge(rejected, evidence=MappingProxyType({**rejected.evidence, **evidence}))
    assert refused(svc.verify, altered, req) is C.RESULT_INVALID


# --- 16. replay -------------------------------------------------------------------------------------------


def test_replay_against_a_different_patch(setup):
    svc = service(setup)
    req_a = approval_request(setup, loop_result(setup, CLEAN))
    req_b = approval_request(setup, loop_result(setup, CLEAN_B))
    assert req_a.provenance.patch_hash != req_b.provenance.patch_hash
    approval_a = svc.decide(req_a, approve())
    assert refused(svc.verify, approval_a, req_b) is C.RESULT_INVALID
    retargeted = dataclasses.replace(approval_a, provenance=req_b.provenance)  # approval_id A, scope B
    assert refused(svc.verify, retargeted, req_b) is C.RESULT_INVALID


def test_replay_against_a_different_task(setup):
    svc = service(setup)
    approval = svc.decide(approval_request(setup), approve())
    s2 = second_task(setup)
    req_task_b = approval_request(s2, loop_result(s2, CLEAN))
    assert req_task_b.task_id != approval.task_id and req_task_b.project_id == approval.project_id
    assert refused(svc.verify, approval, req_task_b) is C.RESULT_INVALID
    assert refused(svc.verify, dataclasses.replace(approval, provenance=req_task_b.provenance), req_task_b) \
        is C.RESULT_INVALID


def test_replay_against_a_different_project(setup, other):
    approval = service(setup).decide(approval_request(setup), approve())
    req_other = approval_request(other)
    for svc in (service(setup), service(other)):  # even with the same injected key
        assert refused(svc.verify, approval, req_other) is C.RESULT_INVALID


def test_replay_against_an_identical_but_distinct_chain(setup):
    svc = service(setup)
    req_a, req_b = approval_request(setup), approval_request(setup)  # same content, separate runs
    assert req_a.loop_result.content_hash == req_b.loop_result.content_hash
    assert req_a.provenance != req_b.provenance  # bound by identity as well as content
    approval = svc.decide(req_a, approve())
    assert refused(svc.verify, approval, req_b) is C.RESULT_INVALID


def test_replay_through_another_service_instance(setup):
    req = approval_request(setup)
    approval = service(setup).decide(req, approve())
    for elsewhere in (ApprovalService(setup.world.engine, policy=POLICY),
                      ApprovalService(setup.world.engine, policy=POLICY, seal_key=b"x" * 32)):
        assert refused(elsewhere.verify, approval, req) is C.RESULT_INVALID  # another key: not issued here
    # documented semantics: the seal is process-local; an instance given the same key recognises it
    ApprovalService(setup.world.engine, policy=POLICY, seal_key=KEY).verify(approval, req)


def test_same_chain_reverifies_by_design(setup):
    """Not single-use: verify is a pure check. Consumption/idempotency belongs to a future execution layer."""
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve())
    for _ in range(3):
        svc.verify(approval, req)


# --- 17. HMAC seal tampering ---------------------------------------------------------------------------------


def _forged_seal(result, key=b"attacker-key-" + b"0" * 32):
    import hashlib
    import hmac

    return "hmac-sha256:" + hmac.new(key, f"{result.approval_id}\n{result.approval_hash}".encode(),
                                     hashlib.sha256).hexdigest()


ALTERATIONS = {
    "approval_id": dict(approval_id="apr_01FORGED"),
    "decision": dict(status="rejected", reason="Changed my mind."),
    "approver": dict(approver_id="ops.lead"),
    "reason": dict(reason="Different words."),
    "policy_version": dict(policy_version="2"),
}


@pytest.mark.parametrize("name", sorted(ALTERATIONS))
def test_altered_result_fields_break_the_seal(setup, name):
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve(reason="Looks right."))
    altered = dataclasses.replace(approval, **ALTERATIONS[name])
    assert refused(svc.verify, altered, req) is C.RESULT_INVALID


def test_altered_chain_breaks_the_seal(setup):
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve())
    other_req = approval_request(setup)
    assert refused(svc.verify, dataclasses.replace(approval, provenance=other_req.provenance), other_req) \
        is C.RESULT_INVALID


@pytest.mark.parametrize("seal", [
    "hmac-sha256:" + "0" * 64, "hmac-sha256:" + "f" * 64, "hmac-sha256:" + "a" * 63, "hmac-sha256:" + "A" * 64,
    "sha256:" + "0" * 64, "hmac-sha256:", "", None, b"hmac", 0, "hmac-sha256:" + "0" * 64 + "\n",
    "HMAC-SHA256:" + "0" * 64,
])
def test_forged_truncated_or_malformed_seal_is_refused(setup, seal):
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve())
    assert refused(svc.verify, forge(approval, seal=seal), req) is C.RESULT_INVALID


def test_seal_computed_with_another_key_is_refused(setup):
    req = approval_request(setup)
    svc = service(setup)
    approval = svc.decide(req, approve())
    assert refused(svc.verify, dataclasses.replace(approval, seal=_forged_seal(approval)), req) is C.RESULT_INVALID


def test_seal_transplanted_from_another_result_is_refused(setup):
    req = approval_request(setup)
    svc = service(setup)
    rejected = svc.decide(req, reject())
    approved = svc.decide(req, approve())
    assert refused(svc.verify, dataclasses.replace(approved, seal=rejected.seal), req) is C.RESULT_INVALID
    flipped = dataclasses.replace(rejected, status="approved", reason=None, requirements=REQUIREMENTS,
                                  seal=approved.seal)  # an approval's seal on the rejection's ID
    assert refused(svc.verify, flipped, req) is C.RESULT_INVALID


def test_hand_built_result_is_refused(setup):
    req = approval_request(setup)
    forged = ApprovalResult(approval_id="apr_01HANDMADE", status="approved", approver_id=APPROVER, reason=None,
                            provenance=req.provenance, evidence={"qa_status": "pass", "review_status": "pass",
                                                                 "loop_status": "pass", "loop_stop_reason": "success"},
                            policy_version="1", requirements=REQUIREMENTS, seal="hmac-sha256:" + "0" * 64)
    assert refused(service(setup).verify, forged, req) is C.RESULT_INVALID
    assert forged.status is ApprovalStatus.APPROVED  # constructible, but worthless without the service's seal


def test_seal_is_integrity_not_identity(setup):
    """Documented limit: whoever holds the key (or the service) can issue valid results; approver_id is not
    authenticated. The seal only proves the result is unchanged since this key context issued it."""
    req = approval_request(setup)
    a = ApprovalService(setup.world.engine, policy=POLICY, seal_key=KEY).decide(req, approve())
    b = ApprovalService(setup.world.engine, policy=POLICY, seal_key=KEY).decide(req, approve())
    assert a.approval_hash == b.approval_hash and a.approver_id == b.approver_id == APPROVER
