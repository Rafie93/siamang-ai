"""Phase 19.5: immutability, determinism, and secret / hostile input across the whole chain."""

import dataclasses
import itertools
import json
import logging
import os

import pytest

from siamang import ids
from siamang.approval import ApprovalPolicy
from siamang.approval.errors import ApprovalError
from siamang.loop import LoopPolicy, LoopRequest
from siamang.programmer.safety import thaw
from siamang.storage import Database
from tests.support.analyst import SECRET, sample_project
from tests.support.approval import (
    CLEAN,
    approval_request,
    approve,
    fabricate_patch,
    loop_result,
    reject,
    service,
    unrelated,
)
from tests.support.chain import build, files
from tests.support.programmer import INSTRUCTION, change, make_setup, reply, sha
from tests.support.qa import forge


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


# --- 18. immutability -----------------------------------------------------------------------------------


def _artifacts(s):
    req = approval_request(s)
    loop = req.loop_result
    result = service(s).decide(req, approve())
    warned = loop_result(s, unrelated(1), policy=LoopPolicy(retry_statuses=set())).final_qa_result  # has findings
    return {
        "task": s.world.task, "plan": s.plan, "plan_step": s.plan.steps[0], "analysis": s.analysis,
        "patch": loop.final_patch, "change": loop.final_patch.changes[0], "qa": loop.final_qa_result,
        "qa_finding": warned.findings[0], "review": loop.final_review_result,
        "review_finding": loop.final_review_result.findings[0], "loop": loop, "iteration": loop.iterations[0],
        "approval_request": req, "approval_decision": approve(), "approval_policy": ApprovalPolicy(
            authorized_approvers={"rafie"}), "approval_provenance": result.provenance, "approval_result": result,
    }


ARTIFACTS = ["task", "plan", "plan_step", "analysis", "patch", "change", "qa", "qa_finding", "review",
             "review_finding", "loop", "iteration", "approval_request", "approval_decision", "approval_policy",
             "approval_provenance", "approval_result"]


@pytest.mark.parametrize("name", ARTIFACTS)
def test_every_artifact_is_frozen(setup, name):
    obj = _artifacts(setup)[name]
    assert dataclasses.is_dataclass(obj)
    for f in dataclasses.fields(obj):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, f.name, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        obj.injected = True


def _mutable(value, path="", seen=None):
    """Every reachable list, dict, set, or bytearray inside an artifact (none may exist)."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return []
    seen.add(id(value))
    if isinstance(value, (list, dict, set, bytearray)):
        return [path or "<root>"]
    out = []
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            out += _mutable(value.__dict__[f.name], f"{path}.{f.name}", seen)
    elif isinstance(value, (tuple, frozenset)):
        for i, item in enumerate(value):
            out += _mutable(item, f"{path}[{i}]", seen)
    elif hasattr(value, "items") and type(value).__name__ == "mappingproxy":
        for k, v in value.items():
            out += _mutable(v, f"{path}[{k!r}]", seen)
    return out


# Phase 20 closes M-1: no reachable collection may remain mutable.

@pytest.mark.parametrize("name", ARTIFACTS)
def test_no_mutable_collection_is_reachable(setup, name):
    assert set(_mutable(_artifacts(setup)[name])) == set()


def test_shallow_analysis_metadata_mutation_is_caught_everywhere(setup):
    req = approval_request(setup)
    svc = service(setup)
    result = svc.decide(req, approve())
    before = setup.analysis.content_hash
    with pytest.raises(AttributeError):
        setup.analysis.metadata["match_terms"].append("injected")
    assert setup.analysis.content_hash == before
    from siamang.jsondata import thaw, freeze
    altered = thaw(setup.analysis.metadata)
    altered["match_terms"].append("injected")
    object.__setattr__(setup.analysis, "metadata", freeze(altered))  # hostile in-process tampering remains detected
    assert setup.analysis.content_hash != before
    for attempt in (lambda: svc.verify(result, req), lambda: svc.decide(req, approve()),
                    lambda: svc.decide(req, reject()), lambda: approval_request(setup, req.loop_result)):
        with pytest.raises(ApprovalError) as info:
            attempt()
        assert info.value.code.value == "approval_provenance_invalid"


@pytest.mark.parametrize("name", ["qa", "review", "loop", "approval_result"])
def test_nested_mappings_are_read_only(setup, name):
    obj = _artifacts(setup)[name]
    mappings = [obj.__dict__[f.name] for f in dataclasses.fields(obj)
                if type(obj.__dict__[f.name]).__name__ == "mappingproxy"]
    assert mappings
    for m in mappings:
        with pytest.raises(TypeError):
            m["status"] = "pass"


def test_caller_owned_input_is_copied_before_validation(setup):
    caller_input, caller_meta = {"ticket": ["A-1"]}, {"origin": {"who": "test"}}
    req = LoopRequest(setup.world.task.task_id, setup.world.project.project_id, INSTRUCTION, setup.plan,
                      setup.analysis, input=caller_input, metadata=caller_meta)
    caller_input["ticket"].append("evil")
    caller_input["override"] = True
    caller_meta["origin"]["who"] = "admin"
    assert thaw(req.input) == {"ticket": ["A-1"]}
    assert req.metadata["origin"]["who"] == "test"
    approvers = {"rafie"}
    policy = ApprovalPolicy(authorized_approvers=approvers)
    approvers.add("mallory")
    assert not policy.authorizes("mallory")


def test_upstream_mutation_after_approval_never_changes_the_result(setup):
    req = approval_request(setup)
    svc = service(setup)
    result = svc.decide(req, approve())
    snapshot = json.dumps(result.to_dict(), sort_keys=True)
    loop = req.loop_result
    object.__setattr__(loop.final_patch, "summary", "Mutated.")
    object.__setattr__(loop.final_qa_result, "findings", list(loop.final_qa_result.findings))
    object.__setattr__(req.plan, "objective", "Something else entirely.")
    assert json.dumps(result.to_dict(), sort_keys=True) == snapshot
    with pytest.raises(ApprovalError):
        svc.verify(result, req)


@pytest.mark.parametrize("target", ["final_patch", "final_qa_result", "final_review_result"])
def test_mutable_state_forced_in_before_validation_is_refused(setup, target):
    loop = loop_result(setup, CLEAN)
    artifact = loop.__dict__[target]
    if target == "final_patch":
        bad = forge(artifact, changes=list(artifact.changes))
    else:
        bad = forge(artifact, metadata=dict(artifact.metadata))  # same content, but mutable
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, forge(loop, **{target: bad}))
    assert info.value.code.value == "approval_provenance_invalid"


# --- 19. determinism -------------------------------------------------------------------------------------


def test_same_inputs_give_the_same_semantics(setup):
    svc = service(setup)
    results = [svc.decide(approval_request(setup, loop_result(setup, CLEAN)), approve()) for _ in range(5)]
    loops = [loop_result(setup, CLEAN) for _ in range(5)]
    assert len({lp.final_patch.content_hash for lp in loops}) == 1
    assert len({lp.final_qa_result.content_hash for lp in loops}) == 1
    assert len({lp.final_review_result.content_hash for lp in loops}) == 1
    assert len({lp.content_hash for lp in loops}) == 1
    assert {json.dumps(dict(r.evidence), sort_keys=True) for r in results} == {json.dumps(
        {"loop_status": "pass", "loop_stop_reason": "success", "qa_status": "pass", "review_status": "pass"},
        sort_keys=True)}
    assert len({r.approval_id for r in results}) == 5  # identity is generated; semantics are not


def test_same_decision_on_the_same_chain_has_one_hash(setup):
    req = approval_request(setup)
    svc = service(setup)
    a, b = svc.decide(req, approve()), svc.decide(req, approve())
    assert a.approval_hash == b.approval_hash and a.approval_id != b.approval_id and a.seal != b.seal
    svc.verify(a, req)
    svc.verify(b, req)


def test_two_independent_worlds_are_byte_identical(tmp_path, monkeypatch):
    """With identity generation fixed, every stage (and the sealed result) is reproduced exactly: nothing random
    or time-based enters a hash or the verification."""
    outputs = []
    for name in ("one", "two"):
        counter = itertools.count(1)
        monkeypatch.setattr(ids, "ulid", lambda now_ms=None, rand=None: f"{next(counter):026d}")
        base = tmp_path / name
        base.mkdir()
        database = Database(base / "s.db")
        c = build(database, sample_project(base / "banuva"), CLEAN)
        result = c.service.decide(c.request, approve())
        c.service.verify(result, c.request)
        outputs.append(json.dumps([c.plan.to_dict(), c.analysis.to_dict(), c.loop.to_dict(), result.to_dict()],
                                  sort_keys=True))
        database.close()
    assert outputs[0] == outputs[1]


def test_verification_does_not_depend_on_generated_identity(setup, monkeypatch):
    req = approval_request(setup)
    svc = service(setup)
    result = svc.decide(req, approve())
    monkeypatch.setattr(ids, "ulid", lambda now_ms=None, rand=None: (_ for _ in ()).throw(AssertionError("id")))
    monkeypatch.setattr(os, "urandom", lambda n: (_ for _ in ()).throw(AssertionError("random")))
    svc.verify(result, req)  # no ID, randomness, or clock is consulted to verify


def test_no_timestamp_is_evidence(setup):
    result = service(setup).decide(approval_request(setup), approve())
    text = json.dumps(result.to_dict())
    for word in ("time", "date", "created", "expires", "_at"):
        assert word not in text, word


# --- 20. secrets and hostile input -------------------------------------------------------------------------

HOSTILE_PATHS = [".env", ".env.local", ".env.production", ".ssh/id_rsa", ".git/config", ".git/hooks/pre-commit",
                 "/etc/passwd", "~/x.py", "../x.py", "backend/../../x.py", "a\\..\\..\\b.py", "C:\\x.py",
                 "c:/x.py", "\\\\srv\\share\\x.py", "//srv/share/x.py", "a\x00b.py", "a\x1b[31m.py", "a\nb.py",
                 "certs/server.pem", "config/secrets.yaml", "id_rsa", "credentials.json"]
HOSTILE_CONTENT = [f"API_KEY = '{SECRET}'\n", f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n",
                   f"password = '{SECRET}'\n", "token = 'ghp_" + "a" * 36 + "'\n",
                   f"aws_secret_access_key = {SECRET}\n"]


def _leaks(text, root):
    return [x for x in (SECRET, "ghp_" + "a" * 36, str(root), "\x1b[31m") if x in text]


@pytest.mark.parametrize("path", HOSTILE_PATHS)
def test_hostile_path_never_becomes_an_approvable_patch(db, tmp_path, caplog, path):
    caplog.set_level(logging.DEBUG, logger="siamang")
    root = sample_project(tmp_path / "banuva")
    before = files(root)
    s = make_setup(db, tmp_path, root=root)
    loop = loop_result(s, reply([change(path, "create", "x = 1\n")]), policy=LoopPolicy(retry_statuses=set()))
    assert loop.final_patch is None and loop.stop_reason.value == "programmer_failed"
    with pytest.raises(ApprovalError):
        approval_request(s, loop)
    assert not _leaks(json.dumps(loop.to_dict()) + caplog.text, root)
    assert files(root) == before


@pytest.mark.parametrize("content", HOSTILE_CONTENT, ids=range(len(HOSTILE_CONTENT)))
def test_secret_content_never_becomes_an_approvable_patch(setup, caplog, content):
    caplog.set_level(logging.DEBUG, logger="siamang")
    loop = loop_result(setup, reply([change("backend/routes/validation.py", "create", content)]),
                       policy=LoopPolicy(retry_statuses=set()))
    assert loop.final_patch is None and loop.stop_reason.value == "programmer_failed"
    assert not _leaks(json.dumps(loop.to_dict()) + caplog.text, setup.root)


def test_case_only_collision_is_refused(setup):
    loop = loop_result(setup, reply([change("backend/Valid.py", "create", "x = 1\n"),
                                     change("backend/valid.py", "create", "x = 2\n")]),
                       policy=LoopPolicy(retry_statuses=set()))
    assert loop.final_patch is None


@pytest.mark.parametrize("kind", ["symlink_out", "symlink_secret", "fifo"])
@pytest.mark.parametrize("operation", ["modify", "delete", "create"])
def test_symlinks_and_special_files_are_refused(db, tmp_path, kind, operation):
    root = sample_project(tmp_path / "banuva")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE\n")
    target = root / "backend" / "special.py"
    if kind == "symlink_out":
        os.symlink(outside, target)
    elif kind == "symlink_secret":
        os.symlink(root / ".env", target)
    else:
        os.mkfifo(target)
    before = files(root)
    s = make_setup(db, tmp_path, root=root)
    content = None if operation == "delete" else "x = 1\n"
    loop = loop_result(s, reply([change("backend/special.py", operation, content)]),
                       policy=LoopPolicy(retry_statuses=set()))
    assert loop.final_patch is None
    assert files(root) == before and outside.read_text() == "OUTSIDE\n"


@pytest.mark.parametrize("field", [{"path": ".env"}, {"path": ".ssh/id_rsa"}, {"path": ".git/config"},
                                   {"path": "../../x.py"}, {"path": "C:\\x.py"}, {"after_content": f"k='{SECRET}'\n"}])
def test_fully_rebound_hostile_patch_is_refused_without_echo(setup, caplog, field):
    caplog.set_level(logging.DEBUG, logger="siamang")
    loop = loop_result(setup, CLEAN)
    patch = loop.final_patch
    extra = {"after_hash": sha(field["after_content"])} if "after_content" in field else {}
    bad = forge(patch, changes=(forge(patch.changes[0], **field, **extra), *patch.changes[1:]))
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, fabricate_patch(loop, bad))
    text = json.dumps(info.value.to_dict()) + str(info.value) + repr(info.value) + caplog.text
    assert info.value.code.value == "approval_provenance_invalid"
    assert SECRET not in text and next(iter(field.values())) not in text


@pytest.mark.parametrize("reason", [f"key {SECRET}", "see .env.production", "see /home/u/.ssh/id_rsa",
                                    "token=ghp_" + "a" * 36, "C:\\secrets\\x", "multi\nline", "esc\x1b[31m"])
def test_hostile_reason_is_refused_and_never_echoed(setup, caplog, reason):
    caplog.set_level(logging.DEBUG, logger="siamang")
    from siamang.approval import ApprovalDecision

    try:
        decision = ApprovalDecision("reject", "rafie", reason)
    except ApprovalError as exc:
        text = json.dumps(exc.to_dict()) + str(exc) + caplog.text
    else:  # plain text that names a file is a note, not a secret: it may be recorded, never echoed in errors
        assert ".env" in reason
        result = service(setup).decide(approval_request(setup), decision)
        text = caplog.text
        assert result.reason == reason
    assert reason not in text and SECRET not in text


def test_hashes_and_results_carry_no_secret_material(setup):
    req = approval_request(setup)
    results = [service(setup).decide(req, approve()), service(setup).decide(req, reject())]
    text = json.dumps([r.to_dict() for r in results]) + json.dumps(req.to_dict())
    assert SECRET not in text and "def valid" not in text and str(setup.root) not in text
