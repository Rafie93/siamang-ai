"""`siamang tasks list|get|recovery|cancel|retry`: task state only; there is no `tasks run`."""

import json

import pytest

from siamang.cli.main import main
from siamang.projects import ProjectRegistry
from siamang.storage import Database
from siamang.tasks import TaskEngine, TaskFailure, TaskRequest
from tests.support.projects import Clock


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "data" / "s.db"


@pytest.fixture
def cfg(config_file, db_path):
    return config_file(f'[storage]\npath = "{db_path}"\n[logging]\nlevel = "WARNING"\n')


def run(capsys, cfg, *argv):
    code = main(["--config", str(cfg), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def seeded(db_path):
    """Two projects; tasks: failed (retryable), running, queued (second project)."""
    with Database(db_path) as db:
        projects = ProjectRegistry(db)
        a = projects.register(name="Toko", root_path="/work/toko")
        b = projects.register(name="Kasir", root_path="/work/kasir")
        clock = Clock()
        e = TaskEngine(db, projects, clock=clock)

        def new(project, description):
            clock.advance()  # distinct created_at: list order is creation order
            return e.create(TaskRequest(project_id=project.project_id, description=description, source="cli")).task

        failed = new(a, "Add inventory")
        e.start(failed.task_id)
        e.fail(failed.task_id, TaskFailure("MODEL_UNAVAILABLE", "provider down", retryable=True))
        running = new(a, "Fix login")
        e.start(running.task_id)
        queued = new(b, "Write report")
    return {"a": a.project_id, "b": b.project_id, "failed": failed.task_id, "running": running.task_id,
            "queued": queued.task_id}


def _statuses(db_path):
    with Database(db_path) as db:
        return dict(db.conn.execute("SELECT task_id, status FROM tasks").fetchall())


def test_read_commands_without_database_do_not_create_it(capsys, cfg, db_path):
    code, out, _ = run(capsys, cfg, "tasks")
    assert code == 0 and "No tasks." in out
    assert json.loads(run(capsys, cfg, "tasks", "list", "--json")[1]) == []
    code, out, _ = run(capsys, cfg, "tasks", "recovery")
    assert code == 0 and "No active tasks." in out
    assert json.loads(run(capsys, cfg, "tasks", "recovery", "--json")[1]) == {"running": [], "waiting": [],
                                                                               "queued": []}
    code, _, err = run(capsys, cfg, "tasks", "get", "tsk_01NOPE")
    assert code == 1 and "task_not_found" in err
    code, _, err = run(capsys, cfg, "tasks", "cancel", "tsk_01NOPE", "--yes")
    assert code == 1 and "task_not_found" in err
    assert not db_path.exists() and not db_path.parent.exists()


def test_list_text_and_json(capsys, cfg, seeded):
    code, out, _ = run(capsys, cfg, "tasks")
    lines = out.splitlines()
    assert code == 0 and lines[0].split()[:2] == ["ID", "STATUS"] and len(lines) == 4
    assert [line.split()[0] for line in lines[1:]] == [seeded["failed"], seeded["running"], seeded["queued"]]
    assert run(capsys, cfg, "tasks", "list")[1] == out
    data = json.loads(run(capsys, cfg, "tasks", "list", "--json")[1])
    assert [t["status"] for t in data] == ["failed", "running", "queued"]
    assert data[0]["last_error"]["code"] == "MODEL_UNAVAILABLE"


def test_list_filters_and_pagination(capsys, cfg, seeded):
    def ids(*argv):
        return [t["task_id"] for t in json.loads(run(capsys, cfg, "tasks", "list", "--json", *argv)[1])]

    assert ids("--project", seeded["b"]) == [seeded["queued"]]
    assert ids("--status", "running") == [seeded["running"]]
    assert ids("--project", seeded["a"], "--status", "queued") == []
    assert ids("--limit", "1", "--offset", "1") == [seeded["running"]]


@pytest.mark.parametrize("argv, err", [
    (["--project", "prj_x\n"], "project_id"),
    (["--project", "/work/toko"], "project_id"),
    (["--limit", "0"], "limit"),
    (["--limit", "100000"], "limit"),
    (["--offset", "-1"], "offset"),
])
def test_list_rejects_bad_filters(capsys, cfg, seeded, argv, err):
    code, out, stderr = run(capsys, cfg, "tasks", "list", *argv)
    assert code == 1 and out == "" and err in stderr


def test_list_rejects_unknown_status(cfg, seeded):
    with pytest.raises(SystemExit) as info:
        main(["--config", str(cfg), "tasks", "list", "--status", "executing"])
    assert info.value.code == 2


def test_get(capsys, cfg, seeded):
    code, out, _ = run(capsys, cfg, "tasks", "get", seeded["failed"], "--json")
    d = json.loads(out)
    assert code == 0 and d["task_id"] == seeded["failed"] and d["status"] == "failed" and "events" not in d
    code, out, _ = run(capsys, cfg, "tasks", "get", seeded["failed"], "--json", "--events")
    events = json.loads(out)["events"]
    assert [e["type"] for e in events] == ["task.accepted", "task.started", "task.failed"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    code, out, _ = run(capsys, cfg, "tasks", "get", seeded["failed"], "--events")
    assert code == 0 and "status" in out and "task.failed" in out


@pytest.mark.parametrize("bad", ["tsk_x\n", "prj_01ABC", "../tsk_x", "1"])
def test_get_rejects_malformed_ids(capsys, cfg, seeded, bad):
    code, out, err = run(capsys, cfg, "tasks", "get", bad)
    assert code == 1 and out == "" and "invalid_task" in err


def test_recovery_reports_and_changes_nothing(capsys, cfg, seeded, db_path):
    before = _statuses(db_path)
    report = json.loads(run(capsys, cfg, "tasks", "recovery", "--json")[1])
    assert [t["task_id"] for t in report["running"]] == [seeded["running"]]
    assert [t["task_id"] for t in report["queued"]] == [seeded["queued"]]
    assert report["waiting"] == []
    code, out, _ = run(capsys, cfg, "tasks", "recovery")
    assert code == 0 and "Running" in out and seeded["running"] in out
    assert _statuses(db_path) == before


def test_state_changes_require_yes(capsys, cfg, seeded, db_path):
    before = _statuses(db_path)
    for argv in (["cancel", seeded["running"]], ["retry", seeded["failed"]]):
        code, out, err = run(capsys, cfg, "tasks", *argv)
        assert code == 2 and out == "" and "--yes" in err
    assert _statuses(db_path) == before


def test_cancel_and_retry(capsys, cfg, seeded, db_path):
    code, out, _ = run(capsys, cfg, "tasks", "cancel", seeded["running"], "--yes", "--reason", "obsolete", "--json")
    assert code == 0 and json.loads(out)["status"] == "cancelled"
    code, out, _ = run(capsys, cfg, "tasks", "retry", seeded["failed"], "--yes", "--json")
    d = json.loads(out)
    assert code == 0 and (d["status"], d["retry_count"]) == ("queued", 1)
    # final / wrong-state transitions are refused with a typed error
    code, _, err = run(capsys, cfg, "tasks", "cancel", seeded["running"], "--yes")
    assert code == 1 and "invalid_transition" in err
    code, _, err = run(capsys, cfg, "tasks", "retry", seeded["queued"], "--yes")
    assert code == 1 and "invalid_transition" in err


@pytest.mark.parametrize("sub", ["run", "execute", "start", "complete", "create", "exec"])
def test_no_execution_or_lifecycle_driving_command(cfg, sub):
    with pytest.raises(SystemExit) as info:
        main(["--config", str(cfg), "tasks", sub, "tsk_01ABC"])
    assert info.value.code == 2
