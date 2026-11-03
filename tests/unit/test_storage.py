import sqlite3
from datetime import UTC, datetime

import pytest

from siamang.bus import EventBus
from siamang.contracts import Envelope, new_event
from siamang.storage import (
    SCHEMA_VERSION,
    Database,
    DuplicateError,
    EventStore,
    NotFoundError,
    SchemaError,
    SequenceError,
    TaskRepository,
    TaskRow,
)

T0 = datetime(2026, 10, 4, 10, 15, tzinfo=UTC)


def _ev(seq, task_id="tsk_1", etype="task.progress", **kw):
    return new_event(etype, {"summary": f"step {seq}"}, request_id="req_1", task_id=task_id, seq=seq, **kw)


# --- database ------------------------------------------------------------


def test_fresh_database_schema(tmp_path):
    path = tmp_path / "nested" / "dir" / "s.db"
    with Database(path) as db:
        assert path.exists()
        assert db.schema_version() == SCHEMA_VERSION
        assert db.check() == []


def test_reopen_is_idempotent(tmp_path):
    Database(tmp_path / "s.db").close()
    with Database(tmp_path / "s.db") as db:
        assert db.check() == []


def test_newer_schema_rejected(tmp_path):
    path = tmp_path / "s.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(SchemaError):
        Database(path)


def test_check_detects_missing_trigger(db):
    db.conn.execute("DROP TRIGGER events_no_delete")
    assert db.check() == ["missing trigger events_no_delete"]


# --- tasks (storage only; lifecycle rules live in siamang.tasks) ----------
# Phase 10 replaced the Phase 3 TaskStore with the SQL-only TaskRepository.

TS = "2026-10-04T10:15:00.000Z"


def _row(**over):
    base = dict(task_id="tsk_1", request_id="req_1", project_id="prj_toko", parent_task_id=None, title="t",
                description="Tambahkan fitur inventory", status="queued", priority="normal", source="test",
                retry_count=0, max_retries=3, metadata="{}", last_error=None, result=None, created_at=TS,
                updated_at=TS, started_at=None, completed_at=None, failed_at=None, cancelled_at=None)
    base.update(over)
    return TaskRow(**base)


def test_insert_and_get_task(db):
    repo = TaskRepository(db)
    repo.insert(_row())
    assert repo.get("tsk_1") == _row()
    assert repo.get_by_request_id("req_1") == _row()
    assert repo.exists("tsk_1") and not repo.exists("tsk_missing")
    assert repo.get("tsk_missing") is None


def test_duplicate_task_or_request_id(db):
    repo = TaskRepository(db)
    repo.insert(_row())
    with pytest.raises(DuplicateError):
        repo.insert(_row(request_id="req_2"))
    with pytest.raises(DuplicateError):
        repo.insert(_row(task_id="tsk_2"))


def test_update_is_compare_and_set(db):
    repo = TaskRepository(db)
    repo.insert(_row())
    later = "2026-10-04T11:00:00.000Z"
    running = _row(status="running", started_at=later, updated_at=later)
    assert repo.update(running, expected=_row())
    assert repo.get("tsk_1") == running
    assert not repo.update(_row(status="cancelled", cancelled_at=later, updated_at=later), expected=_row())
    assert repo.get("tsk_1") == running  # stale expectation wrote nothing
    assert [r.task_id for r in repo.list(status="running")] == ["tsk_1"]
    assert repo.list(status="completed") == []


def test_list_order_filters_and_bounds(db):
    repo = TaskRepository(db)
    repo.insert(_row(task_id="tsk_b", request_id="req_b", created_at="2026-10-04T10:00:00.002Z",
                     updated_at="2026-10-04T10:00:00.002Z"))
    repo.insert(_row(task_id="tsk_a", request_id="req_a", project_id="prj_other",
                     created_at="2026-10-04T10:00:00.001Z", updated_at="2026-10-04T10:00:00.001Z"))
    assert [r.task_id for r in repo.list()] == ["tsk_a", "tsk_b"]
    assert [r.task_id for r in repo.list(project_id="prj_other")] == ["tsk_a"]
    assert [r.task_id for r in repo.list(limit=1, offset=1)] == ["tsk_b"]
    for bad in ({"limit": 0}, {"limit": 1001}, {"offset": -1}, {"limit": True}, {"limit": "5"}):
        with pytest.raises(ValueError):
            repo.list(**bad)


@pytest.mark.parametrize("over", [
    {"status": "bogus"}, {"status": "accepted"}, {"priority": "critical"}, {"source": "voice"},
    {"task_id": "t"}, {"task_id": "tsk_x\n"}, {"project_id": "x"}, {"project_id": None}, {"request_id": "bad id"},
    {"retry_count": -1}, {"max_retries": 11}, {"retry_count": 4, "max_retries": 3}, {"metadata": "[]"},
    {"metadata": "not json"}, {"last_error": "[1]"}, {"created_at": "2026-10-04T10:15:00Z"},
    {"updated_at": "2026-10-04T09:00:00.000Z"}, {"title": " "}, {"parent_task_id": "tsk_1"},
    {"status": "running"}, {"status": "completed", "started_at": TS}, {"status": "failed"},
    {"status": "cancelled"}, {"completed_at": TS},
])
def test_task_check_constraints_in_db(db, over):
    with pytest.raises(sqlite3.IntegrityError):
        TaskRepository(db).insert(_row(**over))


def test_tasks_are_durable_and_identity_is_immutable(db):
    TaskRepository(db).insert(_row())
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("DELETE FROM tasks")
    for col, value in [("task_id", "tsk_2"), ("project_id", "prj_other"), ("request_id", "req_9"),
                       ("created_at", "2026-10-04T10:15:01.000Z")]:
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(f"UPDATE tasks SET {col} = ?", (value,))


# --- events --------------------------------------------------------------


def test_store_and_retrieve_event(db):
    store = EventStore(db)
    ev = _ev(1)
    position = store.append(ev)
    assert position == 1
    assert store.get(ev.event_id) == ev
    assert store.get("evt_missing") is None
    assert store.count() == 1


def test_store_all_examples(db, examples):
    store = EventStore(db)
    # examples are standalone; they share one task, so store them in seq order
    # and skip the doc's duplicate seq 6 (see examples/README.md)
    seen = set()
    for name, data in sorted(examples.items(), key=lambda kv: (kv[1].get("seq") or 0, kv[0])):
        env = Envelope.from_dict(data)
        if env.seq is not None and env.seq in seen:
            continue
        seen.add(env.seq)
        store.append(env)
        assert store.get(env.event_id).to_dict() == data


def test_duplicate_event_id(db):
    store = EventStore(db)
    ev = _ev(1)
    store.append(ev)
    with pytest.raises(DuplicateError):
        store.append(ev)


def test_seq_must_increase(db):
    store = EventStore(db)
    store.append(_ev(1))
    store.append(_ev(3))  # gaps allowed, order enforced
    with pytest.raises(SequenceError):
        store.append(_ev(3))
    with pytest.raises(SequenceError):
        store.append(_ev(2))
    assert store.last_seq("tsk_1") == 3
    assert store.next_seq("tsk_1") == 4
    assert store.next_seq("tsk_other") == 1


def test_seq_is_per_task(db):
    store = EventStore(db)
    store.append(_ev(1, task_id="tsk_a"))
    store.append(_ev(1, task_id="tsk_b"))
    assert store.count() == 2


def test_append_only_update_blocked(db):
    store = EventStore(db)
    store.append(_ev(1))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.conn.execute("UPDATE events SET payload = '{}'")


def test_append_only_delete_blocked(db):
    store = EventStore(db)
    store.append(_ev(1))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.conn.execute("DELETE FROM events")
    assert store.count() == 1


def test_store_has_no_update_api(db):
    store = EventStore(db)
    assert not any(hasattr(store, n) for n in ("update", "delete", "remove", "edit"))


def test_failed_append_leaves_no_row(db):
    store = EventStore(db)
    store.append(_ev(2))
    with pytest.raises(SequenceError):
        store.append(_ev(1))
    assert store.count() == 1


def test_list_ordering_and_filters(db):
    store = EventStore(db)
    req = new_event("approval.granted", {"approval_id": "apr_1", "decided_by": "dewi", "channel": "text"},
                    request_id="req_1", task_id="tsk_1", producer="web-client")
    store.append(_ev(1))
    store.append(req)
    store.append(_ev(2))
    store.append(_ev(1, task_id="tsk_2"))
    assert [e.seq for e in store.list(task_id="tsk_1")] == [1, None, 2]
    assert len(store.list(request_id="req_1")) == 4
    assert len(store.list(limit=2)) == 2


def test_bus_to_store(db):
    bus, store = EventBus(), EventStore(db)
    store.attach(bus)
    bus.publish(_ev(1))
    bus.publish(_ev(2))
    assert [e.seq for e in store.replay("tsk_1")] == [1, 2]


def test_store_failure_surfaces_through_bus(db):
    bus, store, later = EventBus(), EventStore(db), []
    store.attach(bus)
    bus.subscribe("*", later.append)
    bus.publish(_ev(1))
    with pytest.raises(SequenceError):
        bus.publish(_ev(1))
    assert len(later) == 1  # the second subscriber never saw the rejected event
