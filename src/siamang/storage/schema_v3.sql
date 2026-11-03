-- Siamang SQLite schema step v3 (Phase 10): tasks rebuilt for the Task Engine.
-- Applied once, after v2, in the same transaction as the version bump.
--
-- SQLite cannot alter CHECK constraints, so the v1 table is rebuilt:
-- create tasks_v3, copy, drop tasks, rename. Status names follow the
-- contract: accepted -> queued, waiting_approval -> waiting.
--
-- A v1 task without a project_id (or with an ID in the wrong format) cannot
-- be copied: the step fails, the transaction rolls back, and the database
-- stays at v2. Fix or remove such rows first.
--
-- Timestamps are fixed-width UTC with milliseconds (YYYY-MM-DDTHH:MM:SS.mmmZ),
-- so string order is time order (ORDER BY created_at, updated_at >= created_at).
-- v1 values are converted with strftime; an unparseable one becomes NULL and
-- fails NOT NULL, rolling the step back.
--
-- project_id has no foreign key (Phase 07 P11 / Phase 10 K5): the Task
-- Engine validates it through ProjectRegistry, and the index serves lookups.

CREATE TABLE tasks_v3 (
    task_id        TEXT PRIMARY KEY
                   CHECK (task_id GLOB 'tsk_?*' AND task_id NOT GLOB '*[^A-Za-z0-9_.:-]*'
                          AND length(task_id) <= 128),
    request_id     TEXT NOT NULL UNIQUE
                   CHECK (length(request_id) BETWEEN 1 AND 128
                          AND request_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
    project_id     TEXT NOT NULL
                   CHECK (project_id GLOB 'prj_?*' AND project_id NOT GLOB '*[^A-Za-z0-9_.:-]*'
                          AND length(project_id) <= 128),
    parent_task_id TEXT
                   CHECK (parent_task_id IS NULL
                          OR (parent_task_id GLOB 'tsk_?*' AND parent_task_id NOT GLOB '*[^A-Za-z0-9_.:-]*'
                              AND parent_task_id <> task_id)),
    title          TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    description    TEXT NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 10000),
    status         TEXT NOT NULL
                   CHECK (status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled')),
    priority       TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    source         TEXT NOT NULL CHECK (source IN ('cli', 'api', 'telegram', 'system', 'test')),
    retry_count    INTEGER NOT NULL CHECK (retry_count >= 0),
    max_retries    INTEGER NOT NULL CHECK (max_retries BETWEEN 0 AND 10),
    metadata       TEXT NOT NULL CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    last_error     TEXT CHECK (last_error IS NULL OR (json_valid(last_error) AND json_type(last_error) = 'object')),
    result         TEXT CHECK (result IS NULL OR (json_valid(result) AND json_type(result) = 'object')),
    created_at     TEXT NOT NULL
                   CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at     TEXT NOT NULL
                   CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'
                          AND updated_at >= created_at),
    started_at     TEXT
                   CHECK (started_at IS NULL OR started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    completed_at   TEXT
                   CHECK (completed_at IS NULL OR completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    failed_at      TEXT
                   CHECK (failed_at IS NULL OR failed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    cancelled_at   TEXT
                   CHECK (cancelled_at IS NULL OR cancelled_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    CHECK (retry_count <= max_retries),
    -- status <-> timestamp coherence
    CHECK (status NOT IN ('running', 'waiting', 'completed') OR started_at IS NOT NULL),
    CHECK (status <> 'completed' OR completed_at IS NOT NULL),
    CHECK (status <> 'failed' OR failed_at IS NOT NULL),
    CHECK (status <> 'cancelled' OR cancelled_at IS NOT NULL),
    CHECK (status = 'completed' OR completed_at IS NULL),
    CHECK (status = 'cancelled' OR cancelled_at IS NULL)
) STRICT;

INSERT INTO tasks_v3 (task_id, request_id, project_id, parent_task_id, title, description, status, priority,
                      source, retry_count, max_retries, metadata, last_error, result, created_at, updated_at,
                      started_at, completed_at, failed_at, cancelled_at)
SELECT task_id,
       request_id,
       project_id,
       NULL,
       substr(trim(replace(replace(replace(instruction, char(13), ' '), char(10), ' '), char(9), ' ')), 1, 200),
       instruction,
       CASE status WHEN 'accepted' THEN 'queued' WHEN 'waiting_approval' THEN 'waiting' ELSE status END,
       'normal',
       'system',
       0,
       3,
       '{"migrated_from":"schema_v1"}',
       NULL,
       NULL,
       strftime('%Y-%m-%dT%H:%M:%fZ', created_at),
       strftime('%Y-%m-%dT%H:%M:%fZ', updated_at),
       CASE WHEN status IN ('running', 'waiting_approval', 'completed') THEN strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) END,
       CASE WHEN status = 'completed' THEN strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) END,
       CASE WHEN status = 'failed' THEN strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) END,
       CASE WHEN status = 'cancelled' THEN strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) END
FROM tasks;

DROP TABLE tasks;

ALTER TABLE tasks_v3 RENAME TO tasks;

CREATE INDEX tasks_project_created ON tasks (project_id, created_at, task_id);

CREATE INDEX tasks_status_created ON tasks (status, created_at, task_id);

CREATE TRIGGER tasks_no_delete BEFORE DELETE ON tasks
BEGIN
    SELECT RAISE(ABORT, 'tasks are durable: DELETE is not allowed');
END;

CREATE TRIGGER tasks_immutable_identity BEFORE UPDATE ON tasks
WHEN NEW.task_id IS NOT OLD.task_id
  OR NEW.request_id IS NOT OLD.request_id
  OR NEW.project_id IS NOT OLD.project_id
  OR NEW.parent_task_id IS NOT OLD.parent_task_id
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'task identity (task_id, request_id, project_id, parent_task_id, created_at) is immutable');
END;
