"""Schema-level integrity for the projects table (bypassing the registry)."""

import sqlite3

import pytest

from siamang.storage import SCHEMA_VERSION, Database, EventStore, TaskRepository
from siamang.storage.database import SCHEMA_STEPS, _schema_sql, _split_sql
from siamang.projects import ProjectRegistry

TS = "2026-10-04T10:00:00Z"
COLS = "project_id, name, name_key, description, project_type, status, root_path, metadata, created_at, updated_at"


def _insert(db, **over):
    row = dict(
        project_id="prj_x", name="X", name_key="x", description="", project_type="web",
        status="active", root_path="/w/x", metadata="{}", created_at=TS, updated_at=TS,
    )
    row.update(over)
    db.conn.execute(f"INSERT INTO projects ({COLS}) VALUES ({', '.join('?' * 10)})", tuple(row.values()))


def test_schema_version(db):
    # v2 = Phase 07 projects; v3 = Phase 10 tasks rebuild
    assert SCHEMA_VERSION == 3 == len(SCHEMA_STEPS)
    assert db.schema_version() == 3
    assert db.check() == []


def test_valid_raw_insert(db):
    _insert(db)
    assert db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


@pytest.mark.parametrize(
    "over",
    [
        {"status": "running"},
        {"status": "completed"},
        {"project_type": "spaceship"},
        {"metadata": "not json"},
        {"metadata": "[1, 2]"},
        {"metadata": '"string"'},
        {"project_id": "tsk_1"},
        {"project_id": "prj_"},
        {"root_path": "relative/path"},
        {"name": ""},
        {"name": " padded "},
        {"description": "x" * 2001},
        {"name": None},
        {"created_at": None},
    ],
)
def test_constraints_reject(db, over):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, **over)


def test_strict_types(db):
    # STRICT refuses values that cannot be stored losslessly as the declared type
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, name=b"bytes")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, metadata=b"{}")


def test_duplicate_constraints(db):
    _insert(db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, name="Y", name_key="y", root_path="/w/y")  # same project_id
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, project_id="prj_y", root_path="/w/y")  # same active name_key
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, project_id="prj_y", name="Y", name_key="y")  # same active root_path
    # archived rows may share name/root with an active one
    _insert(db, project_id="prj_old", status="archived")


def test_delete_forbidden(db):
    _insert(db)
    with pytest.raises(sqlite3.IntegrityError, match="archived, not deleted"):
        db.conn.execute("DELETE FROM projects")


@pytest.mark.parametrize("column, value", [("project_id", "prj_other"), ("created_at", "2027-01-01T00:00:00Z")])
def test_identity_immutable(db, column, value):
    _insert(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.conn.execute(f"UPDATE projects SET {column} = ?", (value,))


def test_mutable_columns_update(db):
    _insert(db)
    db.conn.execute("UPDATE projects SET name = 'Z', name_key = 'z', updated_at = ?", ("2026-10-05T00:00:00Z",))


def test_check_reports_missing_project_trigger(db):
    db.conn.execute("DROP TRIGGER projects_no_delete")
    assert db.check() == ["missing trigger projects_no_delete"]


def test_upgrade_v1_database_in_place(tmp_path):
    """A Phase 3/05 database (v1) gains the projects table and keeps its data."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path, isolation_level=None)
    for stmt in _split_sql(_schema_sql("schema.sql")):
        conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO tasks VALUES ('tsk_1', 'req_1', 'running', 'prj_toko', 'x', ?, ?)", (TS, TS)
    )
    conn.close()

    with Database(path) as db:
        assert db.schema_version() == 3  # v1 -> v2 -> v3 in one transaction
        assert db.check() == []
        assert TaskRepository(db).get("tsk_1").project_id == "prj_toko"
        ProjectRegistry(db).register(name="Toko", root_path="/w/toko", project_id="prj_toko")
    with Database(path) as db:  # reopening does not re-apply anything
        assert db.schema_version() == 3
        assert ProjectRegistry(db).get("prj_toko").name == "Toko"


def test_failed_upgrade_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path, isolation_level=None)
    for stmt in _split_sql(_schema_sql("schema.sql")):
        conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    import siamang.storage.database as dbmod

    real = dbmod._schema_sql
    monkeypatch.setattr(dbmod, "_schema_sql", lambda name: real(name) + "\nTHIS IS NOT SQL;" if name == "schema_v2.sql" else real(name))
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    monkeypatch.undo()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM sqlite_schema WHERE name = 'projects'").fetchone() is None
    conn.close()


def test_tasks_and_events_unaffected(db):
    # project_id on tasks stays a free reference at the storage level (no FK, Phase 10 K5);
    # the Task Engine validates it through ProjectRegistry.
    from tests.unit.test_storage import _row

    TaskRepository(db).insert(_row(project_id="prj_not_registered"))
    assert EventStore(db).count() == 0
