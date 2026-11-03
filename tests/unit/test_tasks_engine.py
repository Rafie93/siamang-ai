"""TaskEngine: creation, lifecycle, retry, persistence, atomicity, recovery, bus, concurrency."""

import threading
from datetime import timedelta

import pytest

from siamang.bus import ALL, EventBus
from siamang.contracts import EventType
from siamang.projects import ProjectRegistry
from siamang.storage import Database, EventStore
from siamang.storage.events import EventStore as EventStoreCls
from siamang.tasks import (
    DuplicateTaskError,
    InvalidTaskError,
    InvalidTransitionError,
    NotRetryableError,
    RetryLimitReachedError,
    TaskEngine,
    TaskErrorCode,
    TaskFailure,
    TaskNotFoundError,
    TaskProjectArchivedError,
    TaskProjectNotFoundError,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from tests.support.projects import T0, Clock
from tests.support.tasks import make_engine, request

S = TaskStatus


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def setup(db, clock):
    return make_engine(db, clock=clock)


@pytest.fixture
def engine(setup):
    return setup[0]


@pytest.fixture
def project(setup):
    return setup[1]


def new_task(engine, project, **over):
    return engine.create(request(project, **over)).task


def statuses(engine, task_id):
    return [(e.seq, str(e.type), e.payload.get("status")) for e in engine.history(task_id)]


# --- creation -------------------------------------------------------------------------


def test_create(engine, project, db):
    sub = engine.create(request(project, title="Inventory", priority="high", metadata={"language": "python"}))
    t = sub.task
    assert not sub.duplicate
    assert t.task_id.startswith("tsk_") and t.request_id.startswith("req_")
    assert (t.status, t.priority, t.source, t.title) == (S.QUEUED, "high", "test", "Inventory")
    assert (t.retry_count, t.max_retries, t.parent_task_id) == (0, 3, None)
    assert dict(t.metadata) == {"language": "python"}
    assert t.created_at == t.updated_at == T0 and t.started_at is None
    assert engine.get(t.task_id) == t
    (accepted,) = engine.history(t.task_id)
    assert (accepted.type, accepted.seq, accepted.request_id) == (EventType.TASK_ACCEPTED, 1, t.request_id)
    assert accepted.payload["project"] == {"id": project.project_id, "name": project.name}
    assert accepted.payload["status"] == "queued"


def test_create_requires_explicit_source_and_project(engine, project):
    with pytest.raises(TypeError):
        TaskRequest(project_id=project.project_id, description="d")  # source has no default
    with pytest.raises(InvalidTaskError):
        engine.create({"project_id": project.project_id, "description": "d", "source": "cli"})
    for bad in ({"project_id": "prj_x\n"}, {"project_id": "/work/toko"}, {"source": "voice"},
                {"priority": "p0"}, {"max_retries": 99}, {"description": " "}, {"metadata": {"command": "x"}},
                {"metadata": {"api_key": "x"}}, {"task_id": "tsk_x\n"}, {"request_id": "a b"}):
        with pytest.raises(InvalidTaskError):
            engine.create(request(project, **bad))


def test_unknown_and_archived_project(engine, project, db):
    with pytest.raises(TaskProjectNotFoundError) as info:
        engine.create(request(project, project_id="prj_missing"))
    assert info.value.code is TaskErrorCode.PROJECT_NOT_FOUND
    ProjectRegistry(db).archive(project.project_id)
    with pytest.raises(TaskProjectArchivedError):
        engine.create(request(project))
    assert not ProjectRegistry(db).get(project.project_id).is_active  # not restored
    assert engine.list() == [] and EventStore(db).count() == 0  # nothing written


def test_duplicate_task_id(engine, project):
    new_task(engine, project, task_id="tsk_fixed")
    with pytest.raises(DuplicateTaskError) as info:
        new_task(engine, project, task_id="tsk_fixed")
    assert info.value.field == "task_id"


def test_request_id_is_idempotent(engine, project, db):
    first = engine.create(request(project, request_id="req_client_1"))
    again = engine.create(request(project, request_id="req_client_1"))
    assert again.duplicate and again.task == first.task
    assert len(engine.list()) == 1 and EventStore(db).count() == 1  # nothing new written
    with pytest.raises(DuplicateTaskError):
        engine.create(request(project, request_id="req_client_1", description="something else"))


def test_parent_task(engine, project, db):
    parent = new_task(engine, project)
    child = new_task(engine, project, parent_task_id=parent.task_id)
    assert child.parent_task_id == parent.task_id
    other_project = ProjectRegistry(db).register(name="Other", root_path="/work/other")
    with pytest.raises(InvalidTaskError):
        new_task(engine, other_project, parent_task_id=parent.task_id)
    with pytest.raises(InvalidTaskError):
        new_task(engine, project, parent_task_id="tsk_missing")


def test_engine_requires_shared_database(db, tmp_path):
    with pytest.raises(ValueError):
        TaskEngine(db, ProjectRegistry(Database(tmp_path / "other.db")))
    with pytest.raises(TypeError):
        TaskEngine(db, object())


# --- lifecycle ------------------------------------------------------------------------


def test_full_lifecycle_and_timestamps(engine, project, clock):
    t = new_task(engine, project)
    clock.advance(1)
    running = engine.start(t.task_id)
    assert running.status is S.RUNNING and running.started_at == T0 + timedelta(seconds=1)
    clock.advance(1)
    assert engine.wait(t.task_id, reason="needs input").status is S.WAITING
    clock.advance(1)
    resumed = engine.start(t.task_id)
    assert resumed.started_at == running.started_at  # resume keeps the start time
    clock.advance(1)
    done = engine.complete(t.task_id, TaskResult("Selesai", result={"tests": {"passed": 4, "failed": 0}}))
    assert done.status is S.COMPLETED and done.completed_at == T0 + timedelta(seconds=4)
    assert done.result.result["tests"]["passed"] == 4
    assert statuses(engine, t.task_id) == [(1, "task.accepted", "queued"), (2, "task.started", "running"),
                                            (3, "task.waiting", "waiting"), (4, "task.started", "running"),
                                            (5, "task.completed", "completed")]
    assert engine.history(t.task_id)[2].payload["reason"] == "needs input"


def test_waiting_requeue(engine, project):
    t = new_task(engine, project)
    engine.start(t.task_id)
    engine.wait(t.task_id)
    assert engine.requeue(t.task_id).status is S.QUEUED
    assert statuses(engine, t.task_id)[-1] == (4, "task.requeued", "queued")


@pytest.mark.parametrize("path", [["cancel"], ["start", "cancel"], ["start", "wait", "cancel"]])
def test_cancel(engine, project, path):
    t = new_task(engine, project)
    for step in path[:-1]:
        getattr(engine, step)(t.task_id)
    c = engine.cancel(t.task_id, reason="user changed mind")
    assert c.status is S.CANCELLED and c.cancelled_at is not None
    assert engine.history(t.task_id)[-1].payload["summary"] == "user changed mind"


@pytest.mark.parametrize("prep, action", [
    (["start", "complete"], "start"), (["start", "complete"], "requeue"), (["cancel"], "start"),
    (["fail"], "start"), ([], "complete"), ([], "wait"), (["start", "complete"], "cancel"),
    (["cancel"], "retry"), ([], "retry"), (["start"], "retry"),
])
def test_invalid_transitions_change_nothing(engine, project, prep, action, db):
    t = new_task(engine, project)
    for step in prep:
        if step == "complete":
            engine.complete(t.task_id, TaskResult("ok"))
        elif step == "fail":
            engine.fail(t.task_id, TaskFailure("X", retryable=True))
        else:
            getattr(engine, step)(t.task_id)
    before, events = engine.get(t.task_id), EventStore(db).count()
    with pytest.raises(InvalidTransitionError):
        if action == "complete":
            engine.complete(t.task_id, TaskResult("ok"))
        else:
            getattr(engine, action)(t.task_id)
    assert engine.get(t.task_id) == before and EventStore(db).count() == events


def test_unknown_or_malformed_task(engine):
    with pytest.raises(TaskNotFoundError):
        engine.start("tsk_missing")
    for bad in ("tsk_x\n", "1", "", None):
        with pytest.raises(InvalidTaskError):
            engine.get(bad)


# --- failure --------------------------------------------------------------------------


def test_structured_failure(engine, project, clock):
    t = new_task(engine, project)
    engine.start(t.task_id)
    clock.advance(5)
    f = engine.fail(t.task_id, TaskFailure("QA_FAILED", "3 tests failed", retryable=True, step="qa",
                                           details={"failed": 3}))
    assert f.status is S.FAILED and f.failed_at == T0 + timedelta(seconds=5)
    assert f.last_error.to_dict() == {"code": "QA_FAILED", "message": "3 tests failed", "retryable": True,
                                      "step": "qa", "details": {"failed": 3}}
    event = engine.history(t.task_id)[-1]
    assert event.type is EventType.TASK_FAILED and event.payload["error"]["code"] == "QA_FAILED"


def test_failure_from_exception_is_sanitized(engine, project, db):
    t = new_task(engine, project)
    engine.fail(t.task_id, RuntimeError("password=hunter2 /Users/x/.env"), step=2)
    err = engine.get(t.task_id).last_error
    assert (err.code, err.retryable, err.step, dict(err.details)) == ("INTERNAL", False, 2, {"cause": "RuntimeError"})
    raw = db.path.read_bytes()
    assert b"hunter2" not in raw and b".env" not in raw
    with pytest.raises(InvalidTaskError):
        engine.fail(t.task_id, "just a string")


# --- retry ----------------------------------------------------------------------------


def test_retry_increments_and_requeues(engine, project):
    t = new_task(engine, project, max_retries=2)
    engine.fail(t.task_id, TaskFailure("MODEL_UNAVAILABLE", retryable=True))
    r = engine.retry(t.task_id)
    assert (r.status, r.retry_count, r.retries_left) == (S.QUEUED, 1, 1)
    assert r.last_error.code == "MODEL_UNAVAILABLE"  # kept as history
    e = engine.history(t.task_id)[-1]
    assert (e.type, e.payload["retry_count"], e.payload["max_retries"]) == (EventType.TASK_RETRY_REQUESTED, 1, 2)


def test_retry_limit(engine, project, db):
    t = new_task(engine, project, max_retries=1)
    engine.fail(t.task_id, TaskFailure("X", retryable=True))
    engine.retry(t.task_id)
    engine.start(t.task_id)
    engine.fail(t.task_id, TaskFailure("X", retryable=True))
    before, events = engine.get(t.task_id), EventStore(db).count()
    with pytest.raises(RetryLimitReachedError):
        engine.retry(t.task_id)
    assert engine.get(t.task_id) == before and EventStore(db).count() == events


def test_no_retries_allowed(engine, project):
    t = new_task(engine, project, max_retries=0)
    engine.fail(t.task_id, TaskFailure("X", retryable=True))
    with pytest.raises(RetryLimitReachedError):
        engine.retry(t.task_id)


def test_non_retryable_failure_is_not_retried(engine, project):
    t = new_task(engine, project)
    engine.fail(t.task_id, TaskFailure("APPROVAL_REJECTED", retryable=False))
    with pytest.raises(NotRetryableError):
        engine.retry(t.task_id)
    assert engine.get(t.task_id).status is S.FAILED


def test_retry_is_explicit_only(engine, project, clock):
    t = new_task(engine, project)
    engine.fail(t.task_id, TaskFailure("X", retryable=True))
    clock.advance(3600)
    engine.list()
    engine.recovery()
    assert engine.get(t.task_id).status is S.FAILED  # nothing retries on its own


# --- persistence / restart ------------------------------------------------------------------


def test_tasks_and_events_survive_restart(tmp_path, clock):
    path = tmp_path / "s.db"
    with Database(path) as db:
        engine, project = make_engine(db, clock=clock)
        t = new_task(engine, project)
        engine.start(t.task_id)
        engine.fail(t.task_id, TaskFailure("X", "m", retryable=True))
        expected = engine.get(t.task_id)
        expected_events = statuses(engine, t.task_id)
    with Database(path) as db:
        engine = TaskEngine(db, ProjectRegistry(db), clock=clock)
        assert engine.get(t.task_id) == expected
        assert statuses(engine, t.task_id) == expected_events
        assert engine.retry(t.task_id).retry_count == 1  # lifecycle continues after restart


def test_running_task_after_restart_is_reported_not_touched(tmp_path, clock):
    path = tmp_path / "s.db"
    with Database(path) as db:
        engine, project = make_engine(db, clock=clock)
        running = engine.start(new_task(engine, project).task_id)
        waiting = new_task(engine, project, description="second")
        engine.start(waiting.task_id)
        engine.wait(waiting.task_id)
        queued = new_task(engine, project, description="third")
        done = new_task(engine, project, description="fourth")
        engine.cancel(done.task_id)
    with Database(path) as db:  # "crash" and restart
        engine = TaskEngine(db, ProjectRegistry(db), clock=clock)
        events_before = EventStore(db).count()
        report = engine.recovery()
        assert [t.task_id for t in report.interrupted] == [running.task_id]
        assert [t.task_id for t in report.waiting] == [waiting.task_id]
        assert [t.task_id for t in report.queued] == [queued.task_id]
        assert engine.get(running.task_id) == running  # still running: not completed, failed, or retried
        assert EventStore(db).count() == events_before
        # the explicit way out of an interrupted run
        engine.fail(running.task_id, TaskFailure("INTERRUPTED", "process restarted", retryable=True))
        assert engine.retry(running.task_id).status is S.QUEUED


# --- atomicity -------------------------------------------------------------------------------


def test_event_failure_rolls_back_state(engine, project, db, monkeypatch):
    t = new_task(engine, project)
    before, events = engine.get(t.task_id), EventStore(db).count()

    def broken(self, event):
        raise OSError("disk full")

    monkeypatch.setattr(EventStoreCls, "append", broken)
    with pytest.raises(OSError):
        engine.start(t.task_id)
    monkeypatch.undo()
    assert engine.get(t.task_id) == before and EventStore(db).count() == events
    assert engine.start(t.task_id).status is S.RUNNING  # and it still works afterwards


def test_create_failure_leaves_no_task(engine, project, db, monkeypatch):
    monkeypatch.setattr(EventStoreCls, "append", lambda self, e: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        new_task(engine, project, task_id="tsk_ghost")
    monkeypatch.undo()
    assert not engine.exists("tsk_ghost") and EventStore(db).count() == 0


def test_invalid_event_payload_rolls_back(engine, project, db):
    t = new_task(engine, project)
    engine.start(t.task_id)
    with pytest.raises(Exception):
        engine.wait(t.task_id, summary="   ")  # contract: summary must be non-empty
    assert engine.get(t.task_id).status is S.RUNNING
    assert len(engine.history(t.task_id)) == 2


def test_state_change_without_event_is_impossible(engine, project, db):
    """Invariant over a whole run: one event per state, last event's status == task status."""
    t = new_task(engine, project)
    for op in ("start", "wait", "requeue", "start"):
        getattr(engine, op)(t.task_id)
    engine.fail(t.task_id, TaskFailure("X", retryable=True))
    engine.retry(t.task_id)
    history = engine.history(t.task_id)
    assert [e.seq for e in history] == list(range(1, len(history) + 1))
    assert history[-1].payload["status"] == str(engine.get(t.task_id).status)


def test_clock_skew_never_moves_updated_at_backwards(engine, project, clock):
    t = new_task(engine, project)
    clock.now = T0 - timedelta(hours=1)
    assert engine.start(t.task_id).updated_at == T0


# --- bus ------------------------------------------------------------------------------------


def test_bus_notified_after_commit_only(db, clock, monkeypatch):
    bus = EventBus()
    seen = []
    bus.subscribe(ALL, lambda e: seen.append((str(e.type), db.conn.in_transaction)))
    engine, project = make_engine(db, clock=clock, bus=bus)
    t = new_task(engine, project)
    engine.start(t.task_id)
    assert seen == [("task.accepted", False), ("task.started", False)]  # outside the transaction
    monkeypatch.setattr(EventStoreCls, "append", lambda self, e: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        engine.cancel(t.task_id)
    assert len(seen) == 2  # a rolled-back event is never published


def test_subscriber_error_does_not_undo_committed_state(db, clock):
    bus = EventBus()

    def bad(event):
        raise RuntimeError("subscriber bug")

    bus.subscribe(ALL, bad)
    engine, project = make_engine(db, clock=clock, bus=bus)
    t = new_task(engine, project)
    assert engine.start(t.task_id).status is S.RUNNING
    assert len(engine.history(t.task_id)) == 2


# --- concurrency ---------------------------------------------------------------------------


def test_concurrent_start_only_one_wins(tmp_path):
    path = tmp_path / "s.db"
    with Database(path) as db:
        engine, project = make_engine(db)
        task_id = new_task(engine, project).task_id
    barrier = threading.Barrier(4, timeout=20)  # a broken worker fails the test instead of hanging it
    outcomes = []

    def worker():
        try:
            with Database(path) as db:  # one connection per thread, like separate processes
                e = TaskEngine(db, ProjectRegistry(db))
                barrier.wait()
                try:
                    e.start(task_id)
                    outcomes.append("ok")
                except InvalidTransitionError:
                    outcomes.append("invalid_transition")
        except Exception as exc:  # recorded, so the assertion below shows it
            outcomes.append(f"error: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert sorted(outcomes) == ["invalid_transition"] * 3 + ["ok"]
    with Database(path) as db:
        e = TaskEngine(db, ProjectRegistry(db))
        assert [s for _, s, _ in statuses(e, task_id)] == ["task.accepted", "task.started"]


# --- queries -----------------------------------------------------------------------------


def test_list_filters_and_pagination(engine, project, db, clock):
    other = ProjectRegistry(db).register(name="Other", root_path="/work/other")
    ids = []
    for i in range(5):
        clock.advance(1)
        ids.append(new_task(engine, project if i % 2 == 0 else other, description=f"t{i}").task_id)
    engine.start(ids[0])
    assert [t.task_id for t in engine.list()] == ids
    assert [t.task_id for t in engine.list(project_id=project.project_id)] == ids[0::2]
    assert [t.task_id for t in engine.list(status="running")] == [ids[0]]
    assert [t.task_id for t in engine.list(limit=2, offset=1)] == ids[1:3]
    assert engine.list(project_id="prj_nobody") == []
    for bad in ({"status": "accepted"}, {"project_id": "prj_x\n"}, {"limit": 0}, {"limit": 5000}, {"offset": -1}):
        with pytest.raises(InvalidTaskError):
            engine.list(**bad)


def test_list_is_a_single_query(engine, project, db):
    for i in range(10):
        new_task(engine, project, description=f"t{i}")
    statements = []
    db.conn.set_trace_callback(statements.append)
    try:
        tasks = engine.list()
    finally:
        db.conn.set_trace_callback(None)
    assert len(tasks) == 10 and len(statements) == 1  # no N+1 (no per-task project lookups)
