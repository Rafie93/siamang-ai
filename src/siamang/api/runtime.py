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
from siamang.ids import new_id
from siamang.documents.author import Document, choose_files, clip, clip_bytes, design_input, write_documents
from siamang.documents.catalog import SPECS, DocumentStatus
from siamang.documents.derive import programmer_documents, qa_documents, review_documents
from siamang.documents.packages import plan_work, render
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
from siamang.log import redact
from siamang.models.errors import ModelError
from siamang.programmer.policy import ProgrammingPolicy
from siamang.repos.client import RepoClient, RepoError
from siamang.repos.workspace import branch_name, named_in, read_context, source_files, write_changes
from siamang.loop import LoopRequest, LoopService
from siamang.planner import Planner, PlanningRequest
from siamang.programmer import Programmer
from siamang.projects import ProjectRegistry
from siamang.qa import QAService
from siamang.reviewer import ReviewerService
from siamang.storage import Database
from siamang.tasks import TaskEngine, TaskFailure, TaskResult

from .codec import decode, encode
from .parallel import ONE_ROUND, QA_POLICY, INTEGRATION_REVIEW, INPUT_DESIGN_BYTES, SPEC_BYTES, Merged, PackageRun, PackageStopped, commit_groups, merge_patches
from .store import RunStore, now


class RuntimeRefusal(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class Stopped(Exception):
    pass


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


class DesignFailed(Exception):
    """A model failed while writing the design: there is nothing for the operator to approve."""


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


def _pull_request_body(task, patch, loop):
    qa, review = loop.final_qa_result, loop.final_review_result
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
- Siamang sendiri tidak menjalankan kode. Hasil build dan test nyata ada di tab Checks kalau repo punya workflow.

Dibuat oleh Siamang setelah disetujui operator. Merge tetap keputusan pemilik repository.
''')


def _stored(row):
    return Document(SPECS[row['kind']], DocumentStatus(row['status']), row['content'], row['reason'], row['model_id'])


class Runtime:
    def __init__(self, settings, models=None, authors=None, repos=None):
        self.settings = settings
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

    def _heartbeat(self, run_id, done):
        while not done.wait(3):
            with Database(self.settings.database) as db:
                db.conn.execute("UPDATE api_runs SET heartbeat_at=? WHERE run_id=? AND state='running'",
                                (now(), run_id))

    def _work(self, run_id, mode='start'):
        done = threading.Event()
        pulse = threading.Thread(target=self._heartbeat, args=(run_id, done), daemon=True)
        try:
            with Database(self.settings.database) as db:
                engine, store = TaskEngine(db, ProjectRegistry(db)), RunStore(db)
                row = store.get(run_id)
                task_id = row['task_id']

                def phase(name, iteration=None):
                    with db.transaction():
                        if self._closing or engine.get(task_id).status == 'cancelled':
                            raise Stopped()
                        store.update(run_id, state='running', phase=name, iteration=iteration)

                def documents(stage, context, *, required=False):
                    agent = next(s.agent for s in SPECS.values() if s.stage == stage)
                    router = self.author(agent)
                    if router is not None:
                        phase(stage, iteration=None)
                    written = write_documents(router, stage, context)
                    store.save_documents(run_id, task_id, written)
                    if required and any(d.status is DocumentStatus.FAILED for d in written):
                        raise DesignFailed()
                    return written

                with db.transaction():
                    if engine.get(task_id).status == 'cancelled':
                        raise Stopped()
                    task = engine.start(task_id)
                    # With a design gate the first visible work is the Lead's brief; the template plan is only input.
                    gated = mode == 'start' and bool(self.author('planner') or self.author('analyst'))
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
                focus = self._focus(ProjectRegistry(db).get(task.project_id), stored, task) if repo and not gated else ()
                analysis = SystemAnalyst(engine).analyze(AnalysisRequest(**ids, plan=plan, focus=focus))
                project = ProjectRegistry(db).get(task.project_id)
                brief = dict(project=dict(name=project.name, description=project.description),
                             instruction=task.description, plan=plan.to_dict(), observed_project=analysis.to_dict())
                spec = (store.spec(task.project_id) or {}).get('content', '') if repo else ''
                conversation = store.messages(task.project_id, task_id) if repo else []
                if repo:
                    brief |= dict(project_spec=spec, task_conversation=conversation,
                                  repository=read_context(self._repos, project.root_path))
                if gated and repo and self.author('lead') and not store.draft_of_task(task_id):  # a draft already asked
                    rounds = sum(1 for m in store.messages(task.project_id, task_id) if m['role'] == 'lead')
                    reply = ask_lead(self.author('lead'), TASK_PROMPT,
                                     brief | dict(rounds_left=max(MAX_TASK_ROUNDS - rounds, 0)))
                    if not reply.ready and rounds < MAX_TASK_ROUNDS:
                        store.add_message(task.project_id, task_id, 'lead', reply.payload())
                        with db.transaction():
                            if self._closing or engine.get(task_id).status == 'cancelled':
                                raise Stopped()
                            engine.wait(task_id, reason='clarification_required')
                            store.update(run_id, state='waiting', phase='clarification',
                                         wait_reason='clarification_required')
                        return
                if gated:
                    design = []
                    for stage in DESIGN_STAGES:
                        prior = {d.spec.kind: clip(d.content) for d in design if d.status is DocumentStatus.GENERATED}
                        design += documents(stage, brief | dict(prior_documents=prior), required=True)
                    if repo:
                        work = plan_work(self.author('analyst'), dict(
                            instruction=task.description, project_spec=spec, task_conversation=conversation,
                            repository=brief['repository'],
                            design=[dict(document=d.spec.kind, text=clip(d.content, 3_000)) for d in design
                                    if d.status is DocumentStatus.GENERATED]))
                        if work and work.undecided:
                            store.add_message(task.project_id, task_id, 'lead', dict(
                                message='Sebelum tim dibagi, ada keputusan teknis yang belum disepakati.',
                                questions=[dict(question=q, options=[]) for q in work.undecided]))
                            with db.transaction():
                                if self._closing or engine.get(task_id).status == 'cancelled':
                                    raise Stopped()
                                engine.wait(task_id, reason='clarification_required')
                                store.update(run_id, state='waiting', phase='clarification',
                                             wait_reason='clarification_required')
                            return
                        store.set_work_packages(run_id, work.packages if work else ())
                    else:
                        work = None
                    spec_row = SPECS['work_packages']
                    store.save_documents(run_id, task_id, (
                        Document(spec_row, DocumentStatus.GENERATED, render(work)) if work and work.packages else
                        Document(spec_row, DocumentStatus.NOT_APPLICABLE, reason='Cukup satu Programmer untuk pekerjaan ini.'),))
                    with db.transaction():
                        if self._closing or engine.get(task_id).status == 'cancelled':
                            raise Stopped()
                        engine.wait(task_id, reason='design_approval')
                        store.update(run_id, state='waiting', phase='design_approval', wait_reason='design_approval')
                    return
                design = stored

                class TrackedProgrammer(Programmer):
                    iteration = 0
                    last = None

                    def program(self, request):
                        self.iteration += 1
                        phase('programming', self.iteration)
                        self.last = super().program(request)
                        return self.last

                programmer = TrackedProgrammer(engine, models=self.models('programmer'), policy=MODEL_POLICY)

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
                        return
                    loop, analysis, programmer = delivered
                else:
                    loop = LoopService(engine, programmer, TrackedQA(engine), TrackedReviewer(engine)).run(
                        LoopRequest(**ids, plan=plan, analysis=analysis,
                                    input=design_input(design) | ({'project_spec': clip_bytes(spec)} if spec else {})
                                    | ({'owner_conversation': conversation_tail(conversation)} if conversation else {})))
                if str(loop.stop_reason) == 'programmer_waiting':
                    # The Programmer needs a decision: it reaches the owner, and the answer returns to it.
                    asked = programmer.last
                    store.add_message(task.project_id, task_id, 'lead', dict(
                        message=asked.summary, questions=[dict(question=q, options=[]) for q in asked.unknowns
                                                          if not q.startswith(SYSTEM_NOTES)][:6]))
                    with db.transaction():
                        if self._closing or engine.get(task_id).status == 'cancelled':
                            raise Stopped()
                        engine.wait(task_id, reason='clarification_required')
                        store.update(run_id, state='waiting', phase='clarification', wait_reason='clarification_required')
                    return
                patch, qa, review = loop.final_patch, loop.final_qa_result, loop.final_review_result
                store.save_documents(run_id, task_id, programmer_documents(patch) + qa_documents(qa) + review_documents(review))
                changes = [dict(path=c.path, operation=str(c.operation), after_content=clip(c.after_content or ''))
                           for c in (patch.changes if patch else ())]
                work = dict(brief, patch=dict(summary=patch.summary, changes=changes) if patch else None,
                            qa=qa.to_dict() if qa else None, review=review.to_dict() if review else None,
                            loop=dict(iterations=len(loop.iterations), stop_reason=str(loop.stop_reason)),
                            design=[dict(document=d.spec.kind, text=clip(d.content)) for d in design
                                    if d.status is DocumentStatus.GENERATED])
                if patch:  # no code means nothing for these agents to write about
                    for stage in ('programming', 'qa', 'reviewing', 'reporting'):
                        documents(stage, work)
                bundle = json.dumps({'version': 1, 'plan': encode(plan), 'analysis': encode(analysis),
                                     'loop': encode(loop)}, sort_keys=True, separators=(',', ':'))
                seal = self._sign(run_id, task_id, bundle)
                report = json.dumps(dict(plan=plan.to_dict(), analysis=analysis.to_dict(), loop=loop.to_dict()))
                with db.transaction():
                    if self._closing or engine.get(task_id).status == 'cancelled':
                        raise Stopped()
                    db.conn.execute('UPDATE api_runs SET bundle=?,bundle_seal=?,report=? WHERE run_id=?',
                                    (bundle, seal, report, run_id))
                    if str(loop.stop_reason) == 'programmer_failed':
                        cause = str((programmer.last.error or {}).get('code', 'unknown'))
                        engine.fail(task_id, TaskFailure('PROGRAMMER_FAILED', 'Programmer could not produce a proposal.',
                                                         retryable=True, details={'cause': cause}))
                        store.update(run_id, state='failed', phase='failed')
                        return
                    reason = 'approval_required' if loop.passed else 'quality_gate'
                    if not loop.passed:
                        store.add_message(task.project_id, task_id, 'lead', dict(
                            message=f'QA dan Reviewer masih menahan hasil ini setelah {len(loop.iterations)} putaran. '
                                    'Beri arahan dan Programmer mengerjakannya lagi, atau batalkan task ini.',
                            questions=[dict(question=b, options=[]) for b in _blockers(loop)]))
                    engine.wait(task_id, reason=reason)
                    store.update(run_id, state='waiting', phase='awaiting_approval' if loop.passed else 'blocked',
                                 wait_reason=reason)
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
                                                             retryable=True, details={'cause': str(getattr(exc, 'code', type(exc).__name__))}))
                        store.update(run_id, state='failed', phase='failed')
        finally:
            done.set()
            if pulse.is_alive():
                pulse.join()
            self._busy.release()

    def _stop_for_owner(self, db, store, engine, run_id, task, reason, message, questions):
        store.add_message(task.project_id, task.task_id, 'lead', dict(
            message=message, questions=[dict(question=q, options=[]) for q in questions][:12]))
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
                                     'beri arahan dan hanya paket ini yang dikerjakan lagi.', held)
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
                        return
                    store.set_ci(task_id, sha, 'watching', runs)
                elif runs == [] and waited > CI_NO_WORKFLOW_SECS:
                    store.set_ci(task_id, sha, 'none')
                    return
                if waited > CI_TIMEOUT_SECS:
                    store.set_ci(task_id, sha, 'timeout', runs or ())
                    return
                self._ci_wake.wait(self.ci_poll_secs)

    ci_poll_secs = CI_POLL_SECS

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

    def _focus(self, project, documents, task):
        """Which files the Programmer reads. Chosen here from the tracked list, never by an API caller."""
        limit = MODEL_POLICY.max_context_files
        names = source_files(self._repos.files(project.root_path))
        if len(names) <= limit:
            return tuple(names)
        texts = [d.content for d in documents if d.status is DocumentStatus.GENERATED]
        chosen = choose_files(self.author('analyst'), dict(
            instruction=task.description, design=[clip(t, 3_000) for t in texts]), names, limit)
        return tuple((chosen or named_in(names, texts))[:limit])

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
                service = ApprovalService(engine, policy=policy, seal_key=self.settings.seal_key)
                result = service.decide(request, ApprovalDecision(decision, principal.identity, reason))
                service.verify(result, request)
                stored = json.dumps(result.to_dict())
                signed = json.dumps([stored, decision_input, principal.identity], separators=(',', ':'))
                db.conn.execute('UPDATE api_runs SET decision=?,decision_input=?,decision_actor=?,decision_seal=? WHERE run_id=?',
                                (stored, decision_input, principal.identity, self._sign(run_id, row['task_id'], signed), run_id))
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
                decided = result.to_dict()
            if publish:
                self._submit_publish(run_id)
            return decided

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
                repository=read_context(self._repos, project.root_path), conversation=history,
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
                                                         title=title, body=_pull_request_body(task, patch, loop))
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
                                    repository=read_context(self._repos, project.root_path))
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
