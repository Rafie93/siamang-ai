from datetime import UTC, datetime, timedelta

from siamang.contracts import new_event
from siamang.storage import Database, EventStore

T0 = datetime(2026, 10, 4, 10, 15, tzinfo=UTC)

STREAM = [
    ("task.accepted", {"status": "queued", "summary": "Diterima."}),
    ("task.started", {"summary": "Mulai."}),
    ("task.progress", {"summary": "Setengah.", "percent": 50}),
    ("approval.required", {"approval_id": "apr_1", "action": "apply_changes", "risk": "medium",
                           "question": "Boleh?", "expires_at": "2026-10-04T10:30:00Z"}),
    ("task.waiting_approval", {"approval_id": "apr_1", "summary": "Menunggu."}),
    ("task.completed", {"summary": "Selesai.", "outcome": "success"}),
]


def _store_stream(store, task_id="tsk_1"):
    events = []
    for i, (etype, payload) in enumerate(STREAM):
        ev = new_event(etype, payload, request_id="req_1", task_id=task_id,
                       seq=store.next_seq(task_id), timestamp=T0 + timedelta(seconds=i),
                       event_id=f"evt_{task_id}_{i}")
        store.append(ev)
        events.append(ev)
    return events


def test_replay_sequence(db):
    store = EventStore(db)
    stored = _store_stream(store)
    replayed = list(store.replay("tsk_1"))
    assert replayed == stored
    assert [e.seq for e in replayed] == [1, 2, 3, 4, 5, 6]
    assert [str(e.type) for e in replayed] == [t for t, _ in STREAM]


def test_replay_after_seq(db):
    store = EventStore(db)
    _store_stream(store)
    assert [e.seq for e in store.replay("tsk_1", after_seq=4)] == [5, 6]
    assert list(store.replay("tsk_1", after_seq=6)) == []
    assert list(store.replay("tsk_unknown")) == []


def test_replay_orders_by_seq_not_insertion(db):
    store = EventStore(db)
    # interleave two tasks; each task's replay must be its own seq order
    for i in range(1, 4):
        for t in ("tsk_a", "tsk_b"):
            store.append(new_event("task.progress", {"summary": f"{t} {i}"}, request_id="req_1", task_id=t, seq=i))
    assert [e.payload["summary"] for e in store.replay("tsk_b")] == ["tsk_b 1", "tsk_b 2", "tsk_b 3"]


def test_replay_excludes_unsequenced_inbound(db):
    store = EventStore(db)
    _store_stream(store)
    store.append(new_event("task.cancel", {"reason": ""}, request_id="req_1", task_id="tsk_1", producer="web-client"))
    assert len(list(store.replay("tsk_1"))) == 6
    assert len(store.list(task_id="tsk_1")) == 7


def test_replay_deterministic_across_reopen(tmp_path):
    path = tmp_path / "s.db"
    with Database(path) as db:
        first = [e.to_json() for e in (_store_stream(EventStore(db)))]
    runs = []
    for _ in range(3):
        with Database(path) as db:
            runs.append([e.to_json() for e in EventStore(db).replay("tsk_1")])
    assert runs[0] == runs[1] == runs[2] == first


def test_replay_is_read_only(db):
    store = EventStore(db)
    _store_stream(store)
    before = store.count()
    list(store.replay("tsk_1"))
    list(store.replay("tsk_1"))
    assert store.count() == before
