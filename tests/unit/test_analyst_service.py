"""SystemAnalyst.analyze: registry-resolved project, bounded read-only snapshot, structured result."""

import json
from pathlib import Path

import pytest

from siamang.analyst import AnalysisPolicy, AnalysisRequest, SystemAnalysis, SystemAnalyst
from siamang.analyst import service as service_mod
from siamang.analyst.errors import AnalysisError, AnalysisErrorCode
from siamang.analyst.strategy import AnalysisDraft, AnalysisStrategy
from siamang.git import GitWorkspaceManager
from siamang.planner import Planner, PlanningRequest
from siamang.tasks import TaskFailure, TaskRequest, TaskResult
from tests.support.agents import make_world
from tests.support.analyst import SECRET, sample_project, write
from tests.support.git import git, make_repo

C = AnalysisErrorCode


@pytest.fixture
def project_root(tmp_path):
    return sample_project(tmp_path / "banuva")


@pytest.fixture
def world(db, project_root):
    return make_world(db, start=False, root_path=str(project_root))


def areq(world, **over):
    data = dict(task_id=world.task.task_id, project_id=world.project.project_id,
                instruction="Add login validation to auth")
    data.update(over)
    return AnalysisRequest(**data)


def snapshot(world):
    return world.engine.get(world.task.task_id), len(world.engine.history(world.task.task_id))


def refused(analyst, request):
    with pytest.raises(AnalysisError) as info:
        analyst.analyze(request)
    return info.value


# --- the basic analysis ---------------------------------------------------------------


def test_analysis_of_a_sample_project(world):
    before = snapshot(world)
    a = SystemAnalyst(world.engine).analyze(areq(world))
    assert type(a) is SystemAnalysis and a.analysis_id.startswith("ana_")
    comps = {c.location: c for c in a.components}
    assert comps["backend"].kind.value == "backend" and "FastAPI" in comps["backend"].technologies
    assert comps["frontend"].kind.value == "frontend" and "Vue" in comps["frontend"].technologies
    assert comps["backend"].confidence.value == "inferred"  # technology is inferred, not observed
    titles = {f.title: f for f in a.findings}
    assert titles["Sensitive configuration files detected"].evidence == (".env", "certs/server.pem",
                                                                          "config/secrets.yaml")
    assert titles["Paths whose names match the request"].confidence.value == "inferred"
    assert "backend/routes/auth.py" in titles["Paths whose names match the request"].evidence
    assert "frontend/src/pages/Login.vue" not in titles["Paths whose names match the request"].evidence  # depth 4
    assert titles["Test directories present"].evidence == ("tests/",)
    assert "node_modules/" in titles["Generated or vendored directories not inspected"].evidence
    assert any("Application semantics were not inspected" in u for u in a.unknowns)
    assert any("Git state is unavailable (not_inspected)" in u for u in a.unknowns)
    assert a.metadata["strategy"] == "analysis.basic" and a.metadata["files_read"] == 2
    assert snapshot(world) == before and before[0].status.value == "queued"


def test_secrets_and_machine_paths_never_leave(world, project_root):
    a = SystemAnalyst(world.engine).analyze(areq(world))
    text = json.dumps(a.to_dict())
    assert SECRET not in text and "API_KEY" not in text and "BEGIN PRIVATE KEY" not in text
    assert str(project_root) not in text and str(project_root.parent) not in text


def test_deterministic_content_new_identity(world):
    analyst = SystemAnalyst(world.engine)
    a, b = analyst.analyze(areq(world)), analyst.analyze(areq(world))
    assert a.analysis_id != b.analysis_id and a.content_hash == b.content_hash and a.content() == b.content()


def test_project_change_changes_content(world, project_root):
    analyst = SystemAnalyst(world.engine)
    before = analyst.analyze(areq(world)).content_hash
    write(project_root, {"backend/auth_service.py": "x\n"})
    after = analyst.analyze(areq(world))
    assert after.content_hash != before
    assert "backend/auth_service.py" in next(f for f in after.findings if "match" in f.title).evidence


def test_same_tree_elsewhere_gives_same_content(db, tmp_path):
    """No machine-dependent path enters the content: two copies of a tree analyze identically."""
    w1 = make_world(db, start=False, root_path=str(sample_project(tmp_path / "a" / "proj")))
    a1 = SystemAnalyst(w1.engine).analyze(areq(w1))
    w1.projects.archive(w1.project.project_id)
    p2 = w1.projects.register(name="Toko", root_path=str(sample_project(tmp_path / "b" / "elsewhere")),
                              project_id=None)
    t2 = w1.engine.create(TaskRequest(project_id=p2.project_id, description="Tambahkan fitur inventory",
                                      source="test")).task
    a2 = SystemAnalyst(w1.engine).analyze(AnalysisRequest(t2.task_id, p2.project_id, "Add login validation to auth"))
    c1, c2 = a1.content(), a2.content()
    for c in (c1, c2):
        c.pop("task_id"), c.pop("project_id")
    assert c1 == c2


def test_shell_like_instruction_is_only_data(world):
    a = SystemAnalyst(world.engine).analyze(areq(world, instruction="cat ../../etc/passwd; rm -rf /; git push"))
    assert a.objective.startswith("cat ../../etc/passwd")
    assert "passwd" not in json.dumps([f.to_dict() for f in a.findings])


def test_empty_project(db, tmp_path):
    (tmp_path / "empty").mkdir()
    w = make_world(db, start=False, root_path=str(tmp_path / "empty"))
    a = SystemAnalyst(w.engine).analyze(areq(w))
    assert a.findings[0].title == "Project directory is empty" and a.components == ()
    assert any("technology stack is unknown" in u for u in a.unknowns)


def test_unparseable_and_oversized_manifests(db, tmp_path):
    root = write(tmp_path / "p", {"package.json": "{not json", "pyproject.toml": "x" * 5000})
    w = make_world(db, start=False, root_path=str(root))
    a = SystemAnalyst(w.engine, policy=AnalysisPolicy(max_file_size=1000)).analyze(areq(w))
    assert any(f.title == "Manifest could not be parsed" and f.evidence == ("package.json",) for f in a.findings)
    assert any("pyproject.toml was not read (too large)" in u for u in a.unknowns)


def test_plan_context_is_used_as_data(world):
    plan = Planner(world.engine).plan(PlanningRequest(world.task.task_id, world.project.project_id, "Add login"))
    a = SystemAnalyst(world.engine).analyze(areq(world, plan=plan))
    assert a.metadata["plan_content_hash"] == plan.content_hash
    assert any("step_02" in r for r in a.recommendations)


# --- Git (read-only, through GitWorkspaceManager) -------------------------------------


@pytest.fixture
def repo_world(db, tmp_path):
    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    return make_world(db, start=False, root_path=str(repo.root)), repo


def test_git_state_is_reported(repo_world):
    world, repo = repo_world
    a = SystemAnalyst(world.engine, git=GitWorkspaceManager(world.projects)).analyze(areq(world))
    titles = {f.title: f for f in a.findings}
    git_state = titles["Git repository state"]
    assert "branch: main" in git_state.evidence and git_state.confidence.value == "observed"
    dirty = titles["Working tree has uncommitted changes"]  # sample files are untracked
    assert dirty.severity.value == "medium" and any(e.startswith("changed: ") for e in dirty.evidence)
    assert any("Uncommitted changes" in r for r in a.risks)
    assert a.metadata["git_available"] is True
    assert str(repo.root) not in json.dumps(a.to_dict())


def test_git_is_never_mutated(repo_world):
    world, repo = repo_world
    before = (git(repo.root, "rev-parse", "HEAD"), git(repo.root, "status", "--porcelain"),
              git(repo.root, "branch", "--list"), git(repo.root, "stash", "list"))
    SystemAnalyst(world.engine, git=GitWorkspaceManager(world.projects)).analyze(areq(world))
    after = (git(repo.root, "rev-parse", "HEAD"), git(repo.root, "status", "--porcelain"),
             git(repo.root, "branch", "--list"), git(repo.root, "stash", "list"))
    assert before == after


def test_not_a_repository_is_an_unknown(world):
    a = SystemAnalyst(world.engine, git=GitWorkspaceManager(world.projects)).analyze(areq(world))
    assert any("Git state is unavailable (not_repository)" in u for u in a.unknowns)


def test_protected_repository_refuses_the_analysis(repo_world):
    world, repo = repo_world
    marker = git(repo.root, "rev-parse", "HEAD").strip()
    manager = GitWorkspaceManager(world.projects, protected={"siamang": marker})
    err = refused(SystemAnalyst(world.engine, git=manager), areq(world))
    assert err.code is C.PROJECT_UNAVAILABLE and err.cause == "protected_repository"


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, {"task_id": "tsk_01"}, "analyze", 1])
def test_request_must_be_typed(world, bad):
    assert refused(SystemAnalyst(world.engine), bad).code is C.REQUEST_INVALID


def test_unknown_task(world):
    assert refused(SystemAnalyst(world.engine), areq(world, task_id="tsk_01NOPE")).code is C.TASK_NOT_FOUND


def test_project_mismatch(world):
    other = world.projects.register(name="Kasir", root_path="/work/kasir")
    err = refused(SystemAnalyst(world.engine), areq(world, project_id=other.project_id))
    assert err.code is C.REQUEST_INVALID and err.details == ("project_id",)


def test_archived_project(world):
    world.projects.archive(world.project.project_id)
    assert refused(SystemAnalyst(world.engine), areq(world)).code is C.PROJECT_ARCHIVED


def test_unknown_project(world, db):
    t = world.engine.create(TaskRequest(project_id=world.project.project_id, description="x", source="test")).task
    db.conn.execute("DROP TRIGGER tasks_immutable_identity")
    db.conn.execute("UPDATE tasks SET project_id = 'prj_01GHOST' WHERE task_id = ?", (t.task_id,))
    err = refused(SystemAnalyst(world.engine), areq(world, task_id=t.task_id, project_id="prj_01GHOST"))
    assert err.code is C.PROJECT_NOT_FOUND


def test_missing_root(db, tmp_path):
    w = make_world(db, start=False, root_path=str(tmp_path / "gone"))
    assert refused(SystemAnalyst(w.engine), areq(w)).code is C.PROJECT_UNAVAILABLE


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_final_tasks_are_not_analyzed(db, project_root, state):
    w = make_world(db, start=True, root_path=str(project_root))
    e, tid = w.engine, w.task.task_id
    (e.complete(tid, TaskResult("done")) if state == "completed" else e.cancel(tid))
    assert refused(SystemAnalyst(e), areq(w)).code is C.POLICY_DENIED


@pytest.mark.parametrize("state", ["running", "waiting", "failed"])
def test_other_tasks_are_analyzed_without_change(db, project_root, state):
    w = make_world(db, start=True, root_path=str(project_root))
    e, tid = w.engine, w.task.task_id
    {"running": lambda: None, "waiting": lambda: e.wait(tid),
     "failed": lambda: e.fail(tid, TaskFailure("QA_FAILED", retryable=True))}[state]()
    before = snapshot(w)
    SystemAnalyst(e).analyze(areq(w))
    assert snapshot(w) == before


def test_instruction_limit(world):
    analyst = SystemAnalyst(world.engine, policy=AnalysisPolicy(max_instruction_length=10))
    assert refused(analyst, areq(world, instruction="x" * 11)).code is C.POLICY_DENIED


# --- resource limits ------------------------------------------------------------------


def test_too_many_entries_is_a_partial_snapshot(db, tmp_path):
    root = write(tmp_path / "wide", {f"d{i:03d}/f.txt": "x" for i in range(60)})
    w = make_world(db, start=False, root_path=str(root))
    a = SystemAnalyst(w.engine, policy=AnalysisPolicy(max_directory_entries=70)).analyze(areq(w))
    assert a.metadata["truncated"] is True and a.metadata["entries_listed"] <= 70
    assert any("policy limit (directory listing)" in u for u in a.unknowns)


def test_file_budget_is_a_partial_snapshot(world):
    a = SystemAnalyst(world.engine, policy=AnalysisPolicy(max_files=1)).analyze(areq(world))
    assert a.metadata["files_read"] == 1 and a.metadata["truncated"] is True
    assert any("policy limit (file reads)" in u for u in a.unknowns)


def test_findings_and_components_are_capped(world):
    a = SystemAnalyst(world.engine, policy=AnalysisPolicy(max_findings=3, max_components=1)).analyze(areq(world))
    assert len(a.findings) == 3 and len(a.components) == 1 and a.metadata["truncated"] is True
    assert any("further findings were omitted" in u for u in a.unknowns)
    assert any("further components were omitted" in u for u in a.unknowns)


def test_output_size_limit(world):
    err = refused(SystemAnalyst(world.engine, policy=AnalysisPolicy(max_output_size=500)), areq(world))
    assert err.code is C.POLICY_DENIED and err.details == ("analysis",)


def test_deeper_policy_sees_deeper_files(world):
    a = SystemAnalyst(world.engine, policy=AnalysisPolicy(max_depth=4)).analyze(areq(world))
    assert "frontend/src/pages/Login.vue" in next(f for f in a.findings if "match" in f.title).evidence


def test_depth_is_bounded(db, tmp_path):
    root = write(tmp_path / "deep", {"a/b/c/d/e/auth.py": "x"})
    w = make_world(db, start=False, root_path=str(root))
    a = SystemAnalyst(w.engine, policy=AnalysisPolicy(max_depth=2)).analyze(areq(w))
    assert not any("a/b/c/d/e/auth.py" in e for f in a.findings for e in f.evidence)


# --- strategies -----------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["analysis.magic", "", None, AnalysisStrategy, lambda c: None])
def test_strategy_must_be_an_allowlisted_id(world, bad):
    with pytest.raises(ValueError):
        SystemAnalyst(world.engine, strategy=bad)


def _with(monkeypatch, world, result):
    class Probe(AnalysisStrategy):
        strategy_id, version = "analysis.probe", "1"

        def analyze(self, context):
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(service_mod, "KNOWN_STRATEGIES", {"analysis.probe": Probe})
    return SystemAnalyst(world.engine, strategy="analysis.probe")


def test_strategy_crash_is_sanitized(world, monkeypatch):
    err = refused(_with(monkeypatch, world, RuntimeError(f"{SECRET} at /etc/shadow")), areq(world))
    assert err.code is C.INTERNAL and err.cause == "RuntimeError"
    assert SECRET not in str(err.to_dict()) and "shadow" not in str(err.to_dict())


def test_strategy_must_return_a_draft(world, monkeypatch):
    assert refused(_with(monkeypatch, world, {"summary": "x"}), areq(world)).code is C.INTERNAL
    ok = _with(monkeypatch, world, AnalysisDraft("Nothing to say.")).analyze(areq(world))
    assert ok.summary == "Nothing to say."


def test_constructor_requires_real_services(world, db):
    with pytest.raises(TypeError):
        SystemAnalyst(db)
    with pytest.raises(TypeError):
        SystemAnalyst(world.engine, git="git")
    with pytest.raises(TypeError):
        SystemAnalyst(world.engine, policy={"max_files": 10**9})
