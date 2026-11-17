"""QAService.verify: validated inputs, deterministic checks, pass/fail/inconclusive, nothing changed."""

import dataclasses

import pytest

from siamang.api.parallel import named_paths, with_planned_paths
from siamang.programmer import Patch, ProposedChange
from siamang.qa import QAPolicy, QAService, QAStatus
from siamang.qa.errors import QAError, QAErrorCode
from siamang.storage import Database
from siamang.tasks import TaskFailure, TaskResult
from tests.support.analyst import write
from tests.support.programmer import AUTH, AUTH_TEXT, change, make_setup, sha, tree
from tests.support.qa import codes, forge, propose, qa_request, verify, with_changes

C = QAErrorCode


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def patch(setup):
    return propose(setup)


@pytest.fixture
def other(tmp_path):
    database = Database(tmp_path / "other.db")
    (tmp_path / "o").mkdir()
    yield make_setup(database, tmp_path / "o")
    database.close()


def refused(setup, patch, **over):
    with pytest.raises(QAError) as info:
        verify(setup, patch, **over)
    return info.value.code


def state(setup):
    tid = setup.world.task.task_id
    return setup.world.engine.get(tid), len(setup.world.engine.history(tid)), tree(setup.root)


# --- pass ------------------------------------------------------------------------------


def test_genuine_patch_passes(setup, patch):
    before = state(setup)
    r = verify(setup, patch)
    assert r.status is QAStatus.PASS and r.passed and r.findings == ()
    assert r.qa_id.startswith("qa_") and r.patch_id == patch.patch_id and r.patch_hash == patch.content_hash
    files = {f["path"]: f for f in r.snapshot["files"]}
    assert files[AUTH]["recorded"] == files[AUTH]["current"] == "observed"
    assert files[AUTH]["current_hash"] == sha(AUTH_TEXT)
    assert files["backend/routes/validation.py"]["current"] == "absent"
    assert r.metadata["strategy"] == "qa.static" and r.metadata["changes_verified"] == 2
    assert r.metadata["plan_content_hash"] == setup.plan.content_hash
    assert state(setup) == before  # task, history, and every file unchanged
    assert "runtime behaviour (no code is executed)" in r.not_verified


def test_delete_passes(setup):
    p = propose(setup, [change("README.md", "delete", None)])
    r = verify(setup, p)
    assert r.status is QAStatus.PASS and (setup.root / "README.md").exists()


def test_deterministic_hash(setup, patch):
    a, b = verify(setup, patch), verify(setup, patch)
    assert a.qa_id != b.qa_id and a.content_hash == b.content_hash and a.content() == b.content()
    regenerated = propose(setup)  # same content, new patch_id: same QA content
    assert verify(setup, regenerated).content_hash == a.content_hash
    write(setup.root, {AUTH: "def login():\n    return 1\n"})
    assert verify(setup, patch).content_hash != a.content_hash


@pytest.mark.parametrize("status", ["running", "failed"])
def test_non_final_tasks_allowed(db, tmp_path, status):
    s = make_setup(db, tmp_path, start=True)
    p = propose(s)
    if status == "failed":
        s.world.engine.fail(s.world.task.task_id, TaskFailure(code="x", message="boom"))
    assert verify(s, p).status is QAStatus.PASS


# --- patch integrity ---------------------------------------------------------------------


def test_forged_patch_hash_fails(setup, patch):
    r = verify(setup, patch, "sha256:" + "0" * 64)
    assert r.status is QAStatus.FAIL and "patch.hash_mismatch" in codes(r)
    assert r.patch_hash == patch.content_hash  # the hash of what was actually supplied


def test_altered_content_fails_even_with_a_matching_recorded_hash(setup, patch):
    first = patch.changes[0]
    altered = with_changes(patch, (forge(first, after_content="import os\nos.system('id')\n"), *patch.changes[1:]))
    r = verify(setup, altered, altered.content_hash)  # the forger records the forged hash too
    assert r.status is QAStatus.FAIL and "patch.after_hash_mismatch" in codes(r)
    recorded = patch.content_hash  # what the Programmer produced
    consistent = forge(first, after_content="x = 2\n", after_hash=sha("x = 2\n"))  # internally consistent forgery
    relabelled = forge(first, step_id="step_01", reason="Re-labelled.")
    for forged_change in (consistent, relabelled):
        r = verify(setup, with_changes(patch, (forged_change, *patch.changes[1:])), recorded)
        assert r.status is QAStatus.FAIL and codes(r) == {"patch.hash_mismatch"}


@pytest.mark.parametrize("fields, code", [
    ({"operation": "rename"}, "patch.change_invalid"),
    ({"operation": "chmod"}, "patch.change_invalid"),
    ({"before_hash": "sha256:zz"}, "patch.change_invalid"),
    ({"before_hash": None}, "patch.change_invalid"),  # observed needs its hash
    ({"before": "absent"}, "patch.change_invalid"),  # modify of an "absent" file
    ({"after_content": "a\x00b"}, "patch.change_invalid"),
    ({"after_content": None}, "patch.change_invalid"),
    ({"after_content": 12}, "patch.change_invalid"),
    ({"step_id": "; rm -rf /"}, "patch.change_invalid"),
    ({"change_id": "evil"}, "patch.change_invalid"),
    ({"reason": ""}, "patch.change_invalid"),
    ({"metadata": {"command": "rm -rf /"}}, "patch.change_invalid"),
    ({"after_hash": sha("other")}, "patch.after_hash_mismatch"),
])
def test_malformed_changes_fail(setup, patch, fields, code):
    bad = forge(patch.changes[0], **fields)
    forged = with_changes(patch, (bad, *patch.changes[1:]))
    r = verify(setup, forged, "sha256:" + "1" * 64)
    assert r.status is QAStatus.FAIL and code in codes(r)


def test_create_with_observed_before_is_inconsistent(setup, patch):
    create = patch.changes[1]
    bad = forge(create, before="observed", before_hash=sha("x"))
    assert "patch.change_invalid" in codes(verify(setup, with_changes(patch, (patch.changes[0], bad))))


@pytest.mark.parametrize("second", [
    lambda c: forge(c, change_id="change_02"),  # same path twice (modify + modify)
    lambda c: forge(c, change_id="change_02", operation="delete", after_content=None, after_hash=None),
    lambda c: forge(c, change_id="change_02", path="Backend/routes/auth.py"),  # case-only duplicate
    lambda c: forge(c, change_id="change_02", path="backend/routes/auth.py/inner.py", operation="create",
                    before="absent", before_hash=None),  # file / folder collision
])
def test_conflicts_fail(setup, patch, second):
    first = patch.changes[0]
    r = verify(setup, with_changes(patch, (first, second(first))))
    assert r.status is QAStatus.FAIL and "patch.conflict" in codes(r)


def test_duplicate_change_ids_and_foreign_entries(setup, patch):
    r = verify(setup, with_changes(patch, (patch.changes[0], forge(patch.changes[1], change_id="change_01"))))
    assert "patch.conflict" in codes(r)
    r = verify(setup, with_changes(patch, (patch.changes[0], {"path": "x.py", "operation": "create"})),
               "sha256:" + "2" * 64)
    assert r.status is QAStatus.FAIL and codes(r) & {"patch.change_invalid", "patch.unhashable"}
    r = verify(setup, forge(patch, changes=[patch.changes[0]]), "sha256:" + "2" * 64)
    assert r.status is QAStatus.FAIL


def test_empty_patch_is_inconclusive(setup, patch):
    empty = Patch(patch.patch_id, patch.task_id, patch.project_id, "Nothing.", ())
    r = verify(setup, empty)
    assert r.status is QAStatus.INCONCLUSIVE and "patch.empty" in codes(r)


def test_malformed_summary_fails(setup, patch):
    r = verify(setup, forge(patch, summary=""), "sha256:" + "3" * 64)
    assert r.status is QAStatus.FAIL and "patch.conflict" in codes(r)


# --- project consistency -------------------------------------------------------------------


def test_project_changed_after_proposal(setup, patch):
    write(setup.root, {AUTH: "def login():\n    return None\n"})
    r = verify(setup, patch)
    assert r.status is QAStatus.FAIL and "project.before_hash_mismatch" in codes(r)
    assert setup.root.joinpath(AUTH).read_text() == "def login():\n    return None\n"


def test_target_missing_and_create_target_exists(setup, patch):
    (setup.root / AUTH).unlink()
    write(setup.root, {"backend/routes/validation.py": "exists\n"})
    r = verify(setup, patch)
    assert {"project.target_missing", "project.create_target_exists"} <= codes(r) and r.status is QAStatus.FAIL


def test_unverified_before_state_is_inconclusive(setup):
    write(setup.root, {AUTH: "x = 1\n" * 50_000})
    s_policy = dict(max_file_size=64 * 1024, max_context_file_size=1024)
    from siamang.programmer import ProgrammingPolicy
    from tests.support.programmer import make_model, reply

    res = setup.programmer(make_model(reply([change()])), policy=ProgrammingPolicy(**s_policy)).program(
        setup.request())
    p = res.patch
    assert p.changes[0].before.value == "unverified"
    r = verify(setup, p, policy=QAPolicy(max_file_size=64 * 1024))
    assert r.status is QAStatus.INCONCLUSIVE and "project.before_unverified" in codes(r)


def test_current_state_unreadable_is_inconclusive(setup, patch):
    write(setup.root, {AUTH: "y = 2\n" * 60_000})  # now larger than QA's read limit
    r = verify(setup, patch, policy=QAPolicy(max_file_size=64 * 1024))
    assert r.status is QAStatus.INCONCLUSIVE and "project.state_unverified" in codes(r)


# --- plan / analysis / scope -----------------------------------------------------------------


def test_unknown_plan_step_fails(setup, patch):
    step42 = ProposedChange("change_01", "backend/routes/new.py", "create", "step_42", "r", after_content="x\n",
                            before="absent")
    p = Patch(patch.patch_id, patch.task_id, patch.project_id, "s", (step42,))
    r = verify(setup, p)
    assert r.status is QAStatus.FAIL and "plan.unknown_step" in codes(r)
    assert [f.metadata["step_id"] for f in r.findings if f.code == "plan.unknown_step"] == ["step_42"]


def test_analysis_plan_mismatch_warns(setup, patch):
    from siamang.planner import Planner, PlanningRequest

    plan2 = Planner(setup.world.engine).plan(PlanningRequest(task_id=setup.world.task.task_id,
                                                             project_id=setup.world.project.project_id,
                                                             instruction="Something else"))
    r = verify(setup, patch, plan=plan2)
    assert "analysis.plan_mismatch" in codes(r) and r.status is QAStatus.PASS


def test_scope_outside_analysis_is_a_warning(setup):
    p = propose(setup, [change("docs/guide.md", "create", "# Guide\n"), change()])
    r = verify(setup, p)
    out = [f for f in r.findings if f.code == "scope.outside_analysis"]
    assert [f.path for f in out] == ["docs/guide.md"] and r.status is QAStatus.PASS  # reported, not blocking


def test_a_file_the_owner_named_is_in_scope_alone(setup):
    p = propose(setup, [change("data/publications.json", "create", "[]\n"), change("data/other.json", "create", "[]\n"), change()])
    analysis = with_planned_paths(setup.analysis, ["Simpan di `data/publications.json`, lihat https://example.com/x.json."])
    r = verify(setup, p, analysis=analysis)
    assert [f.path for f in r.findings if f.code == "scope.outside_analysis"] == ["data/other.json"]


def test_named_paths_reads_file_names_and_nothing_else():
    text = "Lihat `data/projects.json`, ./js/app.js dan index.html. Bukan https://a.io/b.html, a@b.com, atau v1.2."
    assert named_paths([text, "index.html lagi", None]) == ("data/projects.json", "js/app.js", "index.html")


def test_no_named_path_leaves_the_analysis_untouched(setup):
    assert with_planned_paths(setup.analysis, ["tanpa nama file"]) is setup.analysis


# --- input refusals -------------------------------------------------------------------------


def test_task_and_project_refusals(setup, patch):
    assert refused(setup, patch, task_id="tsk_01UNKNOWN") is C.TASK_NOT_FOUND
    other_project = setup.world.projects.register(name="Other", root_path="/work/other")
    assert refused(setup, patch, project_id=other_project.project_id) is C.REQUEST_INVALID
    setup.world.projects.archive(setup.world.project.project_id)
    assert refused(setup, patch) is C.PROJECT_ARCHIVED


@pytest.mark.parametrize("final", ["complete", "cancel"])
def test_final_tasks_refused(db, tmp_path, final):
    s = make_setup(db, tmp_path, start=True)
    p = propose(s)
    tid = s.world.task.task_id
    s.world.engine.complete(tid, TaskResult(summary="done")) if final == "complete" else s.world.engine.cancel(tid)
    assert refused(s, p) is C.POLICY_DENIED


def test_project_root_unavailable(setup, patch):
    for path in sorted(setup.root.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    setup.root.rmdir()
    assert refused(setup, patch) is C.PROJECT_UNAVAILABLE


def test_plan_mismatch_and_tampering(setup, patch, other):
    for plan in (dataclasses.replace(setup.plan, task_id=other.world.task.task_id),
                 dataclasses.replace(setup.plan, project_id=other.world.project.project_id), other.plan):
        assert refused(setup, patch, plan=plan) is C.PLAN_INVALID
    tampered = forge(setup.plan, steps=(*setup.plan.steps, setup.plan.steps[0]))
    assert refused(setup, patch, plan=tampered) is C.PLAN_INVALID


def test_analysis_mismatch_and_tampering(setup, patch, other):
    for analysis in (dataclasses.replace(setup.analysis, task_id=other.world.task.task_id), other.analysis):
        assert refused(setup, patch, analysis=analysis) is C.ANALYSIS_INVALID
    finding = forge(setup.analysis.findings[0], evidence=(), confidence="observed")
    assert refused(setup, patch, analysis=forge(setup.analysis, findings=(finding,))) is C.ANALYSIS_INVALID


def test_patch_identity_mismatch(setup, patch, other):
    other_patch = propose(other)
    assert refused(setup, other_patch) is C.PATCH_INVALID
    assert refused(setup, forge(patch, task_id=other.world.task.task_id)) is C.PATCH_INVALID
    assert refused(setup, forge(patch, project_id=other.world.project.project_id)) is C.PATCH_INVALID
    assert refused(setup, forge(patch, patch_id="../../etc")) is C.PATCH_INVALID


def test_constructor_and_request_guards(setup, patch):
    with pytest.raises(TypeError):
        QAService(object())
    with pytest.raises(TypeError):
        QAService(setup.world.engine, policy={})
    with pytest.raises(QAError):
        QAService(setup.world.engine).verify(qa_request(setup, patch).to_dict())


# --- policy ------------------------------------------------------------------------------------


def test_policy_limits(setup, patch):
    r = verify(setup, patch, policy=QAPolicy(max_changes=1))
    assert r.status is QAStatus.FAIL and "policy.too_many_changes" in codes(r)
    r = verify(setup, patch, policy=QAPolicy(max_file_size=16, max_total_bytes=64))
    assert "policy.file_too_large" in codes(r)
    r = verify(setup, patch, policy=QAPolicy(max_total_bytes=40, max_file_size=40))
    assert codes(r) & {"policy.total_too_large", "policy.file_too_large"}
    r = verify(setup, patch, policy=QAPolicy(max_path_length=10))
    assert "policy.path_too_long" in codes(r)


def test_findings_are_bounded_and_failure_survives(setup, patch):
    many = tuple(forge(patch.changes[0], change_id=f"change_{i:02d}", operation="rename") for i in range(1, 20))
    forged = with_changes(patch, many)
    r = verify(setup, forged, "sha256:" + "4" * 64, policy=QAPolicy(max_findings=5))
    assert len(r.findings) == 5 and r.findings[-1].code == "qa.findings_truncated" and r.status is QAStatus.FAIL
    r = verify(setup, forged, "sha256:" + "4" * 64, policy=QAPolicy(max_findings=1))
    assert len(r.findings) == 1 and r.status is QAStatus.FAIL
