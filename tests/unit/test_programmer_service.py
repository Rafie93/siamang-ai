"""Programmer.program: validated inputs, one model call, strictly validated proposal, nothing applied."""

import dataclasses
import json

import pytest

from siamang.analyst import SystemAnalysis
from siamang.analyst.finding import Finding
from siamang.models import ModelTimeoutError, RateLimitedError
from siamang.planner import Plan
from siamang.planner.step import PlanStep
from siamang.programmer import Programmer, ProgrammingPolicy, ProgrammingResult
from siamang.programmer import service as service_mod
from siamang.programmer.errors import ProgrammingError, ProgrammingErrorCode
from siamang.storage import Database
from siamang.tasks import TaskFailure
from tests.support.analyst import write
from tests.support.fake_models import VendorAuthError, VendorTimeout
from tests.support.programmer import AUTH, AUTH_TEXT, STEP, change, make_model, make_setup, reply, sha, tree

C = ProgrammingErrorCode


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


def snapshot(setup):
    task_id = setup.world.task.task_id
    return setup.world.engine.get(task_id), len(setup.world.engine.history(task_id))


def refused(programmer, request):
    with pytest.raises(ProgrammingError) as info:
        programmer.program(request)
    return info.value


def failed(result):
    assert result.status.value == "failed" and result.patch is None
    return result.error["code"]


# --- the normal path ---------------------------------------------------------------------


def test_completed_proposal(setup):
    model = make_model(reply([change(), change("backend/routes/validation.py", "create", "def ok(u):\n    return u\n"),
                              change("README.md", "delete", None)]))
    before_task, before_files = snapshot(setup), tree(setup.root)
    r = setup.programmer(model).program(setup.request())
    assert type(r) is ProgrammingResult and r.status.value == "completed" and r.programming_id.startswith("prg_")
    assert r.patch.patch_id.startswith("pch_") and model.calls == 1 and r.metadata["model_calls"] == 1
    modify, create, delete = r.changes
    assert (modify.path, modify.operation.value, modify.before.value) == (AUTH, "modify", "observed")
    assert modify.before_hash == sha(AUTH_TEXT) == sha((setup.root / AUTH).read_text())
    assert (create.before.value, create.before_hash) == ("absent", None)
    assert (delete.before.value, delete.after_content) == ("observed", None)
    assert all(c.step_id == STEP for c in r.changes)
    assert snapshot(setup) == before_task and tree(setup.root) == before_files  # nothing changed anywhere
    assert r.metadata["strategy"] == "programming.model" and r.metadata["model_used"] == "fake-a"
    assert r.metadata["plan_content_hash"] == setup.plan.content_hash
    assert r.metadata["analysis_content_hash"] == setup.analysis.content_hash


def test_analysis_semantics_are_preserved(setup):
    r = setup.programmer(make_model()).program(setup.request())
    assert all(f"From analysis: {u}" in r.unknowns for u in setup.analysis.unknowns)  # unknowns stay unknown
    assert any("inferred, not observed" in w for w in r.warnings)  # inferences are flagged, not promoted


def test_model_warnings_and_unknowns_kept(setup):
    model = make_model(reply(warnings=["Relies on inferred FastAPI usage."],
                             unknowns=["Authentication implementation requires verification."]))
    r = setup.programmer(model).program(setup.request())
    assert "Relies on inferred FastAPI usage." in r.warnings
    assert "Authentication implementation requires verification." in r.unknowns


def test_waiting(setup):
    model = make_model(reply([], status="waiting", unknowns=["Authentication architecture not established."]))
    r = setup.programmer(model).program(setup.request())
    assert r.status.value == "waiting" and r.patch is None and model.calls == 1
    assert "Authentication architecture not established." in r.unknowns


def test_structured_output_preferred_when_available(setup):
    plain, structured = make_model(), make_model(structured=True)
    setup.programmer(plain).program(setup.request())
    setup.programmer(structured).program(setup.request())
    assert plain.requests[0].response_format.value == "text" and plain.requests[0].json_schema is None
    assert structured.requests[0].response_format.value == "json" and structured.requests[0].json_schema
    for m in (plain, structured):
        req = m.requests[0]
        assert req.model_id is None and req.options.temperature == 0
        assert req.effective_capabilities <= {"text_generation", "structured_output"}


def test_fenced_json_is_accepted(setup):
    r = setup.programmer(make_model("```json\n" + reply() + "\n```")).program(setup.request())
    assert r.status.value == "completed"


def test_deterministic_hash_for_same_content(setup):
    a = setup.programmer(make_model()).program(setup.request())
    b = setup.programmer(make_model()).program(setup.request())
    assert a.programming_id != b.programming_id and a.patch.patch_id != b.patch.patch_id
    assert a.content_hash == b.content_hash and a.patch.content_hash == b.patch.content_hash
    c = setup.programmer(make_model(reply([change(content="def login(u):\n    return u\n")]))).program(setup.request())
    assert c.content_hash != a.content_hash


def test_project_change_changes_before_hash(setup):
    a = setup.programmer(make_model()).program(setup.request())
    write(setup.root, {AUTH: "def login():\n    return None\n"})
    b = setup.programmer(make_model()).program(setup.request())
    assert a.changes[0].before_hash != b.changes[0].before_hash


def test_context_files_come_from_the_analysis(setup):
    model = make_model()
    r = setup.programmer(model).program(setup.request())
    assert AUTH in r.metadata["context_files"]
    prompt = model.requests[0].messages[-1].content
    payload = json.loads(prompt.split("\n", 1)[1])
    assert {f["path"] for f in payload["files"]} == set(r.metadata["context_files"])
    assert [s["step_id"] for s in payload["plan"]["steps"]] == [s.step_id for s in setup.plan.steps]
    assert payload["analysis"]["unknowns"] == list(setup.analysis.unknowns)
    assert "root_path" not in prompt and str(setup.root) not in prompt


def test_context_file_limit(setup, db):
    policy = ProgrammingPolicy(max_context_files=1)
    r = setup.programmer(make_model(), policy=policy).program(setup.request())
    assert len(r.metadata["context_files"]) == 1
    assert any("context file limit" in u for u in r.unknowns)


def test_unverified_before_state_is_explicit(setup):
    write(setup.root, {AUTH: "x = 1\n" * 50_000})  # larger than the read limit
    policy = ProgrammingPolicy(max_file_size=64 * 1024, max_context_file_size=1024)
    r = setup.programmer(make_model(), policy=policy).program(setup.request())
    assert r.changes[0].before.value == "unverified" and r.changes[0].before_hash is None
    assert any(AUTH in u and "not verified" in u for u in r.unknowns)


# --- input refusals (raised; nothing is generated) ------------------------------------------


def test_unknown_task_and_project_mismatch(setup, db):
    model = make_model()
    p = setup.programmer(model)
    other = setup.world.projects.register(name="Other", root_path="/work/other")
    assert refused(p, setup.request(task_id="tsk_01UNKNOWN")).code is C.TASK_NOT_FOUND
    assert refused(p, setup.request(project_id=other.project_id)).code is C.REQUEST_INVALID
    assert model.calls == 0


@pytest.mark.parametrize("final", ["complete", "cancel"])
def test_final_tasks_refused(db, tmp_path, final):
    s = make_setup(db, tmp_path, start=True)
    engine, tid = s.world.engine, s.world.task.task_id
    if final == "complete":
        from siamang.tasks import TaskResult
        engine.complete(tid, TaskResult(summary="done"))
    else:
        engine.cancel(tid)
    model = make_model()
    error = refused(s.programmer(model), s.request())
    assert error.code is C.POLICY_DENIED and model.calls == 0


@pytest.mark.parametrize("status", ["running", "failed"])
def test_non_final_tasks_allowed(db, tmp_path, status):
    s = make_setup(db, tmp_path, start=True)
    if status == "failed":
        s.world.engine.fail(s.world.task.task_id, TaskFailure(code="x", message="boom"))
    assert s.programmer(make_model()).program(s.request()).status.value == "completed"


def test_archived_and_unavailable_projects(setup, tmp_path):
    p = setup.programmer(make_model())
    setup.world.projects.archive(setup.world.project.project_id)
    assert refused(p, setup.request()).code is C.PROJECT_ARCHIVED
    setup.world.projects.restore(setup.world.project.project_id)
    for path in sorted(setup.root.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    setup.root.rmdir()
    assert refused(p, setup.request()).code is C.PROJECT_UNAVAILABLE


@pytest.fixture
def other(tmp_path):
    database = Database(tmp_path / "other.db")
    (tmp_path / "o").mkdir()
    yield make_setup(database, tmp_path / "o")
    database.close()


def test_plan_for_another_task_or_project(setup, other):
    p = setup.programmer(make_model())
    wrong_task = dataclasses.replace(setup.plan, task_id=other.world.task.task_id)
    wrong_project = dataclasses.replace(setup.plan, project_id=other.world.project.project_id)
    for plan in (wrong_task, wrong_project, other.plan):
        assert refused(p, setup.request(plan=plan)).code is C.PLAN_INVALID


def test_tampered_or_empty_plan(setup):
    p = setup.programmer(make_model())
    tampered = dataclasses.replace(setup.plan)
    object.__setattr__(tampered, "steps", (*tampered.steps, tampered.steps[0]))  # duplicate step IDs
    assert refused(p, setup.request(plan=tampered)).code is C.PLAN_INVALID
    step = dataclasses.replace(setup.plan.steps[0])
    object.__setattr__(step, "role", "shell")
    bad_step = dataclasses.replace(setup.plan)
    object.__setattr__(bad_step, "steps", (step, *setup.plan.steps[1:]))
    assert refused(p, setup.request(plan=bad_step)).code is C.PLAN_INVALID
    empty = Plan(setup.plan.plan_id, setup.plan.task_id, setup.plan.project_id, "o", ())
    assert refused(p, setup.request(plan=empty)).code is C.PLAN_INVALID


def test_analysis_for_another_task_or_project(setup, other):
    p = setup.programmer(make_model())
    for analysis in (dataclasses.replace(setup.analysis, task_id=other.world.task.task_id),
                     dataclasses.replace(setup.analysis, project_id=other.world.project.project_id), other.analysis):
        assert refused(p, setup.request(analysis=analysis)).code is C.ANALYSIS_INVALID


def test_malformed_analysis(setup):
    p = setup.programmer(make_model())
    finding = dataclasses.replace(setup.analysis.findings[0])
    object.__setattr__(finding, "evidence", ())  # an observed/inferred finding without evidence
    object.__setattr__(finding, "confidence", "observed")
    broken = dataclasses.replace(setup.analysis)
    object.__setattr__(broken, "findings", (finding,))
    assert refused(p, setup.request(analysis=broken)).code is C.ANALYSIS_INVALID
    forged = dataclasses.replace(setup.analysis)
    object.__setattr__(forged, "analysis_id", "../../etc")
    assert refused(p, setup.request(analysis=forged)).code is C.ANALYSIS_INVALID


def test_unknown_evidence_is_tolerated_as_unknown(setup):
    unknown = Finding("finding_99", "unknown", "info", "Auth flow unknown", "Not established.", (), "unknown")
    analysis = dataclasses.replace(setup.analysis, findings=(*setup.analysis.findings, unknown))
    r = setup.programmer(make_model()).program(setup.request(analysis=analysis))
    assert r.status.value == "completed"


def test_instruction_policy(setup):
    model = make_model()
    p = setup.programmer(model, policy=ProgrammingPolicy(max_instruction_length=10))
    assert refused(p, setup.request()).code is C.POLICY_DENIED and model.calls == 0


def test_context_too_large(setup):
    model = make_model()
    p = setup.programmer(model, policy=ProgrammingPolicy(max_context_bytes=100))
    assert refused(p, setup.request()).code is C.POLICY_DENIED and model.calls == 0


def test_constructor_guards(setup):
    with pytest.raises(TypeError):
        Programmer(object())
    with pytest.raises(TypeError):
        Programmer(setup.world.engine, models=object())
    with pytest.raises(TypeError):
        Programmer(setup.world.engine, policy={})
    for bad in ("programming.shell", "", None, lambda: 1, Programmer, "os.system"):
        with pytest.raises(ValueError):
            Programmer(setup.world.engine, strategy=bad)
    with pytest.raises(ProgrammingError):
        Programmer(setup.world.engine).program({"task_id": "x"})


# --- the attempt (returned as a failed result; exactly one model call at most) -------------------


def test_no_router_fails_without_a_call(setup):
    r = setup.programmer(None).program(setup.request())
    assert failed(r) == "programmer_model_unavailable" and r.error["cause"] == "no_router"
    assert r.metadata["model_calls"] == 0


@pytest.mark.parametrize("exc, code, cause", [
    (VendorTimeout("slow"), "programmer_model_timeout", "timeout"),
    (ModelTimeoutError("slow"), "programmer_model_timeout", "timeout"),
    (RateLimitedError("busy"), "programmer_model_unavailable", "rate_limited"),
    (ConnectionError("down"), "programmer_model_unavailable", "provider_unavailable"),
    (VendorAuthError("bad key sk-fake-DO-NOT-LEAK-1234567890"), "programmer_model_rejected", "authentication_error"),
    (RuntimeError("boom"), "programmer_model_unavailable", "unknown_error"),
])
def test_model_failures_normalized_without_retry(setup, exc, code, cause):
    model = make_model(fail_with=exc)
    r = setup.programmer(model).program(setup.request())
    assert failed(r) == code and r.error["cause"] == cause
    assert model.calls == 1 and r.metadata["model_calls"] == 1
    assert "DO-NOT-LEAK" not in json.dumps(r.to_dict())


def test_routing_failure(setup):
    model = make_model()
    model.router.registry.disable_model("fake-a")
    r = setup.programmer(model).program(setup.request())
    assert failed(r) == "programmer_model_unavailable" and model.calls == 0


def test_unknown_step_reference(setup):
    model = make_model(reply([change(step_id="step_42")]))
    r = setup.programmer(model).program(setup.request())
    assert failed(r) == "programmer_model_invalid_output" and model.calls == 1


@pytest.mark.parametrize("bad, code", [
    (change("missing.py", "modify"), "programmer_change_rejected"),
    (change("missing.py", "delete", None), "programmer_change_rejected"),
    (change(AUTH, "modify", AUTH_TEXT), "programmer_change_rejected"),  # "modification" that changes nothing
    (change("Backend/routes/auth.py", "create"), "programmer_change_rejected"),  # case-only clash with backend/
    (change("backend", "modify"), "programmer_change_rejected"),  # a directory
    (change("backend/routes/auth.py/x.py", "create"), "programmer_change_rejected"),  # through a file
])
def test_changes_checked_against_the_snapshot(setup, bad, code):
    r = setup.programmer(make_model(reply([bad]))).program(setup.request())
    assert failed(r) == code


def test_create_on_a_file_the_model_read_becomes_a_modification(setup):
    r = setup.programmer(make_model(reply([change(AUTH, "create", "x = 2\n")]))).program(setup.request())
    assert r.status.value == "completed" and AUTH in r.metadata["context_files"]
    made = r.patch.changes[0]
    assert (made.path, made.operation.value, made.before.value) == (AUTH, "modify", "observed")
    assert made.before_hash == sha(AUTH_TEXT) and made.after_content == "x = 2\n"
    assert any(AUTH in w and "treated as a modification" in w for w in r.warnings)


def test_create_on_a_file_the_model_never_saw_is_refused(setup):
    write(setup.root, {"unseen.py": "x = 1\n"})
    r = setup.programmer(make_model(reply([change("unseen.py", "create", "x = 2\n")]))).program(setup.request())
    assert failed(r) == "programmer_change_rejected"
    assert "unseen.py" not in r.metadata["context_files"] and "unseen.py" in r.error["details"]
    assert (setup.root / "unseen.py").read_text() == "x = 1\n"


def test_symlinks_are_never_targets(setup, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("x\n")
    (setup.root / "link").symlink_to(outside)
    (setup.root / "flink.py").symlink_to(outside / "x.py")
    for bad in (change("link/x.py", "modify"), change("link/new.py", "create"), change("flink.py", "modify")):
        r = setup.programmer(make_model(reply([bad]))).program(setup.request())
        assert failed(r) == "programmer_change_rejected"
    assert (outside / "x.py").read_text() == "x\n"


def test_policy_limits_on_model_output(setup):
    p = setup.programmer(make_model(reply([change(content="x" * 2048 + "\n")])),
                         policy=ProgrammingPolicy(max_file_size=1024, max_total_output=4096))
    assert failed(p.program(setup.request())) == "programmer_policy_denied"
    many = [change(f"new_{i}.py", "create", "x = 1\n") for i in range(3)]
    p = setup.programmer(make_model(reply(many)), policy=ProgrammingPolicy(max_changes=2))
    assert failed(p.program(setup.request())) == "programmer_model_invalid_output"
    p = setup.programmer(make_model(reply(warnings=["w"] * 3)), policy=ProgrammingPolicy(max_warnings=2))
    assert failed(p.program(setup.request())) == "programmer_policy_denied"


def test_strategy_crash_is_contained(setup, monkeypatch):
    def boom(self, context, models):
        raise RuntimeError("/Users/someone/secret/path API_KEY=abcd1234efgh5678")

    monkeypatch.setattr(service_mod.ModelStrategy, "propose", boom)
    r = setup.programmer(make_model()).program(setup.request())
    assert failed(r) == "programmer_internal" and r.error["cause"] == "RuntimeError"
    assert "/Users/someone" not in json.dumps(r.to_dict()) and "abcd1234" not in json.dumps(r.to_dict())


def test_failed_result_keeps_input_notes(setup):
    r = setup.programmer(make_model("not json")).program(setup.request())
    assert failed(r) == "programmer_model_invalid_output"
    assert any(u.startswith("From analysis:") for u in r.unknowns)


def test_plan_and_analysis_are_not_mutated(setup):
    plan_hash, analysis_hash = setup.plan.content_hash, setup.analysis.content_hash
    setup.programmer(make_model()).program(setup.request())
    assert setup.plan.content_hash == plan_hash and setup.analysis.content_hash == analysis_hash


def test_mismatched_plan_hash_warns(setup, db, tmp_path):
    from siamang.planner import Planner, PlanningRequest

    plan2 = Planner(setup.world.engine).plan(PlanningRequest(task_id=setup.world.task.task_id,
                                                             project_id=setup.world.project.project_id,
                                                             instruction="Something else entirely"))
    r = setup.programmer(make_model()).program(setup.request(plan=plan2))
    assert any("different plan" in w for w in r.warnings)


def test_plan_step_objects_are_exact(setup):
    class LiveStep(PlanStep):
        pass

    s = setup.plan.steps[0]
    live = LiveStep(**{f.name: getattr(s, f.name) for f in dataclasses.fields(PlanStep)})
    with pytest.raises(Exception):
        Plan(setup.plan.plan_id, setup.plan.task_id, setup.plan.project_id, "o", (live,))
    assert isinstance(setup.analysis, SystemAnalysis)
