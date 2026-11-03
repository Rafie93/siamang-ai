"""ReviewerService.review: QA as evidence, seven review dimensions, statuses, identity refusals, nothing changed."""

import copy
import dataclasses

import pytest

from siamang.programmer import Patch
from siamang.reviewer import ReviewerPolicy, ReviewerService, ReviewStatus
from siamang.reviewer.errors import ReviewerError, ReviewerErrorCode
from siamang.reviewer.service import QA_STRATEGY
from siamang.storage import Database
from siamang.tasks import TaskResult
from tests.support.analyst import write
from tests.support.programmer import AUTH, make_setup, tree
from tests.support.qa import forge, verify
from tests.support.reviewer import (
    TEST_VALIDATION,
    VALIDATION,
    chain,
    clean_changes,
    codes,
    create,
    delete,
    direct,
    judged,
    modify,
    review,
    review_request,
    with_known_paths,
)

C = ReviewerErrorCode
S = ReviewStatus


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def clean(setup):
    return chain(setup, clean_changes())


@pytest.fixture
def other(tmp_path):
    database = Database(tmp_path / "other.db")
    (tmp_path / "o").mkdir()
    yield make_setup(database, tmp_path / "o")
    database.close()


def refused(setup, patch, qa, **over):
    with pytest.raises(ReviewerError) as info:
        review(setup, patch, qa, **over)
    return info.value.code


def found(result, code):
    return [f for f in result.findings if f.code == code]


# --- statuses and QA evidence ----------------------------------------------------------------


def test_clean_change_passes(setup, clean):
    patch, qa = clean
    r = review(setup, patch, qa)
    assert qa.status.value == "pass" and r.status is S.PASS and r.qa_status.value == "pass"
    assert codes(r) == {"qa.passed"} and not r.errors and not r.warnings
    assert (r.task_id, r.project_id, r.plan_id, r.analysis_id) == (
        setup.world.task.task_id, setup.world.project.project_id, setup.plan.plan_id, setup.analysis.analysis_id)
    assert (r.patch_id, r.patch_hash, r.qa_id, r.qa_hash) == (patch.patch_id, patch.content_hash, qa.qa_id,
                                                             qa.content_hash)
    assert r.review_id.startswith("rev_") and "approval" in r.not_verified and "runtime_behavior" in r.not_verified
    assert r.metadata["strategy"] == "review.static" and r.metadata["changes_reviewed"] == 2
    assert dict(r.metadata["operations"]) == {"create": 2, "modify": 0, "delete": 0}
    assert r.metadata["plan_content_hash"] == setup.plan.content_hash


def test_qa_pass_is_not_review_pass(setup):
    patch, qa = chain(setup)  # the genuine default proposal: modifies auth.py, adds no tests
    r = review(setup, patch, qa)
    assert qa.status.value == "pass" and r.status is S.CONCERN
    assert {"security.sensitive_area", "feasibility.tests_absent"} <= codes(r) and not r.errors


def test_qa_fail_makes_the_review_fail(setup):
    patch, _ = chain(setup)
    write(setup.root, {AUTH: "def login():\n    return None\n"})  # the project drifted after the proposal
    qa = verify(setup, patch)
    assert qa.status.value == "fail"
    r = review(setup, patch, qa)
    assert r.status is S.FAIL and r.qa_status.value == "fail"
    [failed] = found(r, "qa.failed")
    assert failed.blocking and "project.before_hash_mismatch" in failed.metadata["qa_codes"]
    assert r.findings[0].code == "qa.failed"  # errors first


def test_qa_fail_with_an_otherwise_clean_change_still_fails(setup):
    patch, _ = chain(setup, clean_changes())
    write(setup.root, {VALIDATION: "exists = True\n"})  # the create target now exists
    r = review(setup, patch, verify(setup, patch))
    assert r.status is S.FAIL and codes(r) == {"qa.failed"}


def test_qa_inconclusive_keeps_the_review_inconclusive(setup):
    patch, _ = chain(setup)
    empty = Patch(patch.patch_id, patch.task_id, patch.project_id, "Nothing.", ())
    qa = verify(setup, empty)
    assert qa.status.value == "inconclusive"
    r = review(setup, empty, qa)
    assert r.status is S.INCONCLUSIVE and {"qa.inconclusive", "feasibility.empty_patch"} <= codes(r)
    assert all(f.inconclusive for f in r.findings if f.code in ("qa.inconclusive", "feasibility.empty_patch"))


def test_qa_inconclusive_with_a_blocking_concern_fails(setup):
    s = setup
    write(s.root, {"scripts/banana.py": "x = 1\n"})
    spec = dict(path="scripts/banana.py", operation="modify", step_id="step_03", reason="r", after_content="x = 2\n",
                before="unverified")  # the proposal never confirmed what it modifies
    patch = direct(s, spec)
    qa = verify(s, patch)
    assert qa.status.value == "inconclusive"
    r = review(s, patch, qa)
    assert r.status is S.FAIL and {"qa.inconclusive", "alignment.unrelated_patch"} <= codes(r)


def test_open_qa_warnings_remain_concerns(setup):
    r = judged(setup, create("scripts/login_check.py"), create("tests/test_login_check.py"))
    assert r.qa_status.value == "pass"  # QA only warned (scope.outside_analysis)
    [w] = found(r, "qa.warnings_open")
    assert "scope.outside_analysis" in w.metadata["qa_codes"] and r.status is S.CONCERN
    assert not found(r, "alignment.unrelated_change")  # "login" relates the path to the task by name


# --- dimensions ---------------------------------------------------------------------------


def test_alignment_unrelated_change_and_patch(setup):
    r = judged(setup, *[create(c["path"], c["content"]) for c in clean_changes()], create("scripts/banana.py"))
    assert [f.path for f in found(r, "alignment.unrelated_change")] == ["scripts/banana.py"]
    assert r.status is S.CONCERN
    r = judged(setup, create("scripts/banana.py"), create("scripts/mango.py"))
    assert r.qa_status.value == "pass" and r.status is S.FAIL  # QA passed, but nothing relates to the task
    assert found(r, "alignment.unrelated_patch")[0].blocking and len(found(r, "alignment.unrelated_change")) == 2


def test_alignment_matches_word_stems(setup):
    r = judged(setup, create("lib/validators/login_validator.py"), create("tests/test_login_validator.py"))
    assert not found(r, "alignment.unrelated_change")


def test_scope(setup):
    specs = [create(c["path"], c["content"]) for c in clean_changes()]
    r = judged(setup, *specs, policy=ReviewerPolicy(broad_change_count=1, max_directories=1))
    assert {"scope.broad_change", "scope.directory_spread"} <= codes(r) and r.status is S.CONCERN
    assert not {"scope.broad_change", "scope.directory_spread"} & codes(judged(setup, *specs))


def test_a_long_file_is_only_a_finding_when_the_patch_creates_it(setup):
    long = "".join(f"x{i} = {i}\n" for i in range(50))
    r = judged(setup, modify(setup, AUTH, long), policy=ReviewerPolicy(max_file_lines=10))
    assert found(r, "maintainability.large_file") == []


def test_maintainability(setup):
    long = "".join(f"x{i} = {i}\n" for i in range(50))
    r = judged(setup, create(VALIDATION, long), create(TEST_VALIDATION, "def test_x():\n    assert True\n"),
               create("backend/routes/validation_copy.py", long),
               policy=ReviewerPolicy(max_file_lines=10, max_new_files=2))
    assert {f.path for f in found(r, "maintainability.large_file")} == {VALIDATION, "backend/routes/validation_copy.py"}
    assert found(r, "maintainability.large_file")[0].metadata["lines"] == 50
    assert [f.path for f in found(r, "maintainability.duplicate_content")] == ["backend/routes/validation_copy.py"]
    assert found(r, "maintainability.many_new_files") and r.status is S.CONCERN


def test_consistency(setup):
    s = with_known_paths(setup, "backend/routes/user_profile.py", "backend/routes/order_history.py")
    r = judged(s, create("backend/routes/loginValidator.py"), create("backend/routes/login_check.rb"),
               create("tests/test_login.py"))
    assert [f.path for f in found(r, "consistency.naming_style")] == ["backend/routes/loginValidator.py"]
    assert [f.path for f in found(r, "consistency.unfamiliar_file_type")] == ["backend/routes/login_check.rb"]
    r = judged(s, create("backend/routes/login_validator.py"), create("tests/test_login.py"))
    assert not codes(r) & {"consistency.naming_style", "consistency.unfamiliar_file_type"}


def test_risk(setup):
    r = judged(setup, delete(setup, "backend/models/user.py"),
               modify(setup, "backend/pyproject.toml", '[project]\nname = "banuva"\ndependencies = ["fastapi"]\n'),
               modify(setup, "Dockerfile", "FROM python:3.12\n"), create("backend/routes/__init__.py", "\"\"\"x\"\"\"\n"),
               modify(setup, AUTH, "def login(user):\n    return bool(user)\n"),
               create("tests/test_auth_login.py", "def test_login():\n    assert True\n"))
    by_code = {code: {f.path for f in found(r, code)} for code in codes(r)}
    assert by_code["risk.deletion"] == {"backend/models/user.py"}
    assert by_code["risk.dependency_change"] == {"backend/pyproject.toml"}
    assert by_code["risk.configuration_change"] == {"Dockerfile"}
    assert by_code["risk.public_interface"] == {"backend/routes/__init__.py"}
    assert by_code["security.sensitive_area"] == {AUTH, "tests/test_auth_login.py"}
    assert all(f.severity.value == "warning" for f in r.findings if f.category.value in ("risk", "security"))
    assert r.status is S.CONCERN  # risks are concerns, never a verdict by themselves


def test_feasibility(setup):
    r = judged(setup, create(VALIDATION, step="step_01"), create("backend/routes/validation_notes.py", "\n"))
    assert [f.path for f in found(r, "feasibility.step_role")] == [VALIDATION]
    assert [f.path for f in found(r, "feasibility.empty_file")] == ["backend/routes/validation_notes.py"]
    assert found(r, "feasibility.tests_absent") and r.status is S.CONCERN
    docs = judged(setup, create("docs/validation.md", "# Validation\n"))
    assert not found(docs, "feasibility.tests_absent")  # no code changed


def test_findings_are_bounded_and_failure_survives(setup):
    many = [create(f"scripts/banana_{i}.py", f"x = {i}\n") for i in range(12)]
    r = judged(setup, *many, policy=ReviewerPolicy(max_findings=5))
    assert len(r.findings) == 5 and r.findings[-1].code == "review.findings_truncated"
    assert r.findings[0].code == "alignment.unrelated_patch" and r.status is S.FAIL
    r = judged(setup, *many, policy=ReviewerPolicy(max_findings=1))
    assert [f.code for f in r.findings] == ["alignment.unrelated_patch"] and r.status is S.FAIL
    r = judged(setup, *[create(f"backend/routes/validation_{i}.py", f"x = {i}\n") for i in range(12)],
               policy=ReviewerPolicy(max_findings=3, max_changes=20))
    assert r.status is S.INCONCLUSIVE and r.findings[-1].code == "review.findings_truncated"


# --- determinism and immutability ------------------------------------------------------------


def test_deterministic(setup, clean):
    patch, qa = clean
    a, b = review(setup, patch, qa), review(setup, patch, qa)
    assert a.review_id != b.review_id and a.content_hash == b.content_hash and a.content() == b.content()
    again, qa2 = chain(setup, clean_changes())  # a regenerated, identical proposal and its new QA run
    c = review(setup, again, qa2)
    assert again.patch_id != patch.patch_id and c.content_hash == a.content_hash


def _snapshot(*objects):
    return [copy.deepcopy(o.to_dict()) for o in objects]


def test_inputs_are_never_mutated(setup):
    patch, qa = chain(setup)
    task = setup.world.engine.get(setup.world.task.task_id)
    history = len(setup.world.engine.history(task.task_id))
    project = setup.world.projects.get(setup.world.project.project_id)
    before = _snapshot(setup.plan, setup.analysis, patch, qa)
    hashes = (setup.plan.content_hash, setup.analysis.content_hash, patch.content_hash, qa.content_hash)
    files = tree(setup.root)
    review(setup, patch, qa)
    assert _snapshot(setup.plan, setup.analysis, patch, qa) == before
    assert (setup.plan.content_hash, setup.analysis.content_hash, patch.content_hash, qa.content_hash) == hashes
    assert setup.world.engine.get(task.task_id) == task and len(setup.world.engine.history(task.task_id)) == history
    assert setup.world.projects.get(project.project_id) == project and tree(setup.root) == files


def test_caller_metadata_mutation_does_not_change_the_result(setup, clean):
    meta = {"ticket": {"tags": ["a"]}}
    r = review(setup, *clean, metadata=meta)
    snapshot = r.to_dict()
    meta["ticket"]["tags"].append("b")
    assert r.to_dict() == snapshot


@pytest.mark.parametrize("status", ["queued", "running"])
def test_non_final_tasks_allowed(db, tmp_path, status):
    s = make_setup(db, tmp_path, start=status == "running")
    patch, qa = chain(s, clean_changes())
    assert review(s, patch, qa).status is S.PASS


# --- input refusals: identity -----------------------------------------------------------------


def test_task_and_project_refusals(setup, clean):
    patch, qa = clean
    assert refused(setup, patch, qa, task_id="tsk_01UNKNOWN") is C.TASK_NOT_FOUND
    other_project = setup.world.projects.register(name="Other", root_path="/work/other")
    assert refused(setup, patch, qa, project_id=other_project.project_id) is C.REQUEST_INVALID
    setup.world.projects.archive(setup.world.project.project_id)
    assert refused(setup, patch, qa) is C.PROJECT_ARCHIVED


@pytest.mark.parametrize("final", ["complete", "cancel"])
def test_final_tasks_refused(db, tmp_path, final):
    s = make_setup(db, tmp_path, start=True)
    patch, qa = chain(s, clean_changes())
    tid = s.world.task.task_id
    s.world.engine.complete(tid, TaskResult(summary="done")) if final == "complete" else s.world.engine.cancel(tid)
    assert refused(s, patch, qa) is C.POLICY_DENIED


def test_plan_mismatch_and_tampering(setup, clean, other):
    patch, qa = clean
    for plan in (dataclasses.replace(setup.plan, task_id=other.world.task.task_id),
                 dataclasses.replace(setup.plan, project_id=other.world.project.project_id), other.plan):
        assert refused(setup, patch, qa, plan=plan) is C.PLAN_INVALID
    tampered = forge(setup.plan, steps=(*setup.plan.steps, setup.plan.steps[0]))
    assert refused(setup, patch, qa, plan=tampered) is C.PLAN_INVALID


def test_analysis_mismatch_and_tampering(setup, clean, other):
    patch, qa = clean
    for analysis in (dataclasses.replace(setup.analysis, task_id=other.world.task.task_id), other.analysis):
        assert refused(setup, patch, qa, analysis=analysis) is C.ANALYSIS_INVALID
    finding = forge(setup.analysis.findings[0], evidence=(), confidence="observed")
    assert refused(setup, patch, qa, analysis=forge(setup.analysis, findings=(finding,))) is C.ANALYSIS_INVALID


def test_plan_or_analysis_other_than_the_one_qa_verified(setup, clean):
    patch, qa = clean
    plan = dataclasses.replace(setup.plan, objective="Something else entirely")
    assert refused(setup, patch, qa, plan=plan) is C.QA_INVALID
    analysis = dataclasses.replace(setup.analysis, summary="A different analysis.")
    assert refused(setup, patch, qa, analysis=analysis) is C.QA_INVALID


def test_patch_identity_mismatch(setup, clean, other):
    patch, qa = clean
    other_patch, other_qa = chain(other, clean_changes())
    assert refused(setup, other_patch, qa) is C.PATCH_INVALID
    assert refused(setup, forge(patch, task_id=other.world.task.task_id), qa) is C.PATCH_INVALID
    assert refused(setup, forge(patch, project_id=other.world.project.project_id), qa) is C.PATCH_INVALID
    assert refused(setup, forge(patch, patch_id="../../etc"), qa) is C.PATCH_INVALID


def test_patch_hash_mismatch(setup, clean):
    patch, qa = clean
    assert refused(setup, patch, qa, patch_hash="sha256:" + "0" * 64) is C.PATCH_INVALID
    altered = forge(patch, summary="Altered after QA.")  # recorded hash no longer matches the content
    assert refused(setup, altered, qa) is not None
    assert refused(setup, altered, qa, patch_hash=patch.content_hash) is C.PATCH_INVALID


def test_self_consistent_patch_forgery_is_caught_by_qa_binding(setup, clean):
    patch, qa = clean
    swapped = forge(patch, summary="A forged summary.")  # caller recomputes the hash itself
    assert refused(setup, swapped, qa, patch_hash=swapped.content_hash) is C.QA_INVALID


def test_qa_result_mismatch(setup, clean, other):
    patch, qa = clean
    assert refused(setup, patch, qa, qa_hash="sha256:" + "0" * 64) is C.QA_INVALID
    _, other_qa = chain(other, clean_changes())
    assert refused(setup, patch, other_qa) is C.QA_INVALID
    for fields in ({"task_id": other.world.task.task_id}, {"project_id": other.world.project.project_id},
                   {"patch_id": "pch_01OTHER"}, {"patch_hash": "sha256:" + "9" * 64}, {"qa_id": "qa_../../x"}):
        forged = dataclasses.replace(qa, **fields) if "qa_id" not in fields else forge(qa, **fields)
        assert refused(setup, patch, forged, qa_hash=forged.content_hash) is C.QA_INVALID, fields
    second, second_qa = chain(setup, [*clean_changes(), {"path": "backend/routes/extra_validation.py",
                                                          "operation": "create", "step_id": "step_03",
                                                          "reason": "More validation.",
                                                          "content": "def extra():\n    return 1\n"}])
    assert refused(setup, patch, second_qa) is C.QA_INVALID  # QA of a different patch


def test_qa_status_tampering(setup, clean):
    patch, _ = clean
    write(setup.root, {VALIDATION: "exists = True\n"})
    failed = verify(setup, patch)
    lie = forge(failed, status="pass")  # status forced past validation; findings still say fail
    assert refused(setup, patch, lie, qa_hash=failed.content_hash) is C.QA_INVALID
    assert refused(setup, patch, lie, qa_hash=lie.content_hash) is C.QA_INVALID
    scrubbed = forge(failed, findings=(), status="pass")  # a consistent forgery: caught by the recorded hash
    assert refused(setup, patch, scrubbed, qa_hash=failed.content_hash) is C.QA_INVALID


def test_qa_metadata_and_snapshot_tampering(setup, clean):
    patch, qa = clean
    for meta in ({**qa.metadata, "strategy": "qa.lenient"}, {**qa.metadata, "changes_in_patch": 1}):
        forged = dataclasses.replace(qa, metadata=meta)
        assert refused(setup, patch, forged, qa_hash=forged.content_hash) is C.QA_INVALID
    entry = {**qa.snapshot["files"][0], "path": "backend/routes/other.py"}
    forged = dataclasses.replace(qa, snapshot={**qa.snapshot, "files": [entry]})
    assert refused(setup, patch, forged, qa_hash=forged.content_hash) is C.QA_INVALID
    assert QA_STRATEGY == "qa.static"


def test_mutable_state_forced_into_artifacts_is_tampering(setup, clean):
    patch, qa = clean
    mutable_meta = {k: v for k, v in qa.metadata.items()}  # plain dict forced in place of the frozen mapping
    forged_qa = forge(qa, metadata=mutable_meta)
    assert refused(setup, patch, forged_qa, qa_hash=qa.content_hash) is C.QA_INVALID
    change = forge(patch.changes[0], metadata={"a": [1]})
    forged_patch = forge(patch, changes=(change, patch.changes[1]))
    assert refused(setup, forged_patch, qa, patch_hash=forged_patch.content_hash) is C.PATCH_INVALID


def test_constructor_and_request_guards(setup, clean):
    with pytest.raises(TypeError):
        ReviewerService(object())
    with pytest.raises(TypeError):
        ReviewerService(setup.world.engine, policy={})
    with pytest.raises(ReviewerError) as info:
        ReviewerService(setup.world.engine).review(review_request(setup, *clean).to_dict())
    assert info.value.code is C.REQUEST_INVALID


# --- policy bounds (refusals, never partial reviews) ------------------------------------------


def test_policy_bounds_are_refusals(setup, clean):
    patch, qa = clean
    assert refused(setup, patch, qa, policy=ReviewerPolicy(max_changes=1)) is C.POLICY_DENIED
    assert refused(setup, patch, qa, policy=ReviewerPolicy(max_total_bytes=10)) is C.POLICY_DENIED
