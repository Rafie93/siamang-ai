-- Siamang SQLite schema step 2 (Phase 07): project registry.
-- Applied after schema.sql (v1) to fresh databases, or alone to upgrade an
-- existing v1 database. Earlier steps are never edited.

CREATE TABLE projects (
    project_id   TEXT PRIMARY KEY CHECK (project_id GLOB 'prj_?*'),
    name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100 AND name = trim(name)),
    name_key     TEXT NOT NULL CHECK (length(name_key) BETWEEN 1 AND 100),
    description  TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
    project_type TEXT NOT NULL CHECK (project_type IN (
                     'software', 'web', 'mobile', 'desktop', 'game',
                     'research', 'data', 'education', 'other')),
    status       TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    root_path    TEXT NOT NULL CHECK (substr(root_path, 1, 1) = '/' AND length(root_path) <= 4096),
    metadata     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
) STRICT;

-- Names and root paths are unique among ACTIVE projects only; archived
-- projects keep their values as history.
CREATE UNIQUE INDEX projects_active_name ON projects (name_key) WHERE status = 'active';
CREATE UNIQUE INDEX projects_active_root ON projects (root_path) WHERE status = 'active';
CREATE INDEX projects_status ON projects (status);

-- Identity is durable: projects are archived, never deleted.
CREATE TRIGGER projects_no_delete BEFORE DELETE ON projects
BEGIN
    SELECT RAISE(ABORT, 'projects are archived, not deleted');
END;

CREATE TRIGGER projects_immutable_identity BEFORE UPDATE OF project_id, created_at ON projects
BEGIN
    SELECT RAISE(ABORT, 'project_id and created_at are immutable');
END;
