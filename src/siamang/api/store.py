"""API schema v7, additive to core schema v3. All state changes join core transactions."""
import hashlib
import hmac
import json
from datetime import UTC, datetime

from siamang.ids import new_id

from .codec import decode


def now():
    return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


_PROJECT_OF_TASK = '(SELECT project_id FROM tasks WHERE task_id=NEW.task_id)'
_TASK_OF_RUN = '(SELECT task_id FROM api_runs WHERE run_id=NEW.run_id)'
_PROJECT_OF_RUN = ('(SELECT t.project_id FROM api_runs r JOIN tasks t ON t.task_id=r.task_id '
                   'WHERE r.run_id=NEW.run_id)')
_WRITES = ('INSERT', 'UPDATE')
# Every write a reader may need to see: (table, kind, project, task, ref, trigger events). A trigger cannot be forgotten
# at a call site, joins the writer's transaction so a rollback leaves no row, and sees writes from the CLI too.
# A task's state and its run's phase always commit with an event, so ``tasks`` and most of ``api_runs`` need no row here.
# A heartbeat names neither bundle, report, nor decision, so it adds nothing: it is carried by the hub instead.
WATCHED_WRITES = (
    ('events', 'lifecycle', _PROJECT_OF_TASK, 'NEW.task_id', 'NEW.event_id', ('INSERT',)),
    ('api_run_events', 'execution', _PROJECT_OF_TASK, 'NEW.task_id', 'NEW.position', ('INSERT',)),
    ('api_runs', 'reports', _PROJECT_OF_TASK, 'NEW.task_id', 'NULL', ('UPDATE OF bundle, report, decision',)),
    ('api_documents', 'documents', _PROJECT_OF_TASK, 'NEW.task_id', 'NULL', ('INSERT',)),
    ('api_document_revisions', 'documents', _PROJECT_OF_TASK, 'NEW.task_id', 'NULL', ('INSERT',)),  # with every owner edit
    ('api_diagram_proposals', 'documents', _PROJECT_OF_TASK, 'NEW.task_id', 'NULL', _WRITES),
    ('api_design_parts', 'task', _PROJECT_OF_RUN, _TASK_OF_RUN, 'NULL', _WRITES),
    ('api_work_packages', 'task', _PROJECT_OF_RUN, _TASK_OF_RUN, 'NULL', _WRITES),
    ('api_task_branches', 'task', 'NEW.project_id', 'NEW.task_id', 'NULL', _WRITES),
    ('api_task_ci', 'task', _PROJECT_OF_TASK, 'NEW.task_id', 'NULL', ('INSERT',)),  # written with INSERT OR REPLACE
    ('api_autonomy_decisions', 'autonomy', _PROJECT_OF_TASK, 'NEW.task_id', 'NULL', ('INSERT',)),
    ('api_messages', 'messages', 'NEW.project_id', 'NEW.task_id', 'NULL', ('INSERT',)),  # no task: the project's intake
    ('api_task_drafts', 'drafts', 'NEW.project_id', 'NULL', 'NULL', _WRITES),
    ('api_project_repos', 'project', 'NEW.project_id', 'NULL', 'NULL', _WRITES),
    ('api_project_specs', 'project', 'NEW.project_id', 'NULL', 'NULL', _WRITES),
    ('api_features', 'project', 'NEW.project_id', 'NULL', 'NULL', ('INSERT',)),
    ('projects', 'project', 'NEW.project_id', 'NULL', 'NULL', _WRITES),
)
CHANGE_RETENTION = 10_000  # enough to resume after a dropped connection; further behind, a reader reloads instead


# Whose edit produced exactly this text, so a retry that reuses the document keeps the mark and a model's rewrite loses it.
_EDITED_BY = ('(SELECT r.edited_by FROM api_document_revisions r WHERE r.task_id=d.task_id AND r.kind=d.kind '
              'AND r.content_sha256=d.content_sha256 AND r.diagram IS NOT NULL ORDER BY r.position DESC LIMIT 1) AS edited_by')


class RunStore:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        with self.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS api_schema (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL) STRICT')
            conn.execute('INSERT OR IGNORE INTO api_schema VALUES (1,1)')
            if conn.execute('SELECT version FROM api_schema WHERE singleton=1').fetchone()[0] not in (1, 2, 3, 4, 5, 6, 7):
                raise ValueError('unsupported API schema version')
            conn.execute('UPDATE api_schema SET version=7 WHERE singleton=1')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_runs (
                run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, state TEXT NOT NULL,
                phase TEXT NOT NULL, iteration INTEGER, wait_reason TEXT,
                heartbeat_at TEXT, updated_at TEXT NOT NULL, created_at TEXT NOT NULL,
                bundle TEXT, bundle_seal TEXT, report TEXT, decision TEXT,
                decision_input TEXT, decision_actor TEXT, decision_seal TEXT) STRICT''')
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS api_active_task ON api_runs(task_id) "
                         "WHERE state IN ('scheduled','running','waiting')")
            conn.execute('''CREATE TABLE IF NOT EXISTS api_run_events (
                position INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                task_id TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL) STRICT''')
            conn.execute('CREATE INDEX IF NOT EXISTS api_task_events ON api_run_events(task_id,position)')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS api_events_no_update
                BEFORE UPDATE ON api_run_events BEGIN SELECT RAISE(ABORT, 'append only'); END''')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS api_events_no_delete
                BEFORE DELETE ON api_run_events BEGIN SELECT RAISE(ABORT, 'append only'); END''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_features (
                feature_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL, created_at TEXT NOT NULL) STRICT''')
            conn.execute('CREATE INDEX IF NOT EXISTS api_project_features ON api_features(project_id)')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_task_features (
                task_id TEXT PRIMARY KEY, feature_id TEXT NOT NULL) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_documents (
                document_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL, agent TEXT NOT NULL,
                stage TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL, content TEXT NOT NULL, content_sha256 TEXT NOT NULL, reason TEXT NOT NULL,
                model_id TEXT, created_at TEXT NOT NULL, UNIQUE(run_id, kind)) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_design_parts (
                run_id TEXT NOT NULL, stage TEXT NOT NULL, part INTEGER NOT NULL, attempt INTEGER NOT NULL, kinds TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT, model_id TEXT, timeout_secs INTEGER, started_at TEXT NOT NULL, finished_at TEXT,
                PRIMARY KEY (run_id, stage, part, attempt)) STRICT''')
            conn.execute("UPDATE api_design_parts SET state='interrupted', finished_at=NULL WHERE state='running'")
            conn.execute('CREATE TABLE IF NOT EXISTS api_document_basis (run_id TEXT NOT NULL, kind TEXT NOT NULL, '
                         'basis TEXT NOT NULL, PRIMARY KEY (run_id, kind)) STRICT')
            conn.execute('CREATE INDEX IF NOT EXISTS api_task_documents ON api_documents(task_id)')
            # What a document said before each change. ``diagram`` is the diagram the owner edited; NULL when a rewrite replaced their edit.
            conn.execute('''CREATE TABLE IF NOT EXISTS api_document_revisions (
                position INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                diagram INTEGER, previous_content TEXT NOT NULL, previous_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                edited_by TEXT NOT NULL, edited_at TEXT NOT NULL) STRICT''')
            conn.execute('CREATE INDEX IF NOT EXISTS api_document_revision_kinds ON api_document_revisions(task_id, kind)')
            # A diagram changed on a published task: its documents are a record of what shipped, so the change waits here for a revision.
            conn.execute('''CREATE TABLE IF NOT EXISTS api_diagram_proposals (
                position INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, kind TEXT NOT NULL, diagram INTEGER NOT NULL,
                source TEXT NOT NULL, original TEXT NOT NULL, edited_by TEXT NOT NULL, updated_at TEXT NOT NULL, revision_task_id TEXT) STRICT''')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS api_open_diagram_proposals ON api_diagram_proposals(task_id, kind, diagram) '
                         'WHERE revision_task_id IS NULL')
            conn.execute('CREATE INDEX IF NOT EXISTS api_revision_diagrams ON api_diagram_proposals(revision_task_id)')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS api_document_revisions_no_update
                BEFORE UPDATE ON api_document_revisions BEGIN SELECT RAISE(ABORT, 'append only'); END''')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS api_document_revisions_no_delete
                BEFORE DELETE ON api_document_revisions BEGIN SELECT RAISE(ABORT, 'append only'); END''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_project_repos (
                project_id TEXT PRIMARY KEY, repository TEXT NOT NULL, default_branch TEXT, private INTEGER,
                state TEXT NOT NULL, error TEXT, updated_at TEXT NOT NULL) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_project_specs (
                project_id TEXT PRIMARY KEY, state TEXT NOT NULL, content TEXT NOT NULL, error TEXT,
                approved_by TEXT, approved_at TEXT, updated_at TEXT NOT NULL) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_messages (
                position INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT,
                role TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL) STRICT''')
            conn.execute('CREATE INDEX IF NOT EXISTS api_project_messages ON api_messages(project_id, task_id)')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_task_branches (
                task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch TEXT NOT NULL, revises_task_id TEXT,
                publication TEXT NOT NULL, error TEXT, commit_sha TEXT, pr_number INTEGER, pr_url TEXT,
                updated_at TEXT NOT NULL) STRICT''')
            conn.execute("UPDATE api_project_repos SET state='failed', error='interrupted' WHERE state='cloning'")
            conn.execute("UPDATE api_project_specs SET state='failed', error='interrupted' WHERE state='thinking'")
            conn.execute("UPDATE api_task_branches SET publication='failed', error='interrupted' "
                         "WHERE publication='publishing'")
            conn.execute('''CREATE TABLE IF NOT EXISTS api_design_decisions (
                run_id TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT, actor TEXT NOT NULL,
                documents_sha256 TEXT NOT NULL, decided_at TEXT NOT NULL) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_task_drafts (
                draft_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL, title TEXT NOT NULL,
                brief TEXT NOT NULL, options TEXT NOT NULL, messages TEXT NOT NULL, task_id TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_work_packages (
                run_id TEXT NOT NULL, key TEXT NOT NULL, position INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
                specialty TEXT NOT NULL, instructions TEXT NOT NULL, owns TEXT NOT NULL, state TEXT NOT NULL, phase TEXT,
                iteration INTEGER, summary TEXT, questions TEXT, error TEXT, patch TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, key)) STRICT''')
            have = {r['name'] for r in conn.execute('PRAGMA table_info(api_work_packages)')}
            if 'wave' not in have:  # runs made before waves: the foundation went first, everything else together
                conn.execute('ALTER TABLE api_work_packages ADD COLUMN wave INTEGER NOT NULL DEFAULT 1')
                conn.execute("UPDATE api_work_packages SET wave=0 WHERE kind='foundation'")
            if 'attempt' not in have:
                conn.execute('ALTER TABLE api_work_packages ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1')
            if 'contract' not in have:
                conn.execute("ALTER TABLE api_work_packages ADD COLUMN contract TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE api_work_packages SET state='pending', phase=NULL WHERE state='running'")
            conn.execute('''CREATE TABLE IF NOT EXISTS api_task_ci (
                task_id TEXT PRIMARY KEY, commit_sha TEXT NOT NULL, state TEXT NOT NULL, runs TEXT NOT NULL, log TEXT,
                started_at TEXT NOT NULL, updated_at TEXT NOT NULL) STRICT''')
            conn.execute('''CREATE TABLE IF NOT EXISTS api_autonomy_decisions (
                position INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_id TEXT NOT NULL, gate TEXT NOT NULL,
                level INTEGER NOT NULL, risk TEXT NOT NULL, outcome TEXT NOT NULL, actor TEXT NOT NULL, reasons TEXT NOT NULL,
                payload TEXT NOT NULL, evidence TEXT NOT NULL, seal TEXT NOT NULL, decided_at TEXT NOT NULL) STRICT''')
            conn.execute('CREATE INDEX IF NOT EXISTS api_task_autonomy ON api_autonomy_decisions(task_id,position)')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS api_autonomy_no_update
                BEFORE UPDATE ON api_autonomy_decisions BEGIN SELECT RAISE(ABORT, 'append only'); END''')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS api_autonomy_no_delete
                BEFORE DELETE ON api_autonomy_decisions BEGIN SELECT RAISE(ABORT, 'append only'); END''')
            conn.execute('CREATE INDEX IF NOT EXISTS api_project_drafts ON api_task_drafts(project_id, state)')
            conn.execute("UPDATE api_task_drafts SET state='failed', error='interrupted' WHERE state='thinking'")
            self._install_change_log(conn)

    @staticmethod
    def _install_change_log(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS api_changes (
            position INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            project_id TEXT, task_id TEXT, ref TEXT) STRICT''')
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS api_changes_bounded AFTER INSERT ON api_changes
            WHEN NEW.position % 1000 = 0 BEGIN
            DELETE FROM api_changes WHERE position <= NEW.position - {CHANGE_RETENTION}; END''')
        for table, kind, project, task, ref, writes in WATCHED_WRITES:
            for number, write in enumerate(writes):
                # Dropped first, so a newer definition replaces the one an earlier version installed.
                name = f'api_change_{table}_{number}'
                conn.execute(f'DROP TRIGGER IF EXISTS {name}')
                conn.execute(f'''CREATE TRIGGER {name} AFTER {write} ON {table} BEGIN
                    INSERT INTO api_changes(kind,project_id,task_id,ref) VALUES ('{kind}',{project},{task},{ref}); END''')

    def change_bounds(self):
        """The newest change, and the oldest still kept. Both are 0 until something has been written."""
        row = self.db.conn.execute('SELECT MAX(position) AS head, MIN(position) AS oldest FROM api_changes').fetchone()
        return row['head'] or 0, row['oldest'] or 0

    def changes(self, after, limit):
        return [dict(r) for r in self.db.conn.execute(
            'SELECT * FROM api_changes WHERE position>? ORDER BY position LIMIT ?', (after, limit)).fetchall()]

    def execution_event(self, position):
        row = self.db.conn.execute('SELECT * FROM api_run_events WHERE position=?', (position,)).fetchone()
        return dict(position=row['position'], run_id=row['run_id'], task_id=row['task_id'],
                    timestamp=row['timestamp'], **json.loads(row['payload'])) if row else None

    def running_heartbeats(self):
        return [dict(r) for r in self.db.conn.execute(
            'SELECT r.run_id, r.task_id, t.project_id, r.heartbeat_at FROM api_runs r '
            "JOIN tasks t ON t.task_id=r.task_id WHERE r.state='running'").fetchall()]

    def create(self, task_id):
        run_id, timestamp = new_id('request'), now()
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO api_runs(run_id,task_id,state,phase,updated_at,created_at) '
                         'VALUES (?,?,?,?,?,?)', (run_id, task_id, 'scheduled', 'queued', timestamp, timestamp))
            self.event(run_id, task_id, {'state': 'scheduled', 'phase': 'queued'})
        return run_id

    def event(self, run_id, task_id, payload):
        self.db.conn.execute('INSERT INTO api_run_events(run_id,task_id,timestamp,payload) VALUES (?,?,?,?)',
                             (run_id, task_id, now(), json.dumps(payload, sort_keys=True)))

    def get(self, run_id):
        row = self.db.conn.execute('SELECT * FROM api_runs WHERE run_id=?', (run_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, task_id):
        row = self.db.conn.execute('SELECT * FROM api_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1',
                                   (task_id,)).fetchone()
        return dict(row) if row else None

    def update(self, run_id, *, state, phase, iteration=None, wait_reason=None, **note):
        with self.db.transaction() as conn:
            row = self.get(run_id)
            conn.execute('UPDATE api_runs SET state=?,phase=?,iteration=?,wait_reason=?,heartbeat_at=?,updated_at=? '
                         'WHERE run_id=?', (state, phase, iteration, wait_reason,
                                           now() if state == 'running' else None, now(), run_id))
            # ``note`` (attempt, retry_reason) is history for the reader only: it has no column of its own.
            self.event(run_id, row['task_id'], dict(state=state, phase=phase, iteration=iteration,
                                                   wait_reason=wait_reason,
                                                   **{k: v for k, v in note.items() if v is not None}))

    def events(self, task_id, after=0, limit=100):
        rows = self.db.conn.execute('SELECT * FROM api_run_events WHERE task_id=? AND position>? '
                                    'ORDER BY position LIMIT ?', (task_id, after, limit)).fetchall()
        return [dict(position=r['position'], run_id=r['run_id'], task_id=r['task_id'],
                     timestamp=r['timestamp'], **json.loads(r['payload'])) for r in rows]

    def save_documents(self, run_id, task_id, documents, basis=''):
        with self.db.transaction() as conn:
            for d in documents:
                # A rerun after the owner's answer replaces what the earlier attempt recorded, but never loses an owner's edit.
                sha = hashlib.sha256(d.content.encode()).hexdigest()
                replaced = conn.execute(f'SELECT d.*, {_EDITED_BY} FROM api_documents d WHERE d.run_id=? AND d.kind=?',
                                        (run_id, d.spec.kind)).fetchone()
                if replaced and replaced['edited_by'] and replaced['content_sha256'] != sha:
                    conn.execute('INSERT INTO api_document_revisions(run_id,task_id,kind,diagram,previous_content,previous_sha256,'
                                 'content_sha256,edited_by,edited_at) VALUES (?,?,?,NULL,?,?,?,?,?)',
                                 (run_id, task_id, d.spec.kind, replaced['content'], replaced['content_sha256'], sha, 'system', now()))
                conn.execute('DELETE FROM api_documents WHERE run_id=? AND kind=?', (run_id, d.spec.kind))
                conn.execute('INSERT INTO api_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (new_id('document'), run_id, task_id, d.spec.agent, d.spec.stage, d.spec.kind,
                              d.spec.title, str(d.spec.source), str(d.status), d.content,
                              sha, d.reason, d.model_id, now()))
                conn.execute('DELETE FROM api_document_basis WHERE run_id=? AND kind=?', (run_id, d.spec.kind))
                if basis and str(d.status) == 'generated':
                    conn.execute('INSERT INTO api_document_basis VALUES (?,?,?)', (run_id, d.spec.kind, basis))

    def documents(self, task_id, run_id=None):
        run_id = run_id or (self.latest(task_id) or {}).get('run_id')
        rows = self.db.conn.execute(f'SELECT d.*, {_EDITED_BY} FROM api_documents d WHERE d.run_id=? ORDER BY d.rowid',
                                    (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def edit_document(self, document, diagram, content, actor):
        """The owner's edit of one diagram. The caller holds the transaction that checked the design is still at its gate."""
        sha = hashlib.sha256(content.encode()).hexdigest()
        self.db.conn.execute('INSERT INTO api_document_revisions(run_id,task_id,kind,diagram,previous_content,previous_sha256,'
                             'content_sha256,edited_by,edited_at) VALUES (?,?,?,?,?,?,?,?,?)',
                             (document['run_id'], document['task_id'], document['kind'], diagram, document['content'],
                              document['content_sha256'], sha, actor, now()))
        self.db.conn.execute("UPDATE api_documents SET content=?, content_sha256=?, status='generated', reason='' WHERE run_id=? AND kind=?",
                             (content, sha, document['run_id'], document['kind']))
        return sha

    def last_revision(self, task_id):
        row = self.db.conn.execute('SELECT MAX(position) AS position FROM api_document_revisions WHERE task_id=? AND diagram IS NOT NULL',
                                   (task_id,)).fetchone()
        return row['position'] or 0

    def propose_diagram(self, task_id, kind, diagram, source, original, actor):
        """Writing the original back withdraws the proposal: nothing is left to revise."""
        self.db.conn.execute('DELETE FROM api_diagram_proposals WHERE task_id=? AND kind=? AND diagram=? AND revision_task_id IS NULL',
                             (task_id, kind, diagram))
        if source != original:
            self.db.conn.execute('INSERT INTO api_diagram_proposals(task_id,kind,diagram,source,original,edited_by,updated_at) '
                                 'VALUES (?,?,?,?,?,?,?)', (task_id, kind, diagram, source, original, actor, now()))

    def open_proposals(self, task_id):
        return [dict(r) for r in self.db.conn.execute(
            'SELECT * FROM api_diagram_proposals WHERE task_id=? AND revision_task_id IS NULL ORDER BY position', (task_id,))]

    def take_proposals(self, task_id, revision_task_id):
        self.db.conn.execute('UPDATE api_diagram_proposals SET revision_task_id=? WHERE task_id=? AND revision_task_id IS NULL',
                             (revision_task_id, task_id))

    def diagrams_for_revision(self, revision_task_id):
        return [dict(r) for r in self.db.conn.execute(
            'SELECT * FROM api_diagram_proposals WHERE revision_task_id=? ORDER BY kind, diagram', (revision_task_id,))]

    def restart_packages(self, run_id):
        """The design changed, so what passed was built on the old one: every package is worked again."""
        conn = self.db.conn
        conn.execute("DELETE FROM api_work_packages WHERE run_id=? AND kind='fix'", (run_id,))
        conn.execute("UPDATE api_work_packages SET state='pending', phase=NULL, iteration=NULL, summary=NULL, questions=NULL, "
                     'error=NULL, patch=NULL, attempt=1, updated_at=? WHERE run_id=?', (now(), run_id))

    def original_of(self, task_id, kind):
        """The document as it was before the owner's run of edits, which a model's rewrite ends."""
        original = None
        for row in self.db.conn.execute('SELECT diagram, previous_content FROM api_document_revisions WHERE task_id=? AND kind=? '
                                        'ORDER BY position DESC', (task_id, kind)):
            if row['diagram'] is None:
                break
            original = row['previous_content']
        return original

    def start_part(self, run_id, stage, part, attempt, kinds, timeout_secs):
        """One model call of a stage has begun. Its row is the evidence of what ran, when, and for how long."""
        with self.db.transaction() as conn:
            conn.execute('INSERT OR REPLACE INTO api_design_parts(run_id,stage,part,attempt,kinds,state,timeout_secs,started_at) '
                         "VALUES (?,?,?,?,?,'running',?,?)", (run_id, stage, part, attempt, json.dumps(list(kinds)), timeout_secs, now()))

    def finish_part(self, run_id, stage, part, attempt, *, state, reason, model_id, started_at, finished_at):
        with self.db.transaction() as conn:
            conn.execute('UPDATE api_design_parts SET state=?,reason=?,model_id=?,started_at=?,finished_at=? '
                         'WHERE run_id=? AND stage=? AND part=? AND attempt=?',
                         (state, reason, model_id, started_at, finished_at, run_id, stage, part, attempt))

    def design_parts(self, task_id):
        """Every model call of the latest run's design, as recorded when it happened."""
        rows = self.db.conn.execute('SELECT * FROM api_design_parts WHERE run_id=? ORDER BY rowid',
                                    ((self.latest(task_id) or {}).get('run_id'),)).fetchall()
        return [{k: r[k] for k in ('stage', 'part', 'attempt', 'state', 'reason', 'model_id', 'timeout_secs', 'started_at',
                                   'finished_at')} | {'kinds': json.loads(r['kinds'])} for r in rows]

    def copy_packages(self, from_run, to_run):
        """The same division for a retry: the work starts again, the model is not asked to divide it again."""
        with self.db.transaction() as conn:
            conn.execute('DELETE FROM api_work_packages WHERE run_id=?', (to_run,))
            conn.execute("INSERT INTO api_work_packages(run_id,key,position,kind,title,specialty,instructions,owns,state,wave,contract,updated_at) "
                         "SELECT ?,key,position,kind,title,specialty,instructions,owns,'pending',wave,contract,? FROM api_work_packages "
                         "WHERE run_id=? AND kind<>'fix'", (to_run, now(), from_run))

    def reusable_documents(self, task_id, run_id, basis):
        """Documents an earlier run of this task finished on the same basis: a retry resumes there, not from the brief.

        The newest finished copy of each kind wins. A document written for a different instruction, specification, or
        owner answer is never offered.
        """
        rows = self.db.conn.execute(
            "SELECT d.* FROM api_documents d JOIN api_document_basis b ON b.run_id=d.run_id AND b.kind=d.kind "
            "WHERE d.task_id=? AND d.run_id<>? AND b.basis=? AND d.status='generated' "
            'ORDER BY d.created_at DESC, d.rowid DESC', (task_id, run_id, basis)).fetchall()
        out = {}
        for row in rows:
            out.setdefault(row['kind'], dict(row))
        return out

    def documents_digest(self, run_id):
        rows = self.db.conn.execute('SELECT kind,content_sha256 FROM api_documents WHERE run_id=? ORDER BY kind',
                                    (run_id,)).fetchall()
        return hashlib.sha256(json.dumps([list(r) for r in rows]).encode()).hexdigest()

    def record_design_decision(self, run_id, decision, reason, actor):
        self.db.conn.execute('INSERT INTO api_design_decisions VALUES (?,?,?,?,?,?)',
                             (run_id, decision, reason, actor, self.documents_digest(run_id), now()))

    def design_approved(self, run_id):
        row = self.db.conn.execute('SELECT decision FROM api_design_decisions WHERE run_id=?', (run_id,)).fetchone()
        return bool(row) and row['decision'] == 'approve'

    @staticmethod
    def _decision_body(gate, level, risk, outcome, actor, reasons, payload, evidence, decided_at):
        return json.dumps([gate, level, risk, outcome, actor, reasons, payload, evidence, decided_at], separators=(',', ':'))

    def add_decision(self, run_id, task_id, *, sign, gate, level, risk, outcome, actor, reasons=(), payload=None, evidence=''):
        """One gate or Lead decision, sealed and append-only: who decided, on what, at which level, and why."""
        reasons, payload, decided_at = json.dumps(list(reasons), ensure_ascii=False), json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), now()
        seal = sign(run_id, task_id, self._decision_body(gate, level, risk, outcome, actor, reasons, payload, evidence, decided_at))
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO api_autonomy_decisions(run_id,task_id,gate,level,risk,outcome,actor,reasons,payload,evidence,seal,decided_at) '
                         'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (run_id, task_id, gate, level, risk, outcome, actor, reasons, payload, evidence, seal, decided_at))

    def decisions(self, task_id, sign=None):
        """Oldest first. With ``sign``, each row says whether its seal still holds."""
        out = []
        for r in self.db.conn.execute('SELECT * FROM api_autonomy_decisions WHERE task_id=? ORDER BY position', (task_id,)).fetchall():
            row = {k: r[k] for k in ('position', 'run_id', 'task_id', 'gate', 'level', 'risk', 'outcome', 'actor', 'evidence', 'decided_at')}
            row |= {'reasons': json.loads(r['reasons']), 'payload': json.loads(r['payload'])}
            if sign:
                body = self._decision_body(r['gate'], r['level'], r['risk'], r['outcome'], r['actor'], r['reasons'], r['payload'],
                                           r['evidence'], r['decided_at'])
                row['seal_valid'] = hmac.compare_digest(sign(r['run_id'], r['task_id'], body), r['seal'])
            out.append(row)
        return out

    def branch_tasks(self, project_id, branch):
        """Every task that published to one branch: a revision and a CI repair share their base's branch."""
        return [r['task_id'] for r in self.db.conn.execute(
            'SELECT task_id FROM api_task_branches WHERE project_id=? AND branch=? AND commit_sha IS NOT NULL', (project_id, branch)).fetchall()]

    def create_feature(self, project_id, title, description):
        feature = dict(feature_id=new_id('feature'), project_id=project_id, title=title,
                       description=description, created_at=now())
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO api_features VALUES (:feature_id,:project_id,:title,:description,:created_at)', feature)
        return feature

    def features(self, project_id):
        rows = self.db.conn.execute(
            'SELECT f.*, (SELECT COUNT(*) FROM api_task_features l WHERE l.feature_id=f.feature_id) AS task_count '
            'FROM api_features f WHERE f.project_id=? ORDER BY f.rowid', (project_id,)).fetchall()
        return [dict(r) for r in rows]

    def link_feature(self, task_id, feature_id):
        self.db.conn.execute('INSERT OR IGNORE INTO api_task_features VALUES (?,?)', (task_id, feature_id))

    def feature_of(self, task_id):
        row = self.db.conn.execute('SELECT feature_id FROM api_task_features WHERE task_id=?', (task_id,)).fetchone()
        return row['feature_id'] if row else None

    def add_repo(self, project_id, repository):
        self.db.conn.execute('INSERT INTO api_project_repos(project_id,repository,state,updated_at) VALUES (?,?,?,?)',
                             (project_id, repository, 'cloning', now()))

    def update_repo(self, project_id, **fields):
        self._update('api_project_repos', 'project_id', project_id, fields)

    def repo(self, project_id):
        row = self.db.conn.execute('SELECT * FROM api_project_repos WHERE project_id=?', (project_id,)).fetchone()
        return dict(row) if row else None

    def set_spec(self, project_id, *, state, content=None, error=None, approved_by=None):
        with self.db.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO api_project_specs(project_id,state,content,updated_at) VALUES (?,?,'',?)",
                         (project_id, state, now()))
            self._update('api_project_specs', 'project_id', project_id, dict(
                state=state, error=error, approved_by=approved_by, approved_at=now() if approved_by else None,
                **({} if content is None else {'content': content})))

    def spec(self, project_id):
        row = self.db.conn.execute('SELECT * FROM api_project_specs WHERE project_id=?', (project_id,)).fetchone()
        return dict(row) if row else None

    def add_message(self, project_id, task_id, role, payload):
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO api_messages(project_id,task_id,role,payload,created_at) VALUES (?,?,?,?,?)',
                         (project_id, task_id, role, json.dumps(payload, ensure_ascii=False), now()))

    def messages(self, project_id, task_id=None):
        rows = self.db.conn.execute('SELECT * FROM api_messages WHERE project_id=? AND task_id IS ? ORDER BY position',
                                    (project_id, task_id)).fetchall()
        return [dict(position=r['position'], role=r['role'], created_at=r['created_at'], **json.loads(r['payload']))
                for r in rows]

    def set_work_packages(self, run_id, packages):
        with self.db.transaction() as conn:
            conn.execute('DELETE FROM api_work_packages WHERE run_id=?', (run_id,))
            for position, package in enumerate(packages):
                self.add_package(run_id, package, position)

    def add_package(self, run_id, package, position):
        self.db.conn.execute('INSERT INTO api_work_packages(run_id,key,position,kind,title,specialty,instructions,owns,state,'
                             'wave,contract,updated_at) VALUES (?,?,?,?,?,?,?,?,\'pending\',?,?,?)',
                             (run_id, package.key, position, package.kind, package.title, package.specialty,
                              package.instructions, json.dumps(list(package.owns)), package.wave, package.contract, now()))

    def split_package(self, run_id, key, children):
        """A package too big for one sitting is replaced by smaller ones in the same place and wave."""
        with self.db.transaction() as conn:
            row = conn.execute('SELECT position FROM api_work_packages WHERE run_id=? AND key=?', (run_id, key)).fetchone()
            conn.execute('DELETE FROM api_work_packages WHERE run_id=? AND key=?', (run_id, key))
            for child in children:
                self.add_package(run_id, child, row['position'])

    def work_packages(self, run_id):
        rows = self.db.conn.execute('SELECT * FROM api_work_packages WHERE run_id=? ORDER BY position', (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def update_package(self, run_id, key, **fields):
        columns = ','.join(f'{name}=?' for name in fields)  # column names come from this package only
        with self.db.transaction() as conn:
            conn.execute(f'UPDATE api_work_packages SET {columns},updated_at=? WHERE run_id=? AND key=?',
                         (*fields.values(), now(), run_id, key))

    def public_packages(self, task_id):
        """What a reader needs about each package: never the patch itself."""
        out = []
        for r in self.work_packages((self.latest(task_id) or {}).get('run_id')):
            out.append({k: r[k] for k in ('key', 'kind', 'title', 'specialty', 'state', 'phase', 'iteration', 'summary',
                                          'error', 'updated_at', 'wave', 'attempt')} | {'owns': json.loads(r['owns']),
                       'questions': json.loads(r['questions'] or '[]'),
                       'files': len(decode(json.loads(r['patch'])).changes) if r['patch'] else 0})
        return out

    def set_ci(self, task_id, commit_sha, state, runs=(), log=None):
        with self.db.transaction() as conn:
            started = conn.execute('SELECT started_at, commit_sha FROM api_task_ci WHERE task_id=?', (task_id,)).fetchone()
            begun = started['started_at'] if started and started['commit_sha'] == commit_sha else now()
            conn.execute('INSERT OR REPLACE INTO api_task_ci VALUES (?,?,?,?,?,?,?)',
                         (task_id, commit_sha, state, json.dumps(list(runs), ensure_ascii=False), log, begun, now()))

    def ci(self, task_id):
        row = self.db.conn.execute('SELECT * FROM api_task_ci WHERE task_id=?', (task_id,)).fetchone()
        return dict(row) | {'runs': json.loads(row['runs'])} if row else None

    def watching_ci(self):
        return [r['task_id'] for r in self.db.conn.execute("SELECT task_id FROM api_task_ci WHERE state='watching'").fetchall()]

    def add_draft(self, project_id, content, options):
        draft_id, timestamp = new_id('draft'), now()
        messages = json.dumps([{'role': 'owner', 'message': content}], ensure_ascii=False)
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO api_task_drafts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (draft_id, project_id, 'thinking', '', '', json.dumps(options), messages, None, None,
                          timestamp, timestamp))
        return draft_id

    def add_draft_message(self, draft, role, payload, **fields):
        messages = [*draft['messages'], {'role': role, **payload}]
        with self.db.transaction():
            self.update_draft(draft['draft_id'], messages=json.dumps(messages, ensure_ascii=False), **fields)

    def update_draft(self, draft_id, **fields):
        self._update('api_task_drafts', 'draft_id', draft_id, fields)

    def draft(self, draft_id):
        row = self.db.conn.execute('SELECT * FROM api_task_drafts WHERE draft_id=?', (draft_id,)).fetchone()
        if not row:
            return None
        messages = [dict(m, position=i) for i, m in enumerate(json.loads(row['messages']))]
        return dict(row) | {'messages': messages, 'options': json.loads(row['options'])}

    def open_drafts(self, project_id):
        rows = self.db.conn.execute("SELECT draft_id FROM api_task_drafts WHERE project_id=? "
                                    "AND state NOT IN ('created','discarded') ORDER BY rowid DESC LIMIT 5",
                                    (project_id,)).fetchall()
        return [self.draft(r['draft_id']) for r in rows]

    def draft_of_task(self, task_id):
        return self.db.conn.execute('SELECT draft_id FROM api_task_drafts WHERE task_id=?', (task_id,)).fetchone()

    def add_branch(self, task_id, project_id, branch, revises_task_id=None):
        self.db.conn.execute('INSERT OR IGNORE INTO api_task_branches(task_id,project_id,branch,revises_task_id,'
                             "publication,updated_at) VALUES (?,?,?,?,'none',?)",
                             (task_id, project_id, branch, revises_task_id, now()))

    def update_branch(self, task_id, **fields):
        self._update('api_task_branches', 'task_id', task_id, fields)

    def branch(self, task_id):
        row = self.db.conn.execute('SELECT * FROM api_task_branches WHERE task_id=?', (task_id,)).fetchone()
        return dict(row) if row else None

    def _update(self, table, key, value, fields):
        # Table and column names come from this module only; values stay bound parameters.
        columns = ','.join(f'{name}=?' for name in fields)
        self.db.conn.execute(f'UPDATE {table} SET {columns},updated_at=? WHERE {key}=?',
                             (*fields.values(), now(), value))

    @staticmethod
    def snapshot(row):
        if row is None:
            return dict(run_id=None, phase=None, state='unknown', activity_known=False, wait_reason=None,
                        iteration=None, heartbeat_at=None, updated_at=None, created_at=None, stale=False)
        heartbeat = row['heartbeat_at']
        fresh = heartbeat is not None and (datetime.now(UTC) - datetime.fromisoformat(
            heartbeat.replace('Z', '+00:00'))).total_seconds() <= 15
        return {k: row[k] for k in ('run_id', 'state', 'phase', 'iteration', 'wait_reason',
                                    'heartbeat_at', 'updated_at', 'created_at')} | {
            'activity_known': row['state'] == 'running' and fresh,
            'stale': row['state'] == 'running' and not fresh}
