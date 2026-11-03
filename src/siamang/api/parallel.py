"""Several Programmers on one approved task, each on its own copy of the repository.

A copy holds no ``.git``, so a package can reach neither Git nor another package. Each runs the
normal Programmer, QA, and Reviewer loop against its copy and may only change the paths it owns.
The patches then merge into one, which the same QA and Reviewer check as a whole. That merged
result is the ordinary evidence the approval already seals, so the gate itself is unchanged.
"""
from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.documents.packages import FOUNDATION, WorkPackage, owned
from siamang.ids import new_id
from siamang.jsondata import thaw
from siamang.loop import LoopRequest, LoopService
from siamang.loop.policy import LoopPolicy
from siamang.programmer import Programmer
from siamang.programmer.change import BeforeState, Operation, ProposedChange, change_id
from siamang.programmer.errors import ChangeRejectedError
from siamang.programmer.patch import MAX_CHANGES_LIMIT, MAX_TOTAL_LIMIT, Patch
from siamang.programmer.policy import ProgrammingPolicy
from siamang.programmer.result import ProgrammingResult, ProgrammingStatus
from siamang.projects import ProjectRegistry
from siamang.qa import QAService
from siamang.qa.policy import QAPolicy
from siamang.repos.workspace import source_files, write_changes
from siamang.reviewer import ReviewerService
from siamang.reviewer.policy import ReviewerPolicy
from siamang.storage import Database
from siamang.tasks import TaskEngine

from .codec import decode, encode
from .store import RunStore

PACKAGE_POLICY = ProgrammingPolicy(model_timeout_secs=600, max_context_file_size=64 * 1024, max_context_files=12,
                                   max_changes=30)
QA_POLICY = QAPolicy(max_changes=MAX_CHANGES_LIMIT, max_total_bytes=MAX_TOTAL_LIMIT)
# A new application is expected to add many files and dependencies. These stay visible in the report as
# notes, and hold nothing back; anything about whether the code is right still stops the work.
REVIEW_POLICY = ReviewerPolicy(
    max_changes=MAX_CHANGES_LIMIT, max_total_bytes=MAX_TOTAL_LIMIT, broad_change_count=MAX_CHANGES_LIMIT,
    max_directories=MAX_CHANGES_LIMIT, max_new_files=MAX_CHANGES_LIMIT, max_file_lines=1_500,
    advisory_codes=frozenset({'scope.broad_change', 'scope.directory_spread', 'maintainability.many_new_files',
                              'risk.dependency_change', 'risk.configuration_change', 'risk.public_interface',
                              'consistency.unfamiliar_file_type', 'consistency.naming_style'}))
FOUNDATION_REVIEW = replace(REVIEW_POLICY, advisory_codes=REVIEW_POLICY.advisory_codes | {'feasibility.tests_absent'})  # a skeleton
# Applications side by side legitimately repeat files such as .gitignore.
INTEGRATION_REVIEW = replace(FOUNDATION_REVIEW, advisory_codes=FOUNDATION_REVIEW.advisory_codes | {'maintainability.duplicate_content'})
ONE_ROUND = LoopPolicy(max_iterations=1, max_programmer_calls=1, max_qa_calls=1, max_reviewer_calls=1)
FOCUS_LIMIT = 20
INPUT_DESIGN_BYTES = 3_000
SPEC_BYTES = 1_200


def failure_reason(error):
    """A subscription limit says when work can go on; every other failure is named by its code."""
    error = error or {}
    return 'rate_limited' if 'rate' in str(error.get('cause', '')).lower() else str(error.get('code', 'unknown'))


class PackageStopped(Exception):
    """The task was cancelled or the runtime is closing while packages ran."""


def rooted(cls, root):
    """The same service, reading one copy of the repository instead of the managed clone."""
    class Rooted(cls):
        def _active_project(self, project_id):
            return replace(super()._active_project(project_id), root_path=str(root))
    return Rooted


def merge_patches(task_id, project_id, patches, summary) -> Patch:
    """Later packages built on earlier ones: each path keeps its first before-state and its last content."""
    first, last = {}, {}
    for patch in patches:
        for change in patch.changes:
            first.setdefault(change.path, change)
            last[change.path] = change
    changes = []
    for path, start in first.items():
        end = last[path]
        created = start.before is BeforeState.ABSENT
        if end.operation is Operation.DELETE and created:
            continue  # added and removed again: the repository never sees it
        operation = Operation.DELETE if end.operation is Operation.DELETE else Operation.CREATE if created else Operation.MODIFY
        changes.append(ProposedChange(change_id(len(changes) + 1), path, operation, end.step_id, end.reason,
                                      after_content=end.after_content, before=start.before,
                                      before_hash=start.before_hash, evidence=end.evidence))
    return Patch(new_id('patch'), task_id, project_id, summary[:2_000], tuple(changes))


def commit_groups(rows, patch) -> list[tuple[str, tuple[str, ...]]]:
    """Which package each merged path came from, in order: one commit each. The content stays in the sealed patch."""
    remaining, groups = {c.path for c in patch.changes}, []
    for row in reversed(rows):
        if not row['patch']:
            continue
        paths = tuple(c.path for c in decode(json.loads(row['patch'])).changes if c.path in remaining)
        remaining -= set(paths)
        if paths:
            groups.insert(0, (row['title'], paths))
    if remaining:
        groups.append(('Perubahan lain', tuple(sorted(remaining))))
    return groups


class Merged(Programmer):
    """The Programmer's seat in the integration round: its work is the packages' merged patch, no model call."""

    def __init__(self, tasks, patch, summary):
        super().__init__(tasks)
        self._patch, self._summary = patch, summary
        self.last = None

    def program(self, request):
        self.last = ProgrammingResult(programming_id=new_id('programming'), task_id=request.task_id,
                                      project_id=request.project_id, status=ProgrammingStatus.COMPLETED,
                                      summary=self._summary, patch=self._patch, metadata={})
        return self.last


class PackageRun:
    def __init__(self, runtime, run_id, ids, plan, base_input, names, limit):
        self.runtime, self.run_id, self.ids, self.plan = runtime, run_id, ids, plan
        self.base_input, self.names, self.limit = base_input, names, limit
        self.task_id, self.project_id = ids['task_id'], ids['project_id']
        self.workdir = runtime.settings.database.parent / 'packages' / run_id
        self.source = None
        self.stopped = threading.Event()

    def _copy(self, row, applied) -> Path:
        dest = self.workdir / row['key']
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.source, dest, symlinks=True, ignore=shutil.ignore_patterns('.git'))
        for patch in applied:
            write_changes(dest, patch)
        return dest

    def _focus(self, row, owns, applied):
        added = [c.path for p in applied for c in p.changes]
        names = source_files(dict.fromkeys([*self.names, *added]))
        return tuple(([n for n in names if owned(n, owns)] + [n for n in names if not owned(n, owns)])[:FOCUS_LIMIT])

    def run_one(self, row, applied):
        """One package from start to verdict. Its own thread, its own connection; always records an outcome."""
        with Database(self.runtime.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            key, owns = row['key'], tuple(json.loads(row['owns']))
            try:
                self._run_one(db, store, engine, row, key, owns, applied)
            except PackageStopped:
                store.update_package(self.run_id, key, state='pending', phase=None)
                self.stopped.set()
            except Exception as exc:  # noqa: BLE001 - one package must not lose the others' work
                if self.runtime._closing or engine.get(self.task_id).status == 'cancelled':  # the Loop wraps what it calls
                    store.update_package(self.run_id, key, state='pending', phase=None)
                    self.stopped.set()
                else:
                    cause = str(getattr(exc, 'code', type(exc).__name__))
                    store.update_package(self.run_id, key, state='failed', phase=None, error=cause)
            finally:
                shutil.rmtree(self.workdir / key, ignore_errors=True)

    def _run_one(self, db, store, engine, row, key, owns, applied):
        run, task_id = self, self.task_id

        def step(phase, iteration=None):
            if run.runtime._closing or engine.get(task_id).status == 'cancelled':
                raise PackageStopped()
            store.update_package(run.run_id, key, state='running', phase=phase, iteration=iteration, error=None)

        step('analysis')
        root = self._copy(row, applied)
        analysis = rooted(SystemAnalyst, root)(engine).analyze(AnalysisRequest(
            **self.ids, plan=self.plan, focus=self._focus(row, owns, applied), scope=owns))

        class Coding(rooted(Programmer, root)):
            iteration = 0
            last = None

            def program(self, request):
                self.iteration += 1
                step('programming', self.iteration)
                result = super().program(request)
                outside = [c.path for c in result.changes if not owned(c.path, owns)]
                if outside:  # told once what it may touch; a second breach stops the package
                    note = dict(thaw(request.input), outside_owned_paths=outside[:10], allowed_paths=list(owns))
                    result = super().program(replace(request, input=note))
                    if any(not owned(c.path, owns) for c in result.changes):
                        result = self._failed(request, ChangeRejectedError('a change is outside the owned paths'),
                                              {'warnings': [], 'unknowns': []}, {})
                self.last = result
                return result

        class Checking(rooted(QAService, root)):
            def verify(self, request):
                step('qa', programmer.iteration)
                return super().verify(request)

        class Reviewing(ReviewerService):
            def review(self, request):
                step('reviewing', programmer.iteration)
                return super().review(request)

        programmer = Coding(engine, models=self.runtime.models('programmer'), policy=PACKAGE_POLICY)
        work = dict(title=row['title'], specialty=row['specialty'], instructions=row['instructions'], owned_paths=list(owns))
        loop = LoopService(engine, programmer, Checking(engine, policy=QA_POLICY),
                           Reviewing(engine, policy=FOUNDATION_REVIEW if row['kind'] == FOUNDATION else REVIEW_POLICY)).run(
            LoopRequest(**self.ids, plan=self.plan, analysis=analysis, input=dict(self.base_input, work_package=work)))
        patch = loop.final_patch
        if loop.passed:
            store.update_package(self.run_id, key, state='passed', phase=None, summary=patch.summary[:600],
                                 patch=json.dumps(encode(patch), sort_keys=True), error=None)
        elif str(loop.stop_reason) == 'programmer_waiting':
            asked = [q for q in programmer.last.unknowns if not q.startswith(self.runtime.system_notes)]
            store.update_package(self.run_id, key, state='waiting', phase=None, summary=programmer.last.summary[:600],
                                 questions=json.dumps(asked[:6], ensure_ascii=False))
        elif str(loop.stop_reason) == 'programmer_failed':
            store.update_package(self.run_id, key, state='failed', phase=None,
                                 error=failure_reason(programmer.last.error))
        else:
            store.update_package(self.run_id, key, state='blocked', phase=None,
                                 questions=json.dumps(self.runtime.blockers(loop), ensure_ascii=False))

    def add_fix(self, store, blockers, owns):
        position = len(store.work_packages(self.run_id))
        fix = WorkPackage(f'fix-{position}', 'fix', 'Perbaikan integrasi', 'integrasi',
                          ('Paket sudah lulus sendiri-sendiri, tetapi QA dan Reviewer menahan gabungannya. Perbaiki temuan ini saja:\n'
                           + '\n'.join(f'- {b}' for b in blockers))[:1_500], tuple(owns))
        store.add_package(self.run_id, fix, position)

    def execute(self, store, source):
        """Foundation first and alone, then the rest together. Returns the rows, or stops for the owner."""
        self.source = source
        try:
            rows = store.work_packages(self.run_id)
            done = [decode(json.loads(r['patch'])) for r in rows if r['state'] == 'passed']
            foundation = [r for r in rows if r['kind'] == FOUNDATION and r['state'] != 'passed']
            if foundation:
                self.run_one(foundation[0], [])
                rows = store.work_packages(self.run_id)
                if next(r for r in rows if r['kind'] == FOUNDATION)['state'] != 'passed':
                    return rows
                done = [decode(json.loads(r['patch'])) for r in rows if r['state'] == 'passed']
            pending = [r for r in rows if r['state'] != 'passed']
            with ThreadPoolExecutor(max_workers=max(1, min(self.limit, len(pending) or 1)),
                                    thread_name_prefix='siamang-package') as pool:
                for row in pending:
                    pool.submit(self.run_one, row, done)
            if self.stopped.is_set():
                raise PackageStopped()
            return store.work_packages(self.run_id)
        finally:
            shutil.rmtree(self.workdir, ignore_errors=True)
