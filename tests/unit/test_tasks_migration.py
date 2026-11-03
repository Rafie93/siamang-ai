"""Schema v3 (tasks rebuild): fresh, v2 -> v3 with data, rollback, re-entrant transactions."""

import sqlite3

import pytest

from siamang.contracts import EventType, new_event
from siamang.projects import ProjectRegistry
from siamang.storage import SCHEMA_VERSION, Database, EventStore, SchemaError, TaskRepository
from siamang.storage.database import SCHEMA_STEPS, _schema_sql, _split_sql
from siamang.tasks import TaskEngine, TaskStatus

TS = "2026-10-04T10:00:00Z"
TS2 = "2026-10-04T11:30:00.123456Z"


def _db_at(path, version):
    conn = sqlite3.connect(path, isolation_level=None)
    for step in SCHEMA_STEPS[:version]:
        for stmt in _split_sql(_schema_sql(step)):
            conn.execute(stmt)
    conn.execute(f"PRAGMA user_version = {version}")
    return conn


def _legacy_task(conn, task_id, status, project_id="prj_toko", instruction="Tambah fitur\ndetail", ts=TS, ts2=TS2):
    conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (task_id, f"req_{task_id}", status, project_id, instruction, ts, ts2))


def test_fresh_database_is_v3(db):
    assert SCHEMA_VERSION == 3 and SCHEMA_STEPS[-1] == "schema_v3.sql"
    assert db.schema_version() == 3 and db.check() == []
    cols = [r["name"] for r in db.conn.execute("PRAGMA table_info(tasks)")]
    for c in ("task_id", "project_id", "title", "description", "status", "priority", "source", "retry_count",
              "max_retries", "parent_task_id", "metadata", "created_at", "updated_at", "started_at",
              "completed_at", "failed_at"):
        assert c in cols
    indexes = {r["name"] for r in db.conn.execute("SELECT name FROM sqlite_schema WHERE type = 'index'")}
    assert {"tasks_project_created", "tasks_status_created"} <= indexes


def test_v2_to_v3_preserves_projects_events_and_tasks(tmp_path):
    path = tmp_path / "v2data.db"  # a real v2 database with data, then upgraded
    conn = _db_at(path, 2)
    conn.execute("INSERT INTO projects VALUES ('prj_toko', 'Toko', 'toko', '', 'web', 'active', '/w/toko', '{}',"
                 " ?, ?)", (TS, TS))
    for i, status in enumerate(["accepted", "running", "waiting_approval", "completed", "failed", "cancelled"]):
        _legacy_task(conn, f"tsk_{i}", status)
    ev = new_event(EventType.TASK_STARTED, {"summary": "legacy"}, request_id="req_tsk_1", task_id="tsk_1", seq=1)
    conn.execute(
        "INSERT INTO events (event_id, event_type, contract_version, request_id, task_id, seq, timestamp, producer,"
        " payload, recorded_at) VALUES (?, ?, '0.1', ?, ?, 1, ?, 'siamang', '{\"summary\":\"legacy\"}', ?)",
        (ev.event_id, str(ev.type), ev.request_id, ev.task_id, TS, TS))
    conn.close()

    with Database(path) as db:
        assert db.schema_version() == 3 and db.check() == []
        repo = TaskRepository(db)
        rows = {r.task_id: r for r in repo.list()}
        assert {k: v.status for k, v in rows.items()} == {
            "tsk_0": "queued", "tsk_1": "running", "tsk_2": "waiting", "tsk_3": "completed", "tsk_4": "failed",
            "tsk_5": "cancelled"}
        r1 = rows["tsk_1"]
        assert (r1.title, r1.description, r1.priority, r1.source, r1.retry_count, r1.max_retries) == (
            "Tambah fitur detail", "Tambah fitur\ndetail", "normal", "system", 0, 3)
        assert r1.created_at == "2026-10-04T10:00:00.000Z" and r1.updated_at == "2026-10-04T11:30:00.123Z"
        assert r1.started_at == r1.updated_at
        assert rows["tsk_3"].completed_at and rows["tsk_4"].failed_at and rows["tsk_5"].cancelled_at
        assert rows["tsk_0"].started_at is None
        assert r1.metadata == '{"migrated_from":"schema_v1"}'
        assert EventStore(db).count() == 1
        assert ProjectRegistry(db).get("prj_toko").name == "Toko"
        # migrated tasks work with the engine; seq continues after the legacy event
        engine = TaskEngine(db, ProjectRegistry(db))
        engine.cancel("tsk_1")
        assert [e.seq for e in engine.history("tsk_1")] == [1, 2]
        assert engine.start("tsk_0").status is TaskStatus.RUNNING
    with Database(path) as db:  # reopening re-applies nothing
        assert db.schema_version() == 3 and TaskRepository(db).count() == 6


@pytest.mark.parametrize("bad", [
    {"project_id": None},  # tasks now need a project
    {"project_id": "toko"},  # wrong format
    {"task_id": "legacy-1"},  # not tsk_
    {"ts": "yesterday"},  # unparseable timestamp
    {"instruction": "   "},  # empty description/title
])
def test_bad_legacy_row_rolls_the_step_back(tmp_path, bad):
    path = tmp_path / "bad.db"
    conn = _db_at(path, 2)
    _legacy_task(conn, "tsk_ok", "accepted")
    args = {"task_id": "tsk_bad", "status": "accepted", **bad}
    task_id = args.pop("task_id")
    _legacy_task(conn, task_id, args.pop("status"), **args)
    conn.close()
    with pytest.raises(sqlite3.IntegrityError):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2  # still v2
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2  # v1-style rows untouched
    assert conn.execute("SELECT name FROM sqlite_schema WHERE name = 'tasks_v3'").fetchone() is None
    conn.close()


def test_failed_v3_step_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    _db_at(path, 2).close()
    import siamang.storage.database as dbmod

    real = dbmod._schema_sql
    monkeypatch.setattr(dbmod, "_schema_sql",
                        lambda name: real(name) + "\nNOT SQL;" if name == "schema_v3.sql" else real(name))
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    monkeypatch.undo()
    with Database(path) as db:  # a later open with the correct step succeeds
        assert db.schema_version() == 3 and db.check() == []


def test_newer_schema_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    conn = _db_at(path, 3)
    conn.execute("PRAGMA user_version = 4")
    conn.close()
    with pytest.raises(SchemaError):
        Database(path)


def test_check_reports_missing_task_triggers(db):
    db.conn.execute("DROP TRIGGER tasks_no_delete")
    assert db.check() == ["missing trigger tasks_no_delete"]


# --- re-entrant transactions ---------------------------------------------------------------


def _names(db):
    return {r[0] for r in db.conn.execute("SELECT name FROM projects")}


def _insert_project(conn, pid):
    conn.execute("INSERT INTO projects VALUES (?, ?, ?, '', 'web', 'active', ?, '{}', ?, ?)",
                 (pid, pid, pid, f"/w/{pid}", TS, TS))


def test_nested_transaction_commits_with_outer(db):
    with db.transaction() as conn:
        _insert_project(conn, "prj_a")
        with db.transaction() as inner:
            _insert_project(inner, "prj_b")
    assert _names(db) == {"prj_a", "prj_b"} and not db.conn.in_transaction


def test_inner_failure_rolls_back_inner_only_if_caught(db):
    with db.transaction() as conn:
        _insert_project(conn, "prj_a")
        with pytest.raises(RuntimeError):
            with db.transaction() as inner:
                _insert_project(inner, "prj_b")
                raise RuntimeError("inner")
    assert _names(db) == {"prj_a"}


def test_outer_failure_rolls_back_everything(db):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            _insert_project(conn, "prj_a")
            with db.transaction() as inner:
                _insert_project(inner, "prj_b")
            raise RuntimeError("outer")
    assert _names(db) == set() and not db.conn.in_transaction
