"""Task Engine: task state, persistence, and lifecycle. Never execution.

::

    TaskRequest ──► TaskEngine ──┬──► ProjectRegistry   (project exists and is active)
                                 ├──► TaskRepository     (storage/tasks.py, SQL only)
                                 ├──► EventStore         (same transaction as the state change)
                                 └──► EventBus           (notified after commit)

Every mutation is **one** SQLite transaction: read the row, check the
transition (:mod:`siamang.tasks.lifecycle`), compare-and-set update, append
the event with the next ``seq``, commit. Any failure rolls back both, so a
state change never exists without its event or the other way round.
``BEGIN IMMEDIATE`` serializes concurrent writers; the loser re-reads the new
state and gets :class:`InvalidTransitionError`.

The bus is notified only **after** commit, so subscribers never see an event
that was rolled back. Persistence does not go through the bus (that would
put subscribers inside the transaction). Do not also attach an
``EventStore`` to the same bus, or events would be stored twice. A
subscriber error is logged, not raised: the committed state is the truth.

The engine never calls a model, tool, agent, Git, a subprocess, or the
network. ``start``/``complete``/``fail`` record what *someone else* did;
``retry`` only re-queues. Phase 11+ consumes this state.
"""

from __future__ import annotations

from siamang.jsondata import thaw

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from siamang.bus import EventBus
from siamang.contracts import Envelope, EventType
from siamang.contracts.envelope import new_event, utc_now
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.projects import Project, ProjectNotFoundError, ProjectRegistry
from siamang.storage.database import Database, DuplicateError
from siamang.storage.events import EventStore
from siamang.storage.tasks import LIST_LIMIT_MAX, TaskRepository, TaskRow
from siamang.tasks.definition import (
    SUMMARY_MAX,
    Task,
    TaskFailure,
    TaskPriority,
    TaskRequest,
    TaskResult,
    TaskSource,
    canonical_json_object,
    format_ts,
    freeze_metadata,
    normalize_title,
    parse_priority,
    parse_source,
    parse_status,
    parse_ts,
    validate_description,
    validate_max_retries,
    validate_project_id,
    validate_request_id,
    validate_task_id,
)
from siamang.tasks.errors import (
    DuplicateTaskError,
    InvalidTaskError,
    InvalidTransitionError,
    NotRetryableError,
    RetryLimitReachedError,
    TaskError,
    TaskNotFoundError,
    TaskProjectArchivedError,
    TaskProjectNotFoundError,
)
from siamang.tasks.lifecycle import EVENTS, TaskAction, TaskStatus, plan

log = get_logger("tasks")


def _to_task(row: TaskRow) -> Task:
    return Task(
        task_id=row.task_id,
        request_id=row.request_id,
        project_id=row.project_id,
        parent_task_id=row.parent_task_id,
        title=row.title,
        description=row.description,
        status=TaskStatus(row.status),
        priority=TaskPriority(row.priority),
        source=TaskSource(row.source),
        retry_count=row.retry_count,
        max_retries=row.max_retries,
        metadata=freeze_metadata(row.metadata),
        last_error=TaskFailure.from_json(row.last_error),
        result=TaskResult.from_json(row.result),
        created_at=parse_ts(row.created_at),
        updated_at=parse_ts(row.updated_at),
        started_at=parse_ts(row.started_at),
        completed_at=parse_ts(row.completed_at),
        failed_at=parse_ts(row.failed_at),
        cancelled_at=parse_ts(row.cancelled_at),
    )


def _summary(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 3] + "..."


@dataclass(frozen=True)
class TaskSubmission:
    task: Task
    duplicate: bool  # True: this request_id was already accepted; nothing new was written


@dataclass(frozen=True)
class RecoveryReport:
    """Active tasks found at startup. Read-only: nothing is changed or executed.

    ``running`` tasks may have been interrupted by a crash; nobody can know
    from state alone whether their work finished. An operator (or Phase 12)
    decides explicitly: ``fail`` (then optionally ``retry``) or ``cancel``.
    """

    running: tuple[Task, ...]
    waiting: tuple[Task, ...]
    queued: tuple[Task, ...]

    @property
    def interrupted(self) -> tuple[Task, ...]:
        return self.running

    def to_dict(self) -> dict[str, Any]:
        return {name: [t.to_dict() for t in getattr(self, name)] for name in ("running", "waiting", "queued")}


class TaskEngine:
    def __init__(
        self,
        db: Database,
        projects: ProjectRegistry,
        *,
        bus: EventBus | None = None,
        clock: Callable[[], datetime] = utc_now,
        producer: str = "siamang",
    ):
        if not isinstance(projects, ProjectRegistry):
            raise TypeError("projects must be a ProjectRegistry")
        if projects.db is not db:
            raise ValueError("TaskEngine and ProjectRegistry must share one Database (one transaction)")
        self.db = db
        self.projects = projects
        self.repo = TaskRepository(db)
        self.events = EventStore(db)
        self.bus = bus
        self._clock = clock
        self.producer = producer

    # -- creation ----------------------------------------------------------------------

    def create(self, request: TaskRequest) -> TaskSubmission:
        if not isinstance(request, TaskRequest):
            raise InvalidTaskError("request", "must be a TaskRequest")
        project_id = validate_project_id(request.project_id)
        description = validate_description(request.description)
        title = normalize_title(request.title, description)
        source = parse_source(request.source)
        priority = parse_priority(request.priority)
        max_retries = validate_max_retries(request.max_retries)
        metadata = canonical_json_object(request.metadata)
        parent_id = validate_task_id(request.parent_task_id, "parent_task_id") if request.parent_task_id else None
        task_id = validate_task_id(request.task_id) if request.task_id is not None else new_id("task")
        request_id = validate_request_id(request.request_id) if request.request_id is not None else new_id("request")
        ts = format_ts(self._clock())

        with self.db.transaction():
            existing = self.repo.get_by_request_id(request_id)
            if existing is not None:  # idempotent resubmission (contract §8)
                if existing.project_id == project_id and existing.description == description:
                    return TaskSubmission(_to_task(existing), duplicate=True)
                raise DuplicateTaskError("request_id", existing.task_id)
            project = self._active_project(project_id)
            if parent_id is not None:
                parent = self.repo.get(parent_id)
                if parent is None or parent.project_id != project_id:
                    raise InvalidTaskError("parent_task_id", "must be an existing task of the same project")
            if self.repo.exists(task_id):
                raise DuplicateTaskError("task_id", task_id)
            row = TaskRow(task_id, request_id, project_id, parent_id, title, description, str(TaskStatus.QUEUED),
                          str(priority), str(source), 0, max_retries, metadata, None, None, ts, ts,
                          None, None, None, None)
            try:
                self.repo.insert(row)
            except DuplicateError:
                raise DuplicateTaskError("task_id", task_id) from None
            event = self._append(row, EventType.TASK_ACCEPTED, {
                "status": str(TaskStatus.QUEUED),
                "summary": _summary(f"Queued: {title}"),
                "project": {"id": project.project_id, "name": project.name},
            }, ts)
        log.info("task %s created (project %s, source %s)", task_id, project_id, source)
        self._publish(event)
        return TaskSubmission(_to_task(row), duplicate=False)

    # -- lifecycle ---------------------------------------------------------------------

    def start(self, task_id: str, *, summary: str | None = None) -> Task:
        def change(row: TaskRow, ts: str) -> tuple[dict[str, Any], dict[str, Any]]:
            first = row.status == TaskStatus.QUEUED  # a resumed (waiting) task keeps its start time
            return ({"started_at": ts if first else row.started_at},
                    {"summary": _summary(summary or f"Started: {row.title}")})

        return self._transition(task_id, TaskAction.START, change)

    def wait(self, task_id: str, *, reason: str | None = None, summary: str | None = None) -> Task:
        def change(row: TaskRow, ts: str) -> tuple[dict[str, Any], dict[str, Any]]:
            payload: dict[str, Any] = {"summary": _summary(summary or f"Waiting: {row.title}")}
            if reason:
                payload["reason"] = _summary(reason)
            return {}, payload

        return self._transition(task_id, TaskAction.WAIT, change)

    def requeue(self, task_id: str, *, summary: str | None = None) -> Task:
        return self._transition(task_id, TaskAction.REQUEUE,
                                lambda row, ts: ({}, {"summary": _summary(summary or f"Requeued: {row.title}")}))

    def complete(self, task_id: str, result: TaskResult) -> Task:
        if not isinstance(result, TaskResult):
            raise InvalidTaskError("result", "must be a TaskResult")

        def change(row: TaskRow, ts: str) -> tuple[dict[str, Any], dict[str, Any]]:
            payload: dict[str, Any] = {"summary": result.summary, "outcome": result.outcome}
            if result.result:
                payload["result"] = thaw(result.result)
            if result.details:
                payload["details"] = thaw(result.details)
            return {"completed_at": ts, "result": result.to_json()}, payload

        return self._transition(task_id, TaskAction.COMPLETE, change)

    def fail(self, task_id: str, error: TaskFailure | BaseException, *, step: str | int | None = None,
             summary: str | None = None) -> Task:
        failure = error if isinstance(error, TaskFailure) else None
        if failure is None:
            if not isinstance(error, BaseException):
                raise InvalidTaskError("error", "must be a TaskFailure or an exception")
            failure = TaskFailure.from_exception(error, step=step)

        def change(row: TaskRow, ts: str) -> tuple[dict[str, Any], dict[str, Any]]:
            # contract: optional fields are omitted, never null
            error_payload = {k: v for k, v in failure.to_dict().items() if v is not None}
            return ({"failed_at": ts, "last_error": failure.to_json()},
                    {"summary": _summary(summary or f"Failed: {row.title}"), "error": error_payload})

        return self._transition(task_id, TaskAction.FAIL, change)

    def cancel(self, task_id: str, *, reason: str | None = None) -> Task:
        return self._transition(task_id, TaskAction.CANCEL,
                                lambda row, ts: ({"cancelled_at": ts},
                                                 {"summary": _summary(reason or f"Cancelled: {row.title}")}))

    def retry(self, task_id: str) -> Task:
        """Explicit, counted ``failed → queued``. Re-queues only; nothing is executed."""

        def change(row: TaskRow, ts: str) -> tuple[dict[str, Any], dict[str, Any]]:
            if row.retry_count >= row.max_retries:
                raise RetryLimitReachedError(f"retry limit reached ({row.retry_count}/{row.max_retries})",
                                             task_id=row.task_id)
            last = TaskFailure.from_json(row.last_error)
            if last is not None and not last.retryable:
                raise NotRetryableError(f"last failure ({last.code}) is not retryable", task_id=row.task_id)
            count = row.retry_count + 1
            return ({"retry_count": count},
                    {"summary": _summary(f"Retry {count}/{row.max_retries}: {row.title}"),
                     "retry_count": count, "max_retries": row.max_retries})

        return self._transition(task_id, TaskAction.RETRY, change)

    # -- queries -----------------------------------------------------------------------

    def get(self, task_id: str) -> Task:
        return _to_task(self._row(task_id))

    def exists(self, task_id: str) -> bool:
        return isinstance(task_id, str) and self.repo.exists(task_id)

    def list(
        self,
        *,
        project_id: str | None = None,
        status: TaskStatus | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Task]:
        """Ordered by creation time, then task_id. One query (no per-task lookups)."""
        if project_id is not None:
            validate_project_id(project_id)
        status = str(parse_status(status)) if status is not None else None
        try:
            rows = self.repo.list(project_id=project_id, status=status, limit=limit, offset=offset)
        except ValueError as exc:
            raise InvalidTaskError("pagination", str(exc)) from None
        return [_to_task(r) for r in rows]

    def history(self, task_id: str) -> list[Envelope]:
        """The task's event stream, ordered by ``seq``."""
        self._row(task_id)
        return list(self.events.replay(task_id))

    def recovery(self) -> RecoveryReport:
        """Startup inspection: report active tasks. Changes nothing, executes nothing."""
        groups: dict[str, list[Task]] = {}
        for status in (TaskStatus.RUNNING, TaskStatus.WAITING, TaskStatus.QUEUED):
            found, offset = [], 0
            while True:
                rows = self.repo.list(status=str(status), limit=LIST_LIMIT_MAX, offset=offset)
                found += [_to_task(r) for r in rows]
                if len(rows) < LIST_LIMIT_MAX:
                    break
                offset += LIST_LIMIT_MAX
            groups[str(status)] = found
        report = RecoveryReport(tuple(groups["running"]), tuple(groups["waiting"]), tuple(groups["queued"]))
        if report.running:
            log.warning("recovery: %d running task(s) may have been interrupted; none were changed",
                        len(report.running))
        return report

    # -- internals ---------------------------------------------------------------------

    def _row(self, task_id: str) -> TaskRow:
        validate_task_id(task_id)
        row = self.repo.get(task_id)
        if row is None:
            raise TaskNotFoundError("unknown task", task_id=task_id)
        return row

    def _active_project(self, project_id: str) -> Project:
        failure: TaskError | None = None
        try:
            project = self.projects.get(project_id)
        except ProjectNotFoundError:
            failure = TaskProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise TaskProjectArchivedError("project is archived; restore it first")
        return project

    def _now(self, row: TaskRow) -> str:
        # never earlier than the previous update (clock-skew safe)
        return max(format_ts(self._clock()), row.updated_at)

    def _transition(
        self,
        task_id: str,
        action: TaskAction,
        change: Callable[[TaskRow, str], tuple[dict[str, Any], dict[str, Any]]],
    ) -> Task:
        with self.db.transaction():
            row = self._row(task_id)
            target = plan(action, row.status, task_id=task_id)
            ts = self._now(row)
            fields, payload = change(row, ts)
            new = replace(row, status=str(target), updated_at=ts, **fields)
            if not self.repo.update(new, expected=row):  # compare-and-set (belt and braces)
                raise InvalidTransitionError(str(action), "changed concurrently", task_id=task_id)
            event = self._append(new, EVENTS[action], {"status": str(target), "project": {"id": row.project_id},
                                                        **payload}, ts)
        log.info("task %s %s: %s -> %s", task_id, action, row.status, target)
        self._publish(event)
        return _to_task(new)

    def _append(self, row: TaskRow, event_type: EventType, payload: Mapping[str, Any], ts: str) -> Envelope:
        event = new_event(
            event_type,
            json.loads(json.dumps(payload)),  # plain JSON copy (no mapping proxies)
            request_id=row.request_id,
            task_id=row.task_id,
            seq=self.events.next_seq(row.task_id),
            producer=self.producer,
            timestamp=parse_ts(ts),
        )
        self.events.append(event)
        return event

    def _publish(self, event: Envelope) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(event)
        except Exception as exc:  # committed state is the truth; notification is best effort
            log.warning("task event %s notification failed: %s", event.type, type(exc).__name__)
