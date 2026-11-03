-- Siamang SQLite schema, version 1 (Phase 3).
-- Applied once to a fresh database; PRAGMA user_version records the version.

CREATE TABLE tasks (
    task_id     TEXT PRIMARY KEY,
    request_id  TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL CHECK (status IN (
                    'accepted', 'running', 'waiting_approval',
                    'completed', 'failed', 'cancelled')),
    project_id  TEXT,
    instruction TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
) STRICT;

CREATE INDEX tasks_status ON tasks (status);

-- Append-only log of contract envelopes. Columns mirror the envelope
-- (contract field `type` -> event_type). `position` is the global append
-- order; `seq` is the per-task order used for replay.
CREATE TABLE events (
    position         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT NOT NULL UNIQUE,
    event_type       TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    request_id       TEXT NOT NULL,
    task_id          TEXT,
    seq              INTEGER CHECK (seq IS NULL OR seq >= 1),
    timestamp        TEXT NOT NULL,
    producer         TEXT NOT NULL,
    payload          TEXT NOT NULL CHECK (json_valid(payload)),
    recorded_at      TEXT NOT NULL,
    CHECK (seq IS NULL OR task_id IS NOT NULL),
    UNIQUE (task_id, seq)
) STRICT;

CREATE INDEX events_request ON events (request_id);

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: UPDATE is not allowed');
END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: DELETE is not allowed');
END;
