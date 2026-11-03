"""Append-only event store + replay.

* ``append`` is the only write. UPDATE/DELETE are blocked by SQLite triggers.
* Outbound (Siamang-produced) events carry a per-task ``seq``; ``append``
  requires it to be strictly greater than the task's last ``seq``.
* ``replay(task_id)`` returns the task's sequenced stream ordered by ``seq``
  (the contract's ``GET /v0/tasks/{id}/events?after_seq=N`` semantics).
  Inbound messages (no ``seq``) are stored and listed but not part of replay.
* Replay is read-only: it re-validates and returns envelopes, nothing else.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

from siamang.bus import ALL, EventBus, Subscription
from siamang.contracts import Envelope
from siamang.contracts.envelope import format_timestamp, utc_now
from siamang.log import get_logger
from siamang.storage.database import Database, DuplicateError, SequenceError

log = get_logger("storage.events")


def _row_to_envelope(row: sqlite3.Row) -> Envelope:
    data = {
        "contract_version": row["contract_version"],
        "type": row["event_type"],
        "event_id": row["event_id"],
        "request_id": row["request_id"],
        "timestamp": row["timestamp"],
        "producer": row["producer"],
        "payload": json.loads(row["payload"]),
    }
    if row["task_id"] is not None:
        data["task_id"] = row["task_id"]
    if row["seq"] is not None:
        data["seq"] = row["seq"]
    return Envelope.from_dict(data)


class EventStore:
    def __init__(self, db: Database):
        self.db = db

    def append(self, event: Envelope) -> int:
        """Persist one event. Returns its global ``position``."""
        if not isinstance(event, Envelope):
            raise TypeError(f"EventStore.append expects Envelope, got {type(event).__name__}")
        d = event.to_dict()
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM events WHERE event_id = ?", (event.event_id,)).fetchone():
                raise DuplicateError(f"event already stored: {event.event_id}")
            if event.seq is not None:
                last = self._last_seq(conn, event.task_id)
                if event.seq <= last:
                    raise SequenceError(
                        f"seq {event.seq} for task {event.task_id} must be greater than last seq {last}"
                    )
            cur = conn.execute(
                "INSERT INTO events (event_id, event_type, contract_version, request_id, task_id, seq,"
                " timestamp, producer, payload, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    str(event.type),
                    event.contract_version,
                    event.request_id,
                    event.task_id,
                    event.seq,
                    d["timestamp"],
                    event.producer,
                    json.dumps(d["payload"], ensure_ascii=False, separators=(",", ":")),
                    format_timestamp(utc_now()),
                ),
            )
        log.debug("stored %s seq=%s at position %d", event.type, event.seq, cur.lastrowid)
        return cur.lastrowid

    def attach(self, bus: EventBus) -> Subscription:
        """Persist every event published on ``bus`` (Event -> Bus -> Store)."""
        return bus.subscribe(ALL, self.append)

    def get(self, event_id: str) -> Envelope | None:
        row = self.db.conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return _row_to_envelope(row) if row else None

    def list(self, *, task_id: str | None = None, request_id: str | None = None, limit: int | None = None) -> list[Envelope]:
        """All matching events (inbound + outbound) in append order."""
        sql, params = "SELECT * FROM events", []
        where = []
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        if request_id is not None:
            where.append("request_id = ?")
            params.append(request_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY position"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_row_to_envelope(r) for r in self.db.conn.execute(sql, params).fetchall()]

    def last_seq(self, task_id: str) -> int:
        return self._last_seq(self.db.conn, task_id)

    def next_seq(self, task_id: str) -> int:
        return self.last_seq(task_id) + 1

    def replay(self, task_id: str, *, after_seq: int = 0) -> Iterator[Envelope]:
        """Yield the task's sequenced events with ``seq > after_seq``, ordered by ``seq``."""
        rows = self.db.conn.execute(
            "SELECT * FROM events WHERE task_id = ? AND seq IS NOT NULL AND seq > ? ORDER BY seq",
            (task_id, after_seq),
        ).fetchall()
        for row in rows:
            yield _row_to_envelope(row)

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    @staticmethod
    def _last_seq(conn: sqlite3.Connection, task_id: str | None) -> int:
        row = conn.execute("SELECT MAX(seq) FROM events WHERE task_id = ?", (task_id,)).fetchone()
        return row[0] or 0
