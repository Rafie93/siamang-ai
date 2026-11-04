"""Single-worker proposal runtime. Calls existing services; never applies or executes patches."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.approval import ApprovalDecision, ApprovalPolicy, ApprovalRequest, ApprovalService
from siamang.autonomy import (
    AUTONOMY_ACTOR,
    LEAD_ACTOR,
    AutonomyPolicy,
    Gate,
    Risk,
    RiskLevel,
    Step,
    assess_brief,
    blockers_key,
    next_step,
    settle,
)
from siamang.ids import new_id
from siamang.documents.author import (
    Document,
    batches,
    choose_files,
    clip,
    clip_bytes,
    design_input,
    write_batches,
    write_documents,
)
from siamang.documents.catalog import SPECS, DocumentStatus, Source, specs_for
from siamang.documents.derive import programmer_documents, qa_documents, review_documents
from siamang.documents.packages import divide, render
from siamang.documents.lead import (
    BRIEF_LIMIT,
    DRAFT_PROMPT,
    INTAKE_PROMPT,
    MAX_DRAFT_ROUNDS,
    MAX_INTAKE_ROUNDS,
    MAX_TASK_ROUNDS,
    TASK_PROMPT,
    ask_lead,
)
from siamang.jsondata import thaw
from siamang.log import redact
from siamang.models.errors import ModelError
from siamang.programmer.policy import ProgrammingPolicy
from siamang.repos.client import RepoClient, RepoError
from siamang.repos.workspace import branch_name, named_in, read_context, read_latest, source_files, write_changes
from siamang.loop import LoopRequest, LoopService
from siamang.planner import Planner, PlanningRequest
from siamang.programmer import Programmer
from siamang.recovery import CheckpointStore, RecoveryEngine, RecoveryPolicy, Stage, StageOutcome
from siamang.verification import VerificationReport
from siamang.projects import ProjectRegistry
from siamang.qa import QAService
from siamang.reviewer import ReviewerService
from siamang.storage import Database
from siamang.tasks import TaskEngine, TaskFailure, TaskResult
from siamang.tasks.definition import TaskRequest

from .codec import decode, encode
from . import autonomy, ci_repair, recover, verify
from .parallel import ONE_ROUND, QA_POLICY, INTEGRATION_REVIEW, INPUT_DESIGN_BYTES, SPEC_BYTES, Merged, PackageRun, PackageStopped, commit_groups, merge_patches
from .store import RunStore, now


class RuntimeRefusal(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class Stopped(Exception):
    pass


class Resume(Exception):
    """No owner is needed: the same run goes on, in this mode, on the worker it already holds."""

    def __init__(self, mode):
        self.mode = mode


# A real model writes whole files: it needs more time than the default, and to see more than a small file.
MODEL_POLICY = ProgrammingPolicy(model_timeout_secs=600, max_context_file_size=64 * 1024)


SYSTEM_NOTES = ('From analysis:', 'Not shown to the model:')  # added by the Programmer service, not questions
# Every way a run can stop short of an approval takes an answer from the owner, and that answer resumes it.
ANSWERABLE = ('clarification_required', 'quality_gate', 'input_required', 'worker_interrupted')
MAX_BLOCKERS = 8
DESIGN_STAGES = ('briefing', 'planning', 'analysis')
CI_POLL_SECS = 20
CI_NO_WORKFLOW_SECS = 120  # no run for this commit by then: the repository has no workflow for it
CI_TIMEOUT_SECS = 60 * 60
FINISHED_PACKAGES = ('passed', 'waiting', 'blocked', 'failed')


DESIGN_ATTEMPTS = 3  # a design stage is tried this many times before the run gives up
DESIGN_TIMEOUTS = (300, 450, 600)  # a model that timed out is given longer on the next try


class DesignFailed(Exception):
    """A model failed while writing the design: there is nothing for the operator to approve."""

    def __init__(self, stage='', reason='unknown', attempts=1):
        super().__init__(f'{stage}: {reason} after {attempts} attempt(s)')
        self.code, self.stage, self.reason, self.attempts = reason, stage, reason, attempts


def now_seconds():
    return time.monotonic()


def task_root(store, task):
    return ProjectRegistry(store.db).get(task.project_id).root_path


def conversation_tail(messages, limit=1_000):
    """The latest questions and answers, newest kept, for the Programmer's bounded input."""
    lines = []
    for m in messages:
        asked = ' '.join(q['question'] for q in m.get('questions', ()))
        lines.append(f"{m['role']}: {m.get('message', '')} {asked}".strip())
    return '\n'.join(lines).encode()[-limit:].decode(errors='ignore')


def _blockers(loop):
    """What QA and the Reviewer still hold against the last attempt, most serious first."""
    results = [r for r in (loop.final_qa_result, loop.final_review_result) if r]
    findings = sorted((f for r in results for f in r.findings if str(f.severity) != 'info'), key=lambda f: not f.blocking)
    return [f"{f.path or 'umum'}: {f.message} ({f.code})" for f in findings[:MAX_BLOCKERS]]


def _pull_request_body(task, patch, loop, debt='', actor=None, report=None):
    qa, review = loop.final_qa_result, loop.final_review_result
    ran, signed = autonomy.approver_note(actor, report)
    files = '\n'.join(f'- `{c.path}` ({c.operation})' for c in patch.changes)
    return redact(f'''## Task
{task.description}

## Ringkasan
{patch.summary}

## File
{files}

## Pemeriksaan Siamang
- QA: {qa.status if qa else 'tidak berjalan'} (pemeriksaan statis)
- Reviewer: {review.status if review else 'tidak berjalan'} (pemeriksaan deterministik)
{ran}

{debt}
{signed}
''')


def _stored(row):
    return Document(SPECS[row['kind']], DocumentStatus(row['status']), row['content'], row['reason'], row['model_id'])


def _basis(task, spec, conversation, project):
    """What the design was written for: another instruction, specification, or answer means another design."""
    return hashlib.sha256(json.dumps([task.description, spec, conversation, project.name, project.description],
                                     sort_keys=True, default=str).encode()).hexdigest()


class Runtime:
    def __init__(self, settings, models=None, authors=None, repos=None, verifier=None, tools=None, recovery=None, policy=None):
        self.settings = settings
        self._autonomy = policy  # an AutonomyPolicy; tests inject one
        self._verifier = verifier
        self._tools = tools  # a ToolRunner for the Programmer; tests inject one
        self._recovery = recovery  # a RecoveryPolicy; tests inject one
        self._models = models
        self._authors = authors or {}
        self._repos = repos or RepoClient()
        self._background = None
        self._busy = threading.Lock()
        self._pool = None
        self._lock_file = None
        self._closing = False
        self._ci_pool = None
        self._ci_wake = threading.Event()
        self.system_notes = SYSTEM_NOTES

    blockers = staticmethod(_blockers)

    def parallel_limit(self):
        if self._models is not None:
            return 3
        from siamang.config import load_config
        pin = load_config(self.settings.config).agents.get('programmer')
        return pin.parallel if pin else 3

    def ci_repair_limit(self):
        if self._models is not None:
            return self.ci_repair_attempts
        from siamang.config import load_config
        pin = load_config(self.settings.config).agents.get('programmer')
        return pin.ci_repair if pin else ci_repair.DEFAULT_ATTEMPTS

    ci_repair_attempts = ci_repair.DEFAULT_ATTEMPTS

    def verification(self):
        """``(service, attempts)`` when the sandbox is on, else ``(None, 0)``. A test can inject its own service."""
        if self._verifier is not None:
            return self._verifier, verify.DEFAULT_ATTEMPTS
        if self._models is not None:
            return None, 0
        from siamang.config import load_config
        box = load_config(self.settings.config).sandbox
        if not box.enabled:
            return None, 0
        from siamang.sandbox import CliContainerRuntime, SandboxPolicy, SandboxService
        from siamang.verification import VerificationService, parse_command
        policy = SandboxPolicy(box.image, timeout_secs=box.timeout_secs, memory_mb=box.memory_mb, cpus=box.cpus)
        checks = [c for c in (parse_command('test', box.test_command), parse_command('build', box.build_command)) if c]
        return VerificationService(SandboxService(CliContainerRuntime(box.runtime), policy), checks), box.verify_attempts

    def execution_features(self):
        """What this runtime will do with a run, for the UI: it must not imply a sandbox that is switched off."""
        verifier, _ = self.verification()
        return {'sandbox': verifier is not None, 'tools': self.tooling() is not None,
                'recovery': self.recovery() is not None}

    def tooling(self):
        """The Programmer's tool runner when ``sandbox.tools`` is on, else ``None`` (the one-shot Programmer)."""
        if self._tools is not None:
            return self._tools
        if self._models is not None:
            return None
        from siamang.config import load_config
        box = load_config(self.settings.config).sandbox
        if not (box.enabled and box.tools):
            return None
        from siamang.executor import ToolExecutor, ToolPolicy
        from siamang.sandbox import CliContainerRuntime, SandboxPolicy, SandboxService
        from .tooling import SandboxToolRunner
        policy = SandboxPolicy(box.image, timeout_secs=box.timeout_secs, memory_mb=box.memory_mb, cpus=box.cpus)
        sandbox = SandboxService(CliContainerRuntime(box.runtime), policy)
        return SandboxToolRunner(ToolExecutor(sandbox, ToolPolicy(max_turns=box.tool_turns, max_runs=box.tool_runs)))

    def programmer_options(self):
        tools = self.tooling()
        return dict(strategy='programming.tools', tools=tools) if tools else {}

    def recovery(self):
        """The recovery policy when ``[recovery] enabled``, else ``None`` (verify and retry as before)."""
        if self._recovery is not None:
            return self._recovery
        if self._models is not None:
            return None
        from siamang.config import load_config
        rec = load_config(self.settings.config).recovery
        if not rec.enabled:
            return None
        return RecoveryPolicy(retries=rec.retries, advisor_rounds=rec.advisor_rounds, replans=rec.replans,
                              max_attempts=rec.max_attempts)

    def autonomy(self):
        """Which gates pass without the owner. Only the operator's file sets this, and every gate is human by default."""
        if self._autonomy is not None:
            return self._autonomy
        if self._models is not None:
            return AutonomyPolicy()
        from siamang.config import load_config
        auto = load_config(self.settings.config).autonomy
        return AutonomyPolicy(level=auto.level, lead=auto.lead, lead_answers=auto.lead_answers,
                              lead_revisions=auto.lead_revisions, merge_method=auto.merge_method)

    def open(self):
        self.settings.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(str(self.settings.database) + '.api.lock', 'a')
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError('only one API runtime may own this database') from None
        try:
            with Database(self.settings.database) as db:
                store = RunStore(db)
                store.initialize()
                engine = TaskEngine(db, ProjectRegistry(db))
                with db.transaction():
                    rows = db.conn.execute("SELECT run_id,task_id FROM api_runs "
                                           "WHERE state IN ('scheduled','running')").fetchall()
                    for row in rows:
                        task = engine.get(row['task_id'])
                        if task.status == 'running':
                            engine.wait(task.task_id, reason='worker_interrupted')
                        store.update(row['run_id'], state='interrupted', phase='interrupted',
                                     wait_reason='worker_interrupted')
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='siamang-proposals')
            self._background = ThreadPoolExecutor(max_workers=1, thread_name_prefix='siamang-lead')
            self._ci_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='siamang-ci')
            self._ci_wake.clear()
            self._closing = False
            with Database(self.settings.database) as db:
                for task_id in RunStore(db).watching_ci():  # a restart must not lose a build that is still running
                    self._ci_pool.submit(self._watch_ci, task_id)
        except BaseException:
            self.close()
            raise

    def close(self):
        self._closing = True
        self._ci_wake.set()
        if self._background:
            self._background.shutdown(wait=True, cancel_futures=True)
            self._background = None
        if self._ci_pool:
            self._ci_pool.shutdown(wait=True, cancel_futures=True)
            self._ci_pool = None
        if self._pool:
            self._pool.shutdown(wait=True, cancel_futures=False)
            self._pool = None
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None

    def author(self, agent):
        """Router for an agent's documents; ``None`` when no model is assigned to it."""
        if self._models is not None:
            return self._authors.get(agent)
        from siamang.config import load_config
        return self.models(agent) if agent in load_config(self.settings.config).agents else None

    def assignments(self):
        """Which model each agent uses, as configured. Injected routers report nothing."""
        if self._models is not None:
            return {}
        from siamang.config import load_config
        config = load_config(self.settings.config)
        out = {}
        for agent, pin in config.agents.items():
            model = config.models[pin.model]
            out[agent] = dict(model_id=pin.model, provider_model=model.provider_model or pin.model,
                              provider_type=config.providers[model.provider].type)
        return out

    def models(self, agent='programmer'):
        if self._models is not None:
            return self._models
        from siamang.config import load_config
        from siamang.models.factory import build_router
        config = load_config(self.settings.config)
        # Echo must never masquerade as a production engineering model or a fallback.
        providers = {k: v for k, v in config.providers.items() if v.type != 'echo' and v.enabled}
        models = {k: v for k, v in config.models.items() if v.provider in providers and v.enabled}
        pinned = config.agents.get(agent)
        if pinned is not None:
            # Only the chosen model is registered: its failure must never move work to another.
            models = {k: v for k, v in models.items() if k == pinned.model}
        if not models:
            raise RuntimeRefusal('model_not_configured', 503)
        return build_router(replace(config, providers=providers, models=models))

    def schedule(self, task_id):
        if self._closing or not self._pool:
            raise RuntimeRefusal('runtime_unavailable', 503)
        self.models()  # refuse before claiming task acceptance by a worker
        if not self._busy.acquire(blocking=False):
            raise RuntimeRefusal('worker_busy', 409)
        try:
            with Database(self.settings.database) as db:
                engine, store = TaskEngine(db, ProjectRegistry(db)), RunStore(db)
                with db.transaction():
                    if engine.get(task_id).status != 'queued':
                        raise RuntimeRefusal('task_not_queued')
                    if db.conn.execute("SELECT 1 FROM api_runs WHERE task_id=? AND "
                                       "state IN ('scheduled','running','waiting')", (task_id,)).fetchone():
                        raise RuntimeRefusal('task_already_has_run')
                    run_id = store.create(task_id)
            self._pool.submit(self._work, run_id)
            return run_id
        except BaseException:
            self._busy.release()
            raise

    def _divide(self, db, store, engine, run_id, task, spec, conversation, repository, design, basis='', fresh=False, lead=True):
        """The Analyst's division and its document. Returns ``False`` when the owner must answer first.

        The document says what really happened: a division that failed is never reported as one Programmer being enough.
        """
        task_id, row = task.task_id, SPECS['work_packages']
        earlier = store.reusable_documents(task_id, run_id, basis).get('work_packages') if basis and not fresh else None
        if earlier:  # a retry of the same brief: the division stands, and the model is not paid to repeat it
            store.copy_packages(earlier['run_id'], run_id)
            store.save_documents(run_id, task_id, (Document(row, DocumentStatus.GENERATED, earlier['content'], model_id=earlier['model_id']),), basis)
            return True
        if repository is None:
            store.set_work_packages(run_id, ())
            store.save_documents(run_id, task_id, (Document(row, DocumentStatus.NOT_APPLICABLE, reason='Task tanpa repositori dikerjakan satu Programmer.'),))
            return True

        tries = sum(p['stage'] == 'division' for p in store.design_parts(task_id))  # a redivision adds to the record

        def started(attempt, timeout):
            store.start_part(run_id, 'division', 1, tries + attempt, ('work_packages',), timeout)

        def ended(attempt, began, finished, failure):
            store.finish_part(run_id, 'division', 1, tries + attempt, state='failed' if failure else 'done', reason=failure or None,
                              model_id=None, started_at=began, finished_at=finished)

        division = divide(self.author('analyst'), dict(
            instruction=task.description, project_spec=spec, task_conversation=conversation, repository=repository,
            design=[dict(document=d.spec.kind, text=clip(d.content, 3_000)) for d in design
                    if d.status is DocumentStatus.GENERATED]), started, ended)
        work = division.plan
        if work and work.undecided:
            self._stop_for_owner(db, store, engine, run_id, task, 'clarification_required',
                                 'Sebelum tim dibagi, ada keputusan teknis yang belum disepakati.', work.undecided, lead=lead)
            return False
        store.set_work_packages(run_id, work.packages if work else ())
        if work and work.packages:
            document = Document(row, DocumentStatus.GENERATED, render(work))
        elif division.failure:
            document = Document(row, DocumentStatus.FAILED, reason=(
                f'Pembagian kerja gagal setelah {division.attempts} percobaan: {division.failure}. '
                'Bagi ulang, atau bila desain disetujui sekarang, satu Programmer mengerjakan semuanya.')[:500])
        else:
            document = Document(row, DocumentStatus.NOT_APPLICABLE, reason=(
                f'Analyst menilai cukup satu Programmer: {division.single_reason}' if division.single_reason
                else 'Analyst menilai cukup satu Programmer, tanpa menuliskan alasannya.')[:500])
        store.save_documents(run_id, task_id, (document,), basis if document.status is DocumentStatus.GENERATED else '')
        return True

    def redivide(self, task_id):
        """Only the division again, for a design waiting at the gate: the documents stay, and so does the gate."""
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            task, row = engine.get(task_id), store.latest(task_id)
            if not row or row['state'] != 'waiting' or row['wait_reason'] != 'design_approval' or task.status != 'waiting':
                raise RuntimeRefusal('design_not_pending')
            if self._closing or not self._pool:
                raise RuntimeRefusal('runtime_unavailable', 503)
            if not self._busy.acquire(blocking=False):
                raise RuntimeRefusal('worker_busy', 409)
            try:
                with db.transaction():
                    engine.start(task_id)
                    store.update(row['run_id'], state='running', phase='analysis', redivide=True)
                self._pool.submit(self._redivide, row['run_id'])
            except BaseException:
                self._busy.release()
                raise
            return dict(task_id=task_id, run_id=row['run_id'], scheduled=True)

    def _redivide(self, run_id):
        done = threading.Event()
        pulse = threading.Thread(target=self._heartbeat, args=(run_id, done), daemon=True)
        pulse.start()
        try:
            with Database(self.settings.database) as db:
                engine, store = TaskEngine(db, ProjectRegistry(db)), RunStore(db)
                task_id = store.get(run_id)['task_id']
                try:
                    task = engine.get(task_id)
                    project = ProjectRegistry(db).get(task.project_id)
                    design = tuple(_stored(r) for r in store.documents(task_id, run_id) if r['stage'] in DESIGN_STAGES)
                    repo = store.repo(task.project_id)
                    spec = (store.spec(task.project_id) or {}).get('content', '')
                    conversation = store.messages(task.project_id, task_id)
                    if not self._divide(db, store, engine, run_id, task, spec, conversation,
                                        read_context(self._repos, project.root_path) if repo else None, design,
                                        _basis(task, spec, conversation, project), fresh=True, lead=False):  # the owner is here
                        return
                except Stopped:
                    return
                except Exception as exc:  # noqa: BLE001 - the design is still good: only this try is lost
                    store.save_documents(run_id, task_id, (Document(
                        SPECS['work_packages'], DocumentStatus.FAILED,
                        reason=f"Bagi ulang gagal: {getattr(exc, 'code', type(exc).__name__)}"),))
                with db.transaction():
                    if engine.get(task_id).status == 'cancelled':
                        return
                    engine.wait(task_id, reason='design_approval')
                    store.update(run_id, state='waiting', phase='design_approval', wait_reason='design_approval')
        finally:
            done.set()
            pulse.join()
            self._busy.release()

    def _heartbeat(self, run_id, done):
        while not done.wait(3):
            with Database(self.settings.database) as db:
                db.conn.execute("UPDATE api_runs SET heartbeat_at=? WHERE run_id=? AND state='running'",
                                (now(), run_id))

    def _work(self, run_id, mode='start'):
        """One run on the single worker. It keeps the worker across a gate that passed, so no other task slips in."""
        try:
            while mode:
                mode = self._attempt(run_id, mode)
        finally:
            self._busy.release()

    def _attempt(self, run_id, mode):
        """As far as this run gets. Returns the mode to go on in, or ``None`` when it stopped."""
        done = threading.Event()
        pulse = threading.Thread(target=self._heartbeat, args=(run_id, done), daemon=True)
        try:
            with Database(self.settings.database) as db:
                engine, store = TaskEngine(db, ProjectRegistry(db)), RunStore(db)
                row = store.get(run_id)
                task_id = row['task_id']

                state = dict(basis='')

                def phase(name, iteration=None, **note):
                    with db.transaction():
                        if self._closing or engine.get(task_id).status == 'cancelled':
                            raise Stopped()
                        store.update(run_id, state='running', phase=name, iteration=iteration, **note)

                def reused_for(stage):
                    """What an earlier run of this task already finished for the stage, on the same basis."""
                    kinds = {s.kind for s in specs_for(stage, Source.AUTHORED)}
                    return {k: Document(SPECS[k], DocumentStatus.GENERATED, r['content'], model_id=r['model_id'])
                            for k, r in store.reusable_documents(task_id, run_id, state['basis']).items() if k in kinds}

                def documents(stage, context, *, required=False):
                    agent = next(s.agent for s in SPECS.values() if s.stage == stage)
                    router = self.author(agent)
                    if not (required and router is not None):
                        if router is not None:
                            phase(stage, iteration=None)
                        written = write_documents(router, stage, context)
                        store.save_documents(run_id, task_id, written)
                        return written
                    # The stage is several model calls side by side. A round repeats only what failed, with more time,
                    # and each round is its own history row so the owner can see how often a stage was repeated.
                    done = reused_for(stage)
                    pending = [g for g in (tuple(k for k in group if k not in done) for group in batches(stage)) if g]
                    if done:
                        store.save_documents(run_id, task_id, tuple(done.values()), state['basis'])
                    if not pending:
                        phase(stage, iteration=None, reused=len(done))  # the history shows it was kept, not rewritten
                        return tuple(done[k] for g in batches(stage) for k in g)
                    reason = None
                    numbered = {k: i for i, g in enumerate(batches(stage), 1) for k in g}
                    for attempt in range(1, DESIGN_ATTEMPTS + 1):
                        phase(stage, iteration=None, attempt=attempt, retry_reason=reason,
                              remaining=sum(map(len, pending)) if attempt > 1 else None,
                              reused=len(done) if attempt == 1 and done else None)
                        timeout = DESIGN_TIMEOUTS[min(attempt, len(DESIGN_TIMEOUTS)) - 1]
                        for group in pending:
                            store.start_part(run_id, stage, numbered[group[0]], attempt, group, timeout)

                        def finished(group, written, started, ended):
                            # As each part ends, so its documents and its timing are there to see while the others still run.
                            store.save_documents(run_id, task_id, written, state['basis'])
                            bad = next((d.reason or 'unknown' for d in written if d.status is DocumentStatus.FAILED), None)
                            store.finish_part(run_id, stage, numbered[group[0]], attempt, state='failed' if bad else 'done',
                                              reason=bad, model_id=written[0].model_id if written else None,
                                              started_at=started, finished_at=ended)

                        written = write_batches(router, stage, pending, context, timeout, on_done=finished)
                        failed = [d for d in written if d.status is DocumentStatus.FAILED]
                        done |= {d.spec.kind: d for d in written if d.status is not DocumentStatus.FAILED}
                        if not failed:
                            return tuple(done[k] for g in batches(stage) for k in g)
                        reason = failed[0].reason or 'unknown'
                        again = {d.spec.kind for d in failed}
                        pending = [tuple(k for k in g if k in again) for g in pending]
                        pending = [g for g in pending if g]
                    raise DesignFailed(stage, reason, DESIGN_ATTEMPTS)

                with db.transaction():
                    if engine.get(task_id).status == 'cancelled':
                        raise Stopped()
                    task = engine.start(task_id)
                    # With a design gate the first visible work is the Lead's brief; the template plan is only input.
                    repair = ci_repair.info(task)  # a failed build is the brief: the approved design still stands
                    gated = mode == 'start' and not repair and bool(self.author('planner') or self.author('analyst'))
                    store.update(run_id, state='running',
                                 phase='programming' if mode == 'code' else 'briefing' if gated else 'planning')
                pulse.start()
                repo = store.repo(task.project_id)
                if repo:
                    self._prepare_branch(store, repo, task)
                ids = dict(task_id=task_id, project_id=task.project_id, instruction=task.description)
                plan = Planner(engine).plan(PlanningRequest(**ids))
                if mode != 'code' and not gated:
                    phase('analysis')
                # Only the design: a report from an earlier attempt must not read as an instruction to the Programmer.
                stored = tuple(_stored(row) for row in store.documents(task_id, run_id)
                               if row['stage'] in DESIGN_STAGES)
                failed_ci = store.ci(repair['failed_task_id']) if repair else None
                failed_log = (failed_ci or {}).get('log') or ''
                focus = self._focus(ProjectRegistry(db).get(task.project_id), stored, task, failed_log) if repo and not gated else ()
                analysis = SystemAnalyst(engine).analyze(AnalysisRequest(**ids, plan=plan, focus=focus))
                project = ProjectRegistry(db).get(task.project_id)
                brief = dict(project=dict(name=project.name, description=project.description),
                             instruction=task.description, plan=plan.to_dict(), observed_project=analysis.to_dict())
                spec = (store.spec(task.project_id) or {}).get('content', '') if repo else ''
                conversation = store.messages(task.project_id, task_id) if repo else []
                if repo:
                    brief |= dict(project_spec=spec, task_conversation=conversation,
                                  repository=read_context(self._repos, project.root_path))
                # A retry resumes from what an earlier run finished, but only for the same instruction, specification and answers.
                state['basis'] = _basis(task, spec, conversation, project)
                briefed = len(reused_for('briefing')) == len(specs_for('briefing', Source.AUTHORED))  # the Lead already cleared this
                if gated and repo and self.author('lead') and not briefed and not store.draft_of_task(task_id):  # a draft already asked
                    # Only what the Lead really asked: an answer it settled itself is not a round of questions.
                    rounds = sum(1 for m in store.messages(task.project_id, task_id) if m['role'] == 'lead' and m.get('questions'))
                    reply = ask_lead(self.author('lead'), TASK_PROMPT,
                                     brief | dict(rounds_left=max(MAX_TASK_ROUNDS - rounds, 0)))
                    if not reply.ready and rounds < MAX_TASK_ROUNDS:
                        self._stop_for_owner(db, store, engine, run_id, task, 'clarification_required', reply.message, reply.questions)
                        return None
                if gated:
                    design = []
                    for stage in DESIGN_STAGES:
                        prior = {d.spec.kind: clip(d.content) for d in design if d.status is DocumentStatus.GENERATED}
                        design += documents(stage, brief | dict(prior_documents=prior), required=True)
                    if not self._divide(db, store, engine, run_id, task, spec, conversation,
                                        brief['repository'] if repo else None, design, state['basis']):
                        return None
                    return self._design_gate(db, store, engine, run_id, task)
                design = stored

                class TrackedProgrammer(Programmer):
                    iteration = 0
                    last = None

                    def program(self, request):
                        self.iteration += 1
                        phase('programming', self.iteration)
                        self.last = super().program(request)
                        return self.last

                programmer = TrackedProgrammer(engine, models=self.models('programmer'), policy=MODEL_POLICY,
                                               **self.programmer_options())

                class TrackedQA(QAService):
                    def verify(self, request):
                        phase('qa', programmer.iteration)
                        return super().verify(request)

                class TrackedReviewer(ReviewerService):
                    def review(self, request):
                        phase('reviewing', programmer.iteration)
                        return super().review(request)

                packages = store.work_packages(run_id) if repo else []
                if packages:
                    phase('programming', 1)
                    delivered = self._deliver_packages(db, store, engine, run_id, task, ids, plan, project, spec, conversation, design)
                    if delivered is None:
                        return None
                    loop, analysis, programmer = delivered
                else:
                    base_input = (design_input(design) | ({'project_spec': clip_bytes(spec)} if spec else {})
                                  | ({'owner_conversation': conversation_tail(conversation)} if conversation else {})
                                  | (ci_repair.failure_input(ci_repair.failed_jobs(failed_ci['runs']), failed_log) if failed_ci else {}))

                    def run_loop(extra=None):
                        return LoopService(engine, programmer, TrackedQA(engine), TrackedReviewer(engine)).run(
                            LoopRequest(**ids, plan=plan, analysis=analysis, input=base_input | (extra or {})))

                    recovery = self.recovery()
                    loop = None if recovery else run_loop()
                verifier, attempts = self.verification()
                verification = None
                recovered = None
                if not packages and recovery:
                    checkpoint = CheckpointStore(self.settings.database.parent / 'checkpoints')
                    key = self._checkpoint_key(task, spec, conversation, design)
                    state = dict(loop=None, verification=None)

                    def deliver(ctx):
                        extra = {}
                        previous = state['verification']
                        if previous is not None and previous.status == 'failed':
                            extra |= verify.failure_input(previous)
                        if ctx.feedback:
                            extra['recovery_feedback'] = ctx.feedback[:800]
                        if ctx.approach:
                            extra['recovery_approach'] = ctx.approach[:600]
                        if ctx.subtask:
                            extra['recovery_part'] = ctx.subtask[:200]
                        state['verification'] = None
                        if ctx.attempt > 1:
                            phase('recovering', ctx.attempt, attempt=ctx.attempt)  # the Lead steps in; shown as such, never as plain programming
                        state['loop'] = run = run_loop(extra)
                        if str(run.stop_reason) == 'programmer_waiting':
                            return StageOutcome(True, dict(waiting=True))
                        if not (run.passed and run.final_patch):
                            return StageOutcome(False, failure=('; '.join(_blockers(run)) or str(run.stop_reason))[:700])
                        output = dict(loop=encode(run), plan=encode(plan), analysis=encode(analysis), verification=None)
                        made = programmer.last.metadata if programmer.last else {}
                        note = ('Programmer tidak menjalankan tes setelah edit terakhir; hanya verifikasi akhir yang '
                                'menjalankannya.' if made.get('sandbox_mode') == 'finished' and not made.get('sandbox_verified') else '')
                        if verifier is not None:
                            phase('verifying', ctx.attempt)
                            state['verification'] = report = verifier.verify(
                                project.root_path, run.final_patch.changes, verify.criteria_from(design))
                            output['verification'] = report.to_dict()
                            if report.status == 'failed':
                                return StageOutcome(False, failure=verify.failure_text(report))
                        return StageOutcome(True, output, compromise=note)

                    def replan(remaining, failure):
                        def again(ctx):
                            nonlocal plan, analysis
                            text = (task.description + '\n\nPercobaan sebelumnya gagal: ' + failure)[:3_900]
                            plan = Planner(engine).plan(PlanningRequest(**dict(ids, instruction=text)))
                            analysis = SystemAnalyst(engine).analyze(AnalysisRequest(**ids, plan=plan, focus=focus))
                            return deliver(ctx)
                        return [Stage('deliver_replanned', again)]

                    recovered = RecoveryEngine(
                        advisor=recover.advisor(self.author('lead') or self.author('analyst'), task.description),
                        replanner=replan, checkpoints=checkpoint, policy=recovery).run(
                            key, [Stage('deliver', deliver)], keep_checkpoint=True)
                    if recovered.resumed:
                        saved = recovered.outputs[recovered.completed[-1]]
                        loop, plan, analysis = decode(saved['loop']), decode(saved['plan']), decode(saved['analysis'])
                        verification = VerificationReport.from_dict(saved['verification']) if saved['verification'] else None
                    else:
                        loop, verification = state['loop'], state['verification']
                    if str(loop.stop_reason) == 'programmer_waiting':
                        checkpoint.clear(key)
                elif verifier is not None and loop.passed and loop.final_patch:
                    for attempt in range(1, attempts + 2):
                        phase('verifying', attempt)
                        verification = verifier.verify(project.root_path, loop.final_patch.changes, verify.criteria_from(design))
                        if verification.status != 'failed' or attempt > attempts or packages:
                            break
                        loop = run_loop(verify.failure_input(verification))
                        if not (loop.passed and loop.final_patch):
                            break
                if str(loop.stop_reason) == 'programmer_waiting':
                    # The Programmer needs a decision: it reaches the owner, and the answer returns to it.
                    asked = programmer.last
                    self._stop_for_owner(db, store, engine, run_id, task, 'clarification_required', asked.summary,
                                         [q for q in asked.unknowns if not q.startswith(SYSTEM_NOTES)][:6])
                    return None
                patch, qa, review = loop.final_patch, loop.final_qa_result, loop.final_review_result
                store.save_documents(run_id, task_id, programmer_documents(patch) + qa_documents(qa) + review_documents(review))
                changes = [dict(path=c.path, operation=str(c.operation), after_content=clip(c.after_content or ''))
                           for c in (patch.changes if patch else ())]
                work = dict(brief, patch=dict(summary=patch.summary, changes=changes) if patch else None,
                            qa=qa.to_dict() if qa else None, review=review.to_dict() if review else None,
                            loop=dict(iterations=len(loop.iterations), stop_reason=str(loop.stop_reason)),
                            design=[dict(document=d.spec.kind, text=clip(d.content)) for d in design
                                    if d.status is DocumentStatus.GENERATED])
                if patch and not repair:  # no code means nothing to write about, and a repair is judged by its build
                    for stage in ('programming', 'qa', 'reviewing', 'reporting'):
                        documents(stage, work)
                bundle = json.dumps({'version': 1, 'plan': encode(plan), 'analysis': encode(analysis),
                                     'loop': encode(loop)}, sort_keys=True, separators=(',', ':'))
                seal = self._sign(run_id, task_id, bundle)
                made = thaw(programmer.last.metadata) if getattr(programmer, 'last', None) else {}
                sandbox = {k[len('sandbox_'):]: v for k, v in made.items() if k.startswith('sandbox_')}
                silenced = ci_repair.silencing_reason(patch, project.root_path) if repair and loop.passed else None
                unproven = verifier is not None and loop.passed and not (verification and verification.passed)
                passed = loop.passed and not silenced and not unproven
                report = json.dumps(dict(plan=plan.to_dict(), analysis=analysis.to_dict(), loop=loop.to_dict(),
                                        **(dict(verification=verification.to_dict()) if verification else {}),
                                        **(dict(programmer_sandbox=sandbox) if sandbox else {}),
                                        **(dict(recovery=recovered.to_dict()) if recovered else {})))
                with db.transaction():
                    if self._closing or engine.get(task_id).status == 'cancelled':
                        raise Stopped()
                    db.conn.execute('UPDATE api_runs SET bundle=?,bundle_seal=?,report=? WHERE run_id=?',
                                    (bundle, seal, report, run_id))
                    if str(loop.stop_reason) == 'programmer_failed':
                        error = programmer.last.error or {}
                        engine.fail(task_id, TaskFailure('PROGRAMMER_FAILED', 'Programmer could not produce a proposal.', retryable=True,
                                                         details={'cause': str(error.get('code', 'unknown')),
                                                                  'reason': str(error.get('message', ''))[:300]}))
                        store.update(run_id, state='failed', phase='failed')
                        return None
                if recovered:
                    checkpoint.clear(key)  # the result is saved: a later attempt must not reuse it
                if silenced:
                    self._stop_for_owner(db, store, engine, run_id, task, 'quality_gate',
                                         'Perbaikan CI ini ditahan dan tidak dikirim, karena tampak menyembunyikan kegagalan.', [silenced])
                elif unproven:
                    self._stop_for_owner(db, store, engine, run_id, task, 'quality_gate',
                                         verify.blocked_message(verification) if verification else 'Verifikasi tidak berjalan.',
                                         verification.blockers() if verification else [],
                                         revisable=not recovered and bool(verification) and verification.status == 'failed')
                elif not loop.passed:
                    self._stop_for_owner(db, store, engine, run_id, task, 'quality_gate',
                                         f'QA dan Reviewer masih menahan hasil ini setelah {len(loop.iterations)} putaran. '
                                         'Beri arahan dan Programmer mengerjakannya lagi, atau batalkan task ini.',
                                         _blockers(loop), revisable=not recovered)
                else:
                    with db.transaction():
                        if self._closing or engine.get(task_id).status == 'cancelled':
                            raise Stopped()
                        engine.wait(task_id, reason='approval_required')
                        store.update(run_id, state='waiting', phase='awaiting_approval', wait_reason='approval_required')
                    self._publish_gate(db, store, engine, run_id, task, project, patch, verification, recovered, repair)
                return None
        except Resume as resume:
            return resume.mode
        except BaseException as exc:
            with Database(self.settings.database) as db:
                engine, store = TaskEngine(db, ProjectRegistry(db)), RunStore(db)
                row = store.get(run_id)
                with db.transaction():
                    task = engine.get(row['task_id'])
                    if task.status == 'cancelled':
                        store.update(run_id, state='cancelled', phase='cancelled')
                    elif isinstance(exc, Stopped) or self._closing:
                        if task.status == 'running':
                            engine.wait(task.task_id, reason='worker_interrupted')
                        store.update(run_id, state='interrupted', phase='interrupted',
                                     wait_reason='worker_interrupted')
                    else:
                        engine.fail(task.task_id, TaskFailure('PIPELINE_FAILED', 'Proposal pipeline failed.',
                                                             retryable=True,
                                                             details={'cause': str(getattr(exc, 'code', type(exc).__name__)),
                                                                      **({'stage': exc.stage, 'attempts': exc.attempts} if isinstance(exc, DesignFailed) else {})}))
                        store.update(run_id, state='failed', phase='failed', retry_reason=getattr(exc, 'code', None),
                                     attempt=getattr(exc, 'attempts', None))
        finally:
            done.set()
            if pulse.is_alive():
                pulse.join()
        return None

    @staticmethod
    def _checkpoint_key(task, spec, conversation, design):
        """Same task with the same brief, owner answers, and design: a retry may reuse a finished delivery."""
        brief = json.dumps([task.description, spec, conversation, [d.content for d in design]], sort_keys=True,
                           default=str)
        return f"{task.task_id}-{hashlib.sha256(brief.encode()).hexdigest()[:16]}"

    # -- the gates: what passes without the owner, and the record of it -------------------------

    def _decide(self, store, run_id, task_id, gate, outcome, actor, *, risk='', reasons=(), payload=None, evidence=''):
        store.add_decision(run_id, task_id, sign=self._sign, gate=str(gate), level=self.autonomy().level, risk=str(risk),
                           outcome=outcome, actor=actor, reasons=reasons, payload=payload, evidence=evidence)

    def decisions(self, store, task_id):
        return store.decisions(task_id, sign=self._sign)

    def _still_ours(self, engine, task_id):
        if self._closing or engine.get(task_id).status == 'cancelled':
            raise Stopped()

    def _design_gate(self, db, store, engine, run_id, task):
        """Every design stops here and is assessed. Returns ``'code'`` when the policy passes it."""
        policy, packages = self.autonomy(), store.work_packages(run_id)
        failed = any(d['kind'] == 'work_packages' and d['status'] == 'failed' for d in store.documents(task.task_id, run_id))
        verdict = policy.design(assess_brief(task.description, [o for p in packages for o in json.loads(p['owns'])], len(packages)),
                                division_failed=failed)
        with db.transaction():
            self._still_ours(engine, task.task_id)
            self._decide(store, run_id, task.task_id, Gate.DESIGN, 'auto' if verdict.allowed else 'held',
                         AUTONOMY_ACTOR, risk=verdict.risk, reasons=verdict.reasons, evidence=store.documents_digest(run_id))
            engine.wait(task.task_id, reason='design_approval')
            store.update(run_id, state='waiting', phase='design_approval', wait_reason='design_approval')
            if verdict.allowed:  # the same record a person leaves, under the policy's own name
                store.record_design_decision(run_id, 'approve', verdict.approval_reason, AUTONOMY_ACTOR)
                store.update(run_id, state='scheduled', phase='queued')
        if verdict.allowed:
            return 'code'
        if policy.gate_open(Gate.DESIGN):
            store.add_message(task.project_id, task.task_id, 'lead', dict(message=autonomy.held_message(verdict), questions=[]))
        return None

    def _publish_gate(self, db, store, engine, run_id, task, project, patch, verification, recovered, repair):
        """Judged on the patch itself. Every run records its risk: a later merge reads it, whoever approved."""
        policy = self.autonomy()
        try:
            risk = autonomy.patch_risk(patch, project.root_path)
        except Exception as exc:  # noqa: BLE001 - the run is already waiting for approval: it stays there, held
            risk = Risk(RiskLevel.HIGH, (f'Penilaian risiko gagal ({type(exc).__name__}).',))
        if repair:
            self._decide(store, run_id, task.task_id, Gate.PUBLISH, 'auto', ci_repair.CI_REPAIR_ACTOR, risk=risk.level,
                         reasons=risk.reasons, evidence=patch.content_hash)
            return self._approve_repair(db, store, engine, run_id)
        verdict = policy.publish(risk, test_passed=autonomy.test_passed(verification), has_branch=bool(store.branch(task.task_id)),
                                 compromises=autonomy.compromises(recovered))
        self._decide(store, run_id, task.task_id, Gate.PUBLISH, 'auto' if verdict.allowed else 'held', AUTONOMY_ACTOR,
                     risk=verdict.risk, reasons=verdict.reasons, evidence=patch.content_hash)
        if verdict.allowed:
            return self._approve_by_policy(db, store, engine, run_id, AUTONOMY_ACTOR, verdict.approval_reason,
                                           'Hasil ini lolos kebijakan otomatis tetapi tidak lolos pemeriksaan bukti, jadi menunggu keputusan Anda.')
        if policy.gate_open(Gate.PUBLISH):
            store.add_message(task.project_id, task.task_id, 'lead', dict(message=autonomy.held_message(verdict), questions=[]))
        return None

    def _merge_gate(self, store, task, sha, runs, ci_state):
        """Level 3 only. Judged on what GitHub says now about the whole branch; a refusal leaves the pull request open."""
        policy = self.autonomy()
        if not policy.gate_open(Gate.MERGE):
            return
        task_id, run_id = task.task_id, store.latest(task.task_id)['run_id']
        repo, branch = store.repo(task.project_id), store.branch(task_id)

        def tell(message):
            for target in {task_id, (ci_repair.info(task) or {}).get('root_task_id', task_id)}:
                store.add_message(task.project_id, target, 'lead', dict(message=message, questions=[]))

        def held(reasons):
            self._decide(store, run_id, task_id, Gate.MERGE, 'held', AUTONOMY_ACTOR, reasons=reasons, evidence=f'commit:{sha}')
            tell('Kebijakan otomatis tidak melakukan merge. Pull request menunggu keputusan Anda.\n' + '\n'.join(f'- {r}' for r in reasons[:8]))

        try:
            pull = self._repos.pull_request_details(repo['repository'], branch['branch'])
        except RepoError as exc:
            return held([f'Status pull request tidak terbaca ({exc.code}).'])
        if pull is None:
            return held(['Pull request tidak ditemukan.'])
        if pull.state != 'OPEN':
            return None  # merged or closed: the owner already decided
        verdict = policy.merge(autonomy.branch_risk(store, store.branch_tasks(task.project_id, branch['branch']), pull),
                               ci_state=ci_state, ci_passed=ci_state == 'success' and autonomy.ci_passed(runs),
                               open_at_tested_commit=pull.head_sha == sha and not pull.draft,
                               base_is_default=pull.base == repo['default_branch'], reviewers_object=pull.review == 'CHANGES_REQUESTED')
        if not verdict.allowed:
            self._decide(store, run_id, task_id, Gate.MERGE, 'held', AUTONOMY_ACTOR, risk=verdict.risk, reasons=verdict.reasons,
                         evidence=f'commit:{sha}')
            tell(autonomy.held_message(verdict))
            return None
        # Written before the merge: an action this hard to undo must never happen without a record.
        self._decide(store, run_id, task_id, Gate.MERGE, 'auto', AUTONOMY_ACTOR, risk=verdict.risk, reasons=verdict.reasons,
                     evidence=f'commit:{sha}', payload=dict(pull_request=pull.number, method=policy.merge_method))
        try:
            self._repos.merge_pull_request(repo['repository'], pull.number, head_sha=sha, method=policy.merge_method)
        except RepoError as exc:
            self._decide(store, run_id, task_id, Gate.MERGE, 'merge_failed', AUTONOMY_ACTOR, risk=verdict.risk,
                         reasons=(exc.code,), evidence=f'commit:{sha}')
            return tell(f'GitHub menolak merge otomatis ({exc.code}). Pull request tetap terbuka untuk Anda.')
        return tell(f'Pull request #{pull.number} di-merge otomatis setelah CI lulus. Dasarnya kebijakan level {policy.level}, '
                    f'risiko {verdict.risk}. Commit {sha[:12]}.')

    # -- the Lead: every stop short of an approval passes here ----------------------------------

    def _lead_thinking(self, db, store, engine, run_id, task):
        with db.transaction():
            self._still_ours(engine, task.task_id)
            store.update(run_id, state='running', phase='deciding')

    def _resume(self, db, store, engine, run_id, task, reason):
        with db.transaction():
            self._still_ours(engine, task.task_id)
            engine.wait(task.task_id, reason=reason)
            store.update(run_id, state='scheduled', phase='queued')
        # As an answer from the owner would: an approved design stays approved.
        raise Resume('code' if store.design_approved(run_id) else 'start')

    def _lead_questions(self, db, store, engine, run_id, task, message, questions):
        """Returns what still has to reach the owner. Raises ``Resume`` when nothing does."""
        policy, router = self.autonomy(), self.author('lead')
        if not policy.lead or router is None or not questions:
            return message, questions
        self._lead_thinking(db, store, engine, run_id, task)
        answered = [d for d in store.decisions(task.task_id) if d['gate'] == 'question' and d['outcome'] == 'answered']
        sources = autonomy.sources(store, task)
        resolved = settle(router, questions, sources, policy.lead_answers - len(answered),
                          frozenset(d['payload']['key'] for d in answered))
        evidence = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        with db.transaction():
            self._still_ours(engine, task.task_id)
            for r in resolved:
                self._decide(store, run_id, task.task_id, 'question', 'answered' if r.answered else 'forwarded', LEAD_ACTOR,
                             payload=r.to_dict(), evidence=evidence)
        done = [r for r in resolved if r.answered]
        left = [dict(r.question, lead_reason=r.reason) for r in resolved if not r.answered]
        if done:
            store.add_message(task.project_id, task.task_id, 'lead', dict(
                message=autonomy.answered_message(done), questions=[], resolved=[r.to_dict() for r in done]))
        if not left:
            self._resume(db, store, engine, run_id, task, 'clarification_required')
        return message, left

    def _lead_next_step(self, db, store, engine, run_id, task, blockers):
        """Send held work back with a direction, within a budget. Returns why the owner is needed, or ``''``."""
        policy, router = self.autonomy(), self.author('lead')
        if not policy.lead or router is None or not blockers or not policy.lead_revisions:
            return ''
        revisions = [d for d in store.decisions(task.task_id) if d['gate'] == 'next_step' and d['outcome'] == 'revise']
        mark = blockers_key(blockers)
        if any(d['payload']['blockers'] == mark for d in revisions):
            step = Step('ask_owner', reason='Arahan sebelumnya tidak mengubah temuannya, jadi mengulang tidak akan membantu.')
        elif len(revisions) >= policy.lead_revisions:
            step = Step('ask_owner', reason=f'Lead sudah mengembalikan pekerjaan ini {len(revisions)} kali.')
        else:
            self._lead_thinking(db, store, engine, run_id, task)
            step = next_step(router, task.description, blockers)
        self._decide(store, run_id, task.task_id, 'next_step', step.action if step.action == 'revise' else 'forwarded', LEAD_ACTOR,
                     reasons=(step.reason,) if step.reason else (), payload=dict(blockers=mark, direction=step.direction))
        if step.action != 'revise':
            return step.reason
        store.add_message(task.project_id, task.task_id, 'lead', dict(
            message=f'Arahan Lead untuk Programmer: {step.direction}', questions=[]))
        return self._resume(db, store, engine, run_id, task, 'quality_gate')

    def _stop_for_owner(self, db, store, engine, run_id, task, reason, message, questions, *, revisable=False, lead=True):
        """The one way a run reaches the owner. ``revisable``: more work could clear this, so the Lead may try that first."""
        questions = autonomy.as_questions(questions)
        if lead and reason == 'clarification_required':
            message, questions = self._lead_questions(db, store, engine, run_id, task, message, questions)
        elif lead and revisable:
            why = self._lead_next_step(db, store, engine, run_id, task, [q['question'] for q in questions])
            message = f'{message}\n\nAlasan Lead meneruskan ini ke Anda: {why}' if why else message
        store.add_message(task.project_id, task.task_id, 'lead', dict(message=message, questions=questions[:12]))
        with db.transaction():
            if self._closing or engine.get(task.task_id).status == 'cancelled':
                raise Stopped()
            engine.wait(task.task_id, reason=reason)
            store.update(run_id, state='waiting', phase='clarification' if reason == 'clarification_required' else 'blocked',
                         wait_reason=reason)

    def _deliver_packages(self, db, store, engine, run_id, task, ids, plan, project, spec, conversation, design):
        """Returns the integrated loop, or ``None`` when the owner was asked to step in."""
        base = design_input(design, budget=INPUT_DESIGN_BYTES) | ({'project_spec': clip_bytes(spec, SPEC_BYTES)} if spec else {})
        if conversation:
            base |= {'owner_conversation': conversation_tail(conversation)}
        run = PackageRun(self, run_id, ids, plan, base, self._repos.files(project.root_path), self.parallel_limit())
        for attempt in (1, 2):
            try:
                rows = run.execute(store, project.root_path)
            except PackageStopped:
                raise Stopped() from None
            unfinished = [r for r in rows if r['state'] != 'passed']
            if unfinished:
                asked = [f"{r['title']}: {q}" for r in unfinished if r['state'] == 'waiting' for q in json.loads(r['questions'] or '[]')]
                if asked and all(r['state'] == 'waiting' for r in unfinished):
                    self._stop_for_owner(db, store, engine, run_id, task, 'clarification_required',
                                         'Ada paket kerja yang butuh keputusan Anda. Paket yang sudah selesai tetap disimpan.', asked)
                    return None
                if all(r['error'] == 'rate_limited' or r['state'] == 'passed' for r in unfinished):
                    self._stop_for_owner(db, store, engine, run_id, task, 'quality_gate',
                                         'Batas langganan Claude tercapai, jadi paket ini belum dikerjakan. Paket yang sudah lulus '
                                         'tetap disimpan. Klik Lanjutkan setelah batasnya pulih; tidak ada pekerjaan yang diulang.', [])
                    return None
                held = [f"{r['title']}: {q}" for r in unfinished for q in json.loads(r['questions'] or '[]')]
                held += [f"{r['title']}: gagal ({r['error']})" for r in unfinished if r['state'] == 'failed']
                self._stop_for_owner(db, store, engine, run_id, task, 'quality_gate',
                                     'Sebagian paket kerja belum lulus. Paket yang sudah lulus tetap disimpan; '
                                     'beri arahan dan hanya paket ini yang dikerjakan lagi.', held, revisable=True)
                return None
            patches = [decode(json.loads(r['patch'])) for r in rows]
            summary = 'Gabungan paket kerja: ' + '; '.join(r['title'] for r in rows)
            try:
                merged = merge_patches(task.task_id, task.project_id, patches, summary)
            except Exception as exc:  # noqa: BLE001 - the limit of one patch: the owner decides how to cut the work
                self._stop_for_owner(db, store, engine, run_id, task, 'quality_gate',
                                     'Gabungan paket kerja melebihi batas satu patch, jadi tidak bisa disatukan.',
                                     [str(getattr(exc, 'message', type(exc).__name__))])
                return None
            owns = [o for r in rows for o in json.loads(r['owns'])]
            analysis = SystemAnalyst(engine).analyze(AnalysisRequest(
                **ids, plan=plan, focus=tuple(c.path for c in merged.changes)[:20], scope=tuple(dict.fromkeys(owns))[:20]))

            def stage(cls, method, name):
                class Tracked(cls):
                    pass
                inner = getattr(cls, method)

                def call(self, request):
                    with db.transaction():
                        if self._tasks.get(task.task_id).status == 'cancelled':
                            raise Stopped()
                        store.update(run_id, state='running', phase=name, iteration=1)
                    return inner(self, request)
                setattr(Tracked, method, call)
                return Tracked

            programmer = Merged(engine, merged, summary)
            loop = LoopService(engine, programmer, stage(QAService, 'verify', 'qa')(engine, policy=QA_POLICY),
                               stage(ReviewerService, 'review', 'reviewing')(engine, policy=INTEGRATION_REVIEW),
                               policy=ONE_ROUND).run(LoopRequest(**ids, plan=plan, analysis=analysis, input=dict(base)))
            if loop.passed or attempt == 2:
                return loop, analysis, programmer
            run.add_fix(store, _blockers(loop), owns[:12])
            store.update(run_id, state='running', phase='programming', iteration=2)
        return None

    def _watch_ci(self, task_id):
        """Follow one commit's workflow runs until they end. Only GitHub's report fills the measured documents."""
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            ci, branch = store.ci(task_id), store.branch(task_id)
            if not ci or not branch:
                return
            task = engine.get(task_id)
            repository = store.repo(task.project_id)['repository']
            begun, sha = now_seconds(), ci['commit_sha']
            while not self._closing:
                try:
                    runs = self._repos.ci_runs(repository, branch['branch'], sha)
                except RepoError:
                    runs = None  # GitHub may be unreachable for a moment: the deadline still ends this
                waited = now_seconds() - begun
                if runs:
                    done = all(r['status'] == 'completed' for r in runs)
                    failed = [r for r in runs if r['conclusion'] not in ('success', 'skipped', 'neutral')]
                    if done:
                        log = None
                        if failed:
                            try:
                                log = redact(self._repos.ci_failed_log(repository, failed[0]['id']))
                            except RepoError:
                                log = None
                        store.set_ci(task_id, sha, 'failed' if failed else 'success', runs, log)
                        self._record_ci(store, task_id, runs, log)
                        if failed:
                            try:
                                self._repair_ci(db, store, engine, task, sha, runs, log)
                            except Exception as exc:  # noqa: BLE001 - a background job has no caller: the owner hears of it
                                store.add_message(task.project_id, task_id, 'lead', dict(
                                    message=f'Perbaikan otomatis gagal dimulai ({type(exc).__name__}). Pull request tetap terbuka.',
                                    questions=[]))
                        else:
                            self._note_ci_recovered(store, task)
                            self._try_merge(store, task, sha, runs, 'success')
                        return
                    store.set_ci(task_id, sha, 'watching', runs)
                elif runs == [] and waited > CI_NO_WORKFLOW_SECS:
                    store.set_ci(task_id, sha, 'none')
                    self._try_merge(store, task, sha, (), 'none')
                    return
                if waited > CI_TIMEOUT_SECS:
                    store.set_ci(task_id, sha, 'timeout', runs or ())
                    self._try_merge(store, task, sha, runs or (), 'timeout')
                    return
                self._ci_wake.wait(self.ci_poll_secs)

    ci_poll_secs = CI_POLL_SECS

    def _try_merge(self, store, task, sha, runs, ci_state):
        try:
            self._merge_gate(store, task, sha, runs, ci_state)
        except Exception as exc:  # noqa: BLE001 - a background job has no caller: the owner hears of it, and nothing merged
            store.add_message(task.project_id, task.task_id, 'lead', dict(
                message=f'Pemeriksaan merge otomatis gagal ({type(exc).__name__}). Pull request tetap terbuka.', questions=[]))

    @staticmethod
    def _note_ci_recovered(store, task):
        repair = ci_repair.info(task)
        if repair:
            message = f"CI lulus setelah {repair['attempt']} perbaikan otomatis. Pull request menunggu keputusan Anda."
            for task_id in {task.task_id, repair['root_task_id']}:
                store.add_message(task.project_id, task_id, 'lead', dict(message=message, questions=[]))

    def _repair_ci(self, db, store, engine, task, sha, runs, log):
        """Send a failed build back to the Programmer, or say why it stops here. The pull request stays open either way."""
        limit = self.ci_repair_limit()
        if not limit:
            return
        previous = ci_repair.info(task)
        attempt = (previous['attempt'] if previous else 0) + 1

        def stop(message):
            store.add_message(task.project_id, task.task_id, 'lead', dict(message=message, questions=[]))

        if not log:
            return stop('CI gagal, tetapi log-nya tidak bisa dibaca, jadi tidak ada dasar untuk memperbaikinya otomatis.')
        if attempt > limit:
            return stop(f'CI masih gagal setelah {limit} perbaikan otomatis. Pull request tetap terbuka untuk Anda periksa.')
        mark = ci_repair.fingerprint(log)
        if previous and previous['fingerprint'] == mark:
            return stop('Perbaikan otomatis dihentikan: CI gagal dengan cara yang sama, jadi mengulang tidak akan membantu.')
        repo, branch, root = store.repo(task.project_id), store.branch(task.task_id), task_root(store, task)
        try:
            pull = self._repos.find_pull_request(repo['repository'], branch['branch'])
        except RepoError as exc:
            return stop(f'Perbaikan otomatis tidak dimulai, karena status pull request tidak terbaca ({exc.code}).')
        if pull is None or pull.state != 'OPEN':
            return None  # merged or closed: the owner already decided
        self._repos.refresh(root)
        if self._repos.revision(root, branch['branch']) != sha:
            return stop('Perbaikan otomatis tidak dimulai, karena branch ini sudah berubah sejak build tersebut.')
        jobs = ci_repair.failed_jobs(runs)
        with db.transaction():
            created = engine.create(TaskRequest.from_dict(dict(
                project_id=task.project_id, source='system', request_id=f'req_ci_{sha[:24]}',
                description=ci_repair.description(task.title or task.description.splitlines()[0], attempt, limit, jobs),
                metadata={ci_repair.METADATA_KEY: dict(attempt=attempt, fingerprint=mark, failed_task_id=task.task_id,
                                                       root_task_id=previous['root_task_id'] if previous else task.task_id)}))).task
            self.assign_branch(store, created, task.task_id)
        begun = now_seconds()
        while not self._closing and now_seconds() - begun < CI_TIMEOUT_SECS:
            try:
                self.schedule(created.task_id)
                return None
            except RuntimeRefusal as exc:
                if exc.code in ('task_not_queued', 'task_already_has_run'):
                    return None
                if exc.code != 'worker_busy':
                    return stop(f'Perbaikan otomatis tidak bisa dijalankan ({exc.code}).')
            self._ci_wake.wait(self.ci_poll_secs)
        if not self._closing:
            stop('Perbaikan otomatis terlalu lama menunggu giliran. Task perbaikannya masih antre dan bisa dijalankan manual.')
        return None

    @staticmethod
    def _record_ci(store, task_id, runs, log):
        run_id = (store.latest(task_id) or {}).get('run_id')
        jobs = [f"- {r['name']} / {j['name']}: **{j['conclusion'] or j['status']}**" for r in runs for j in r['jobs']]
        report = '\n'.join([f"[{r['name']}]({r['url']}): **{r['conclusion']}**" for r in runs] + ['', *jobs])
        artifacts = [f"- [{a['name']}]({r['url']}) ({a['size']} byte)" for r in runs for a in r['artifacts'] if not a['expired']]
        store.save_documents(run_id, task_id, (
            Document(SPECS['ci_results'], DocumentStatus.GENERATED, report + (f'\n\n```\n{log}\n```' if log else '')),
            Document(SPECS['build_artifacts'], DocumentStatus.GENERATED, '\n'.join(artifacts)) if artifacts else
            Document(SPECS['build_artifacts'], DocumentStatus.NOT_RUN, reason='Workflow tidak mengunggah artefak.'),))

    def _focus(self, project, documents, task, log=''):
        """Which files the Programmer reads. Chosen here from the tracked list, never by an API caller."""
        limit = MODEL_POLICY.max_context_files
        names = source_files(self._repos.files(project.root_path))
        if len(names) <= limit:
            return tuple(names)
        texts = [d.content for d in documents if d.status is DocumentStatus.GENERATED] + ([log] if log else [])
        chosen = choose_files(self.author('analyst'), dict(
            instruction=task.description, design=[clip(t, 3_000) for t in texts]), names, limit)
        in_log = [n for n in names if n in log]  # a failing build names its own files
        return tuple(dict.fromkeys(in_log + list(chosen or named_in(names, texts))))[:limit]

    def _sign(self, run_id, task_id, bundle):
        return hmac.new(self.settings.seal_key, f'{run_id}\n{task_id}\n{bundle}'.encode(), hashlib.sha256).hexdigest()

    def load_bundle(self, row):
        if not row['bundle'] or not hmac.compare_digest(
                self._sign(row['run_id'], row['task_id'], row['bundle']), row['bundle_seal'] or ''):
            raise RuntimeRefusal('evidence_invalid')
        try:
            data = json.loads(row['bundle'])
            if set(data) != {'version', 'plan', 'analysis', 'loop'} or data['version'] != 1:
                raise ValueError('artifact version')
            plan, analysis, loop = (decode(data[k]) for k in ('plan', 'analysis', 'loop'))
            from siamang.planner.plan import Plan
            from siamang.analyst.analysis import SystemAnalysis
            from siamang.loop.result import LoopResult
            if type(plan) is not Plan or type(analysis) is not SystemAnalysis or type(loop) is not LoopResult:
                raise ValueError('artifact type')
            if any(v.task_id != row['task_id'] or v.project_id != plan.project_id for v in (plan, analysis, loop)):
                raise ValueError('artifact binding')
            return plan, analysis, loop
        except Exception:
            raise RuntimeRefusal('evidence_invalid') from None

    def evidence(self, row):
        plan, analysis, loop = self.load_bundle(row)
        try:
            return ApprovalRequest(row['task_id'], plan.project_id, plan, analysis, loop)
        except Exception:
            raise RuntimeRefusal('evidence_invalid') from None

    def decide(self, run_id, principal, decision, reason):
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            with db.transaction():
                row = store.get(run_id)
                if not row:
                    raise RuntimeRefusal('not_found', 404)
                task = engine.get(row['task_id'])
                if not principal.allows_project(task.project_id):
                    raise RuntimeRefusal('forbidden', 403)
                request = self.evidence(row)
                decision_input = json.dumps({'decision': decision, 'reason': reason}, sort_keys=True)
                if row['decision']:
                    sealed = json.dumps([row['decision'], row['decision_input'], row['decision_actor']], separators=(',', ':'))
                    if not hmac.compare_digest(self._sign(run_id, row['task_id'], sealed), row['decision_seal'] or ''):
                        raise RuntimeRefusal('decision_invalid')
                    if row['decision_actor'] == principal.identity and row['decision_input'] == decision_input:
                        return json.loads(row['decision'])
                    raise RuntimeRefusal('approval_already_decided')
                if row['state'] != 'waiting' or task.status != 'waiting':
                    raise RuntimeRefusal('approval_not_pending')
                policy = ApprovalPolicy(authorized_approvers={p.identity for p in self.settings.principals
                                                              if 'approve' in p.permissions})
                decided, publish = self._record_decision(db, store, engine, run_id, row, task, request, policy,
                                                         principal.identity, decision, reason, decision_input)
            if publish:
                self._submit_publish(run_id)
            return decided

    def _record_decision(self, db, store, engine, run_id, row, task, request, policy, actor, decision, reason, decision_input):
        service = ApprovalService(engine, policy=policy, seal_key=self.settings.seal_key)
        result = service.decide(request, ApprovalDecision(decision, actor, reason))
        service.verify(result, request)
        stored = json.dumps(result.to_dict())
        signed = json.dumps([stored, decision_input, actor], separators=(',', ':'))
        db.conn.execute('UPDATE api_runs SET decision=?,decision_input=?,decision_actor=?,decision_seal=? WHERE run_id=?',
                        (stored, decision_input, actor, self._sign(run_id, row['task_id'], signed), run_id))
        publish = bool(result.approved and store.branch(task.task_id))
        if result.approved:
            engine.start(task.task_id)
            summary = 'Proposal approved; publishing to a branch.' if publish else 'Proposal approved; patch not applied.'
            engine.complete(task.task_id, TaskResult(summary, details={'proposal_approved': True, 'patch_applied': False}))
            store.update(run_id, state='completed', phase='finished')
            if publish:
                store.update_branch(task.task_id, publication='publishing', error=None)
        else:
            engine.cancel(task.task_id, reason='Proposal rejected by authorized approver.')
            store.update(run_id, state='cancelled', phase='cancelled')
        return result.to_dict(), publish

    def _approve_repair(self, db, store, engine, run_id):
        return self._approve_by_policy(db, store, engine, run_id, ci_repair.CI_REPAIR_ACTOR,
                                       'CI repair passed QA and review. It reaches the open pull request; the owner still decides the merge.',
                                       'Perbaikan CI ini tidak lolos persetujuan otomatis, jadi menunggu keputusan Anda.')

    def _approve_by_policy(self, db, store, engine, run_id, actor, reason, refused):
        """Evidence that passed still goes through approval, under the policy's own name. A refusal stays with the owner."""
        decision_input = json.dumps({'decision': 'approve', 'reason': reason}, sort_keys=True)
        try:
            with db.transaction():
                row = store.get(run_id)
                task = engine.get(row['task_id'])
                if row['state'] != 'waiting' or task.status != 'waiting':
                    return
                _, publish = self._record_decision(db, store, engine, run_id, row, task, self.evidence(row),
                                                   ApprovalPolicy(authorized_approvers={actor}),
                                                   actor, 'approve', reason, decision_input)
        except Exception:  # noqa: BLE001 - nothing was published: the proposal waits for the owner's decision instead
            task = engine.get(store.get(run_id)['task_id'])
            store.add_message(task.project_id, task.task_id, 'lead', dict(message=refused, questions=[]))
            return
        if publish:
            self._submit_publish(run_id)

    # -- GitHub projects -----------------------------------------------------------------

    def _workspace(self, project_id):
        return self.settings.database.parent / 'workspaces' / project_id

    def create_project(self, name, repository):
        """Register now, clone in the background. The clone directory always belongs to Siamang."""
        with Database(self.settings.database) as db:
            store, registry = RunStore(db), ProjectRegistry(db)
            with db.transaction():
                project_id = new_id('project')
                project = registry.register(name=name, project_id=project_id, root_path=str(self._workspace(project_id)),
                                            metadata={'repository': repository})
                store.add_repo(project_id, repository)
                store.set_spec(project_id, state='thinking')
        self._submit_background(self._clone, project.project_id, repository)
        return project

    def _submit_background(self, job, *args):
        if self._closing or not self._background:
            raise RuntimeRefusal('runtime_unavailable', 503)
        self._background.submit(job, *args)

    def retry_clone(self, project_id):
        with Database(self.settings.database) as db:
            store = RunStore(db)
            repo = store.repo(project_id)
            if not repo:
                raise RuntimeRefusal('not_found', 404)
            if repo['state'] != 'failed':
                raise RuntimeRefusal('clone_not_failed')
            with db.transaction():
                store.update_repo(project_id, state='cloning', error=None)
                store.set_spec(project_id, state='thinking')
        self._submit_background(self._clone, project_id, repo['repository'])

    def _clone(self, project_id, repository):
        with Database(self.settings.database) as db:
            store = RunStore(db)
            dest = self._workspace(project_id)
            try:
                info = self._repos.info(repository)
                if not info.can_write:
                    raise RepoError('repository_not_writable')
                if dest.exists():
                    shutil.rmtree(dest)  # only ever a failed attempt: this path is Siamang's own
                dest.parent.mkdir(parents=True, exist_ok=True)
                self._repos.clone(info.repository, dest)
                if info.default_branch is None:
                    name, email = self._repos.identity()
                    self._repos.seed_empty(dest, ProjectRegistry(db).get(project_id).name, author_name=name, author_email=email)
                    info = replace(info, default_branch='main')
            except RepoError as exc:
                with db.transaction():
                    store.update_repo(project_id, state='failed', error=exc.code)
                    store.set_spec(project_id, state='failed', error=exc.code)
                return
            with db.transaction():
                store.update_repo(project_id, repository=info.repository, default_branch=info.default_branch,
                                  private=int(info.private), state='ready', error=None)
            self._lead_turn(db, project_id)

    def post_intake(self, project_id, content):
        with Database(self.settings.database) as db:
            store = RunStore(db)
            repo, spec = store.repo(project_id), store.spec(project_id)
            if not repo or not spec:
                raise RuntimeRefusal('not_found', 404)
            if repo['state'] != 'ready':
                raise RuntimeRefusal('repository_not_ready')
            if spec['state'] == 'thinking':
                raise RuntimeRefusal('lead_busy')
            store.add_message(project_id, None, 'owner', {'message': content})
            store.set_spec(project_id, state='thinking')
        self._submit_background(self._lead_turn_job, project_id)

    def _lead_turn_job(self, project_id):
        with Database(self.settings.database) as db:
            self._lead_turn(db, project_id)

    def _lead_turn(self, db, project_id):
        store = RunStore(db)
        try:
            router = self.author('lead')
            if router is None:
                raise RepoError('lead_has_no_model')
            history = store.messages(project_id)
            rounds = sum(1 for m in history if m['role'] == 'lead')
            project, spec = ProjectRegistry(db).get(project_id), store.spec(project_id)
            reply = ask_lead(router, INTAKE_PROMPT, dict(
                project=dict(name=project.name, repository=store.repo(project_id)['repository']),
                repository=read_latest(self._repos, project.root_path, store.repo(project_id)['default_branch']),
                conversation=history,
                current_spec=spec['content'], rounds_left=max(MAX_INTAKE_ROUNDS - rounds, 0)))
        except (ModelError, RepoError) as exc:
            store.set_spec(project_id, state='failed', error=str(exc.code))
            return
        except Exception as exc:  # noqa: BLE001 - a background job has no caller: record it instead of losing it
            store.set_spec(project_id, state='failed', error=type(exc).__name__)
            return
        store.add_message(project_id, None, 'lead', reply.payload())
        if reply.ready and reply.spec.strip():
            store.set_spec(project_id, state='pending_approval', content=redact(reply.spec))
        else:
            store.set_spec(project_id, state='gathering')

    def approve_spec(self, project_id, principal):
        with Database(self.settings.database) as db:
            store = RunStore(db)
            spec = store.spec(project_id)
            if not spec:
                raise RuntimeRefusal('not_found', 404)
            if spec['state'] != 'pending_approval':
                raise RuntimeRefusal('spec_not_pending')
            store.set_spec(project_id, state='approved', approved_by=principal.identity)
            return store.spec(project_id)

    def assign_branch(self, store, task, revises_task_id):
        """Every task of a GitHub project gets a branch. A run decides later if a revision can reuse its base's."""
        store.add_branch(task.task_id, task.project_id, branch_name(task.task_id, task.title or task.description),
                         revises_task_id)

    def _prepare_branch(self, store, repo, task):
        row = store.branch(task.task_id)
        if not row:
            raise RepoError('task_has_no_branch')
        branch = row['branch']
        if row['revises_task_id']:
            base = store.branch(row['revises_task_id'])
            pull = self._repos.find_pull_request(repo['repository'], base['branch'])
            if pull and pull.state == 'OPEN':  # after a merge or close, the task keeps its own fresh branch
                branch = base['branch']
                with store.db.transaction():
                    store.update_branch(task.task_id, branch=branch)
        self._repos.prepare(task_root(store, task), branch, repo['default_branch'])

    def _submit_publish(self, run_id):
        if self._closing or not self._pool:
            with Database(self.settings.database) as db:
                store = RunStore(db)
                with db.transaction():
                    store.update_branch(store.get(run_id)['task_id'], publication='failed', error='runtime_unavailable')
            return
        self._pool.submit(self._publish, run_id)  # the same single worker: the clone has one user at a time

    def retry_publish(self, task_id):
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            row, branch = store.latest(task_id), store.branch(task_id)
            if not branch or not row or engine.get(task_id).status != 'completed':
                raise RuntimeRefusal('publish_not_available')
            if branch['publication'] != 'failed':
                raise RuntimeRefusal('publish_not_failed')
            with db.transaction():
                store.update_branch(task_id, publication='publishing', error=None)
        self._submit_publish(row['run_id'])

    def _publish(self, run_id):
        """Approved evidence only: the patch comes from the sealed bundle, never from a request."""
        with Database(self.settings.database) as db:
            store = RunStore(db)
            row = store.get(run_id)
            task_id = row['task_id']
            try:
                task = TaskEngine(db, ProjectRegistry(db)).get(task_id)
                if not row['decision'] or json.loads(row['decision']).get('status') != 'approved':
                    raise RepoError('not_approved')
                _, _, loop = self.load_bundle(row)
                patch, repo, branch = loop.final_patch, store.repo(task.project_id), store.branch(task_id)
                root = task_root(store, task)
                self._repos.prepare(root, branch['branch'], repo['default_branch'])
                title = (task.title or task.description).splitlines()[0][:72]
                sha = branch['commit_sha']
                if not sha:  # a retry after the push only still owes the pull request
                    paths = write_changes(root, patch)
                    name, email = self._repos.identity()
                    groups = commit_groups(store.work_packages(run_id), patch) or [(title, paths)]
                    for label, group in groups:
                        self._repos.commit_paths(root, branch['branch'], base=repo['default_branch'], paths=group,
                                           message=f'{label}\n\n{patch.summary}', author_name=name, author_email=email)
                    sha = self._repos.push(root, branch['branch'])
                    with db.transaction():
                        store.update_branch(task_id, commit_sha=sha)
                pull = self._repos.find_pull_request(repo['repository'], branch['branch'])
                if pull is None or pull.state != 'OPEN':
                    pull = self._repos.open_pull_request(repo['repository'], branch['branch'], base=repo['default_branch'],
                                                         title=title, body=_pull_request_body(task, patch, loop, recover.debt_of(row['report']),
                                                                                              row['decision_actor'], row['report']))
            except RepoError as exc:
                with db.transaction():
                    store.update_branch(task_id, publication='failed', error=exc.code)
            except Exception as exc:  # noqa: BLE001 - a background job has no caller: record it instead of losing it
                with db.transaction():
                    store.update_branch(task_id, publication='failed', error=type(exc).__name__)
            else:
                with db.transaction():
                    store.update_branch(task_id, publication='published', error=None, commit_sha=sha,
                                        pr_number=pull.number, pr_url=pull.url)
                store.set_ci(task_id, sha, 'watching')
                if self._ci_pool and not self._closing:
                    self._ci_pool.submit(self._watch_ci, task_id)

    # -- the conversation before a task exists ---------------------------------------------

    def create_draft(self, project_id, content, options):
        with Database(self.settings.database) as db:
            store = RunStore(db)
            ProjectRegistry(db).get(project_id)
            repo = store.repo(project_id)
            if repo and (repo['state'] != 'ready' or (store.spec(project_id) or {}).get('state') != 'approved'):
                raise RuntimeRefusal('spec_not_approved')
            if self.author('lead') is None:
                raise RuntimeRefusal('lead_has_no_model', 503)
            draft_id = store.add_draft(project_id, content, options)
        self._submit_background(self._draft_turn, draft_id)
        return draft_id

    def post_draft(self, draft, content):
        if draft['state'] in ('thinking', 'created', 'discarded'):
            raise RuntimeRefusal('draft_not_open')
        with Database(self.settings.database) as db:
            RunStore(db).add_draft_message(draft, 'owner', {'message': content}, state='thinking', error=None)
        self._submit_background(self._draft_turn, draft['draft_id'])

    def _open_revision(self, store, repo, draft):
        """The branch a revision will join: while its pull request is open, the newest work is there."""
        revises = draft['options'].get('revises_task_id')
        base = store.branch(revises) if revises else None
        if not base:
            return None
        try:
            pull = self._repos.find_pull_request(repo['repository'], base['branch'])
        except RepoError:
            return None
        return base['branch'] if pull and pull.state == 'OPEN' else None

    def _draft_turn(self, draft_id):
        with Database(self.settings.database) as db:
            store = RunStore(db)
            draft = store.draft(draft_id)
            project_id = draft['project_id']
            try:
                project, repo = ProjectRegistry(db).get(project_id), store.repo(project_id)
                rounds = sum(1 for m in draft['messages'] if m['role'] == 'lead')
                context = dict(project=dict(name=project.name, description=project.description),
                               conversation=draft['messages'], task_kind='revision' if draft['options'].get('revises_task_id')
                               else 'new', current_brief=draft['brief'], rounds_left=max(MAX_DRAFT_ROUNDS - rounds, 0))
                if repo:
                    context |= dict(project_spec=(store.spec(project_id) or {}).get('content', ''),
                                    repository=read_latest(self._repos, project.root_path, repo['default_branch'],
                                                           self._open_revision(store, repo, draft)))
                reply = ask_lead(self.author('lead'), DRAFT_PROMPT, context)
                if reply.ready and not reply.spec.strip():
                    raise RepoError('lead_returned_empty_brief')
            except (ModelError, RepoError) as exc:
                store.update_draft(draft_id, state='failed', error=str(exc.code))
                return
            except Exception as exc:  # noqa: BLE001 - a background job has no caller: record it instead of losing it
                store.update_draft(draft_id, state='failed', error=type(exc).__name__)
                return
            brief = redact(reply.spec)[:BRIEF_LIMIT] if reply.ready else draft['brief']
            title = ' '.join(reply.title.split())[:120] or brief.splitlines()[0].lstrip('# ')[:120] if reply.ready else ''
            store.add_draft_message(draft, 'lead', reply.payload(), state='ready' if reply.ready else 'need_answers',
                                    brief=brief, title=title, error=None)

    def post_task_message(self, task_id, content):
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            task, row = engine.get(task_id), store.latest(task_id)
            if (not row or row['state'] not in ('waiting', 'interrupted') or row['wait_reason'] not in ANSWERABLE
                    or task.status != 'waiting'):
                raise RuntimeRefusal('clarification_not_pending')
            if self._closing or not self._pool:
                raise RuntimeRefusal('runtime_unavailable', 503)
            if not self._busy.acquire(blocking=False):
                raise RuntimeRefusal('worker_busy', 409)
            try:
                with db.transaction():
                    store.add_message(task.project_id, task_id, 'owner', {'message': content})
                    store.update(row['run_id'], state='scheduled', phase='queued')
                # An approved design stays approved: only an earlier stop goes through the design again.
                mode = 'code' if store.design_approved(row['run_id']) else 'start'
                self._pool.submit(self._work, row['run_id'], mode)
            except BaseException:
                self._busy.release()
                raise
            return dict(task_id=task_id, run_id=row['run_id'], state='scheduled')

    def decide_design(self, task_id, principal, decision, reason):
        """The operator's gate between design and coding. Approving resumes the same run."""
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            with db.transaction():
                task, row = engine.get(task_id), store.latest(task_id)
                if not principal.allows_project(task.project_id):
                    raise RuntimeRefusal('forbidden', 403)
                if (not row or row['state'] != 'waiting' or row['wait_reason'] != 'design_approval'
                        or task.status != 'waiting'):
                    raise RuntimeRefusal('design_not_pending')
                if decision == 'reject':
                    store.record_design_decision(row['run_id'], decision, reason, principal.identity)
                    engine.cancel(task_id, reason='Design rejected by authorized approver.')
                    store.update(row['run_id'], state='cancelled', phase='cancelled')
                    return dict(task_id=task_id, run_id=row['run_id'], decision=decision)
            if self._closing or not self._pool:
                raise RuntimeRefusal('runtime_unavailable', 503)
            if not self._busy.acquire(blocking=False):
                raise RuntimeRefusal('worker_busy', 409)
            try:
                with db.transaction():
                    store.record_design_decision(row['run_id'], decision, reason, principal.identity)
                    store.update(row['run_id'], state='scheduled', phase='queued')
                self._pool.submit(self._work, row['run_id'], 'code')
            except BaseException:
                self._busy.release()
                raise
            return dict(task_id=task_id, run_id=row['run_id'], decision=decision)

    def cancel(self, task_id):
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            with db.transaction():
                task = engine.get(task_id)
                if task.status != 'cancelled':
                    task = engine.cancel(task_id, reason='Explicit operator cancellation.')
                row = store.latest(task_id)
                if row and row['state'] in ('scheduled', 'running', 'waiting'):
                    store.update(row['run_id'], state='cancelled', phase='cancelled')
                return task

    def retry(self, task_id):
        with Database(self.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            with db.transaction():
                task, row = engine.get(task_id), store.latest(task_id)
                if task.status == 'waiting' and row and row['wait_reason'] in (
                        'worker_interrupted', 'quality_gate', 'input_required'):
                    engine.fail(task_id, TaskFailure('EXPLICIT_RETRY', 'Operator requested another proposal attempt.',
                                                    retryable=True))
                    store.update(row['run_id'], state='failed', phase='failed')
                return engine.retry(task_id)
