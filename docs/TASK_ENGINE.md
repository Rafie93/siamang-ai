# Task Engine + Persistence (Phase 10)

**Status:** implemented (`src/siamang/tasks/`, `src/siamang/storage/tasks.py`,
schema v3, contract v0.2). Runtime is still stdlib-only.

The Task Engine owns **task state**: it validates requests, assigns task IDs,
persists tasks, enforces the lifecycle, appends lifecycle events, and answers
queries after a restart. Future Agent/Orchestrator layers will drive it.

> **The Task Engine manages state. It never executes anything.**
> No agent, orchestrator, planner, model, tool, Git, subprocess, shell,
> network, or messaging channel is called from any task method.
> `start`, `complete`, and `fail` record what *someone else* did;
> `retry` only re-queues.

## 1. Architecture

```
TaskRequest ──► TaskEngine (tasks/engine.py)
                   │
                   ├──► ProjectRegistry      project exists and is active (never raw SQL)
                   ├──► TaskRepository       storage/tasks.py, SQL only, compare-and-set update
                   ├──► EventStore           same SQLite transaction as the state change
                   └──► EventBus (optional)  notified only after commit
```

| Module | Role |
|---|---|
| `tasks/errors.py` | `TaskError` + 8 typed codes (`TaskErrorCode`) |
| `tasks/lifecycle.py` | `TaskStatus`, `TaskAction`, `TRANSITIONS` (read-only), `EVENTS`, `plan()` |
| `tasks/definition.py` | `Task`, `TaskRequest`, `TaskFailure`, `TaskResult`, `TaskPriority`, `TaskSource`, field validation |
| `tasks/engine.py` | `TaskEngine`, `TaskSubmission`, `RecoveryReport` |
| `storage/tasks.py` | `TaskRow`, `TaskRepository` (no domain rules, imports nothing from `tasks`) |
| `storage/schema_v3.sql` | the `tasks` table rebuilt for the engine |

Dependency direction (enforced by `tests/unit/test_tasks_architecture.py`):

```
cli ──► tasks ──► projects, storage, contracts, bus, ids, log
tasks ─✗─► models, tools, git, config, cli, any future layer (agents, orchestrator, planner, …)
only cli/main.py imports siamang.tasks
```

## 2. Task domain

`Task` is a frozen dataclass; mappings inside it are read-only.

| Field | Notes |
|---|---|
| `task_id` | `tsk_<ULID>` from `siamang.ids.new_id("task")`; may be supplied explicitly (like `project_id`) |
| `request_id` | `req_<ULID>` or caller-supplied; makes creation idempotent (contract §8) |
| `project_id` | mandatory, validated through `ProjectRegistry` |
| `parent_task_id` | optional; must be an existing task of the same project |
| `title` | 1–200 chars; derived from the first line of `description` when omitted |
| `description` | 1–10 000 chars, no control characters except `\n`/`\t` |
| `status` | see §3 |
| `priority` | `low` / `normal` (default) / `high` / `urgent` |
| `source` | `cli` / `api` / `telegram` / `system` / `test`; **required**, identifier only |
| `retry_count`, `max_retries` | `0 ≤ retry_count ≤ max_retries ≤ 10`; default `max_retries = 3` |
| `metadata` | JSON object (§2.1) |
| `last_error` | `TaskFailure` of the most recent failure (kept after a retry, as history) |
| `result` | `TaskResult` once completed |
| `created_at`, `updated_at`, `started_at`, `completed_at`, `failed_at`, `cancelled_at` | UTC |

Deliberately **not** domain fields: `agent_id`, `planner_id`, `model_id`,
`tool_id`, `branch`, `commit`, `worktree`, `telegram_message_id`. Execution
details a future agent wants to record go into `TaskResult.details`,
`TaskFailure.details`, or the event payload, never into ownership columns.

`TaskRequest(project_id, description, source, title=None, priority="normal",
max_retries=3, parent_task_id=None, metadata=None, request_id=None,
task_id=None)`. `TaskRequest.from_dict` is the strict constructor for
untrusted input: unknown fields are refused, and execution-style fields
(`shell`, `argv`, `command`, `cwd`, `root_path`, `repository_path`,
`git_args`, `python`, `callable`, `tool`, `agent_id`, `model_id`, `branch`,
`worktree`, …) get a dedicated refusal.

### 2.1 Metadata policy

Reuses the Project Registry policy (`projects.definition.canonical_metadata`)
instead of a second implementation: JSON object only, ≤ 64 keys, ≤ 5 levels,
≤ 16 KiB, no NaN/Infinity, no credential-looking keys (`password`, `api_key`,
`token`, `secret`, …). Tasks add one stricter rule: keys naming commands,
argv, paths, or credentials (`command`, `cmd`, `argv`, `args`, `shell`,
`script`, `exec`, `python`, `callable`, `git_args`, `path`, `root_path`,
`repository_path`, `cwd`, `directory`, `private_key`, `credential(s)`,
`env`, `environment`) are refused **at any depth**. The same policy applies to
`TaskFailure.details`, `TaskResult.result`, and `TaskResult.details`.

## 3. Lifecycle

```
queued ──start──► running ──complete──► completed
  ▲  ▲              │  ▲
  │  └──requeue── waiting ◄─wait─┘   (waiting ──start──► running)
  │
  └──retry── failed ◄──fail── queued | running | waiting
             cancelled ◄──cancel── queued | running | waiting
```

| Action | From | To | Event |
|---|---|---|---|
| `create` | — | `queued` | `task.accepted` |
| `start` | `queued`, `waiting` | `running` | `task.started` |
| `wait` | `running` | `waiting` | `task.waiting` |
| `requeue` | `waiting` | `queued` | `task.requeued` |
| `complete` | `running` | `completed` | `task.completed` |
| `fail` | `queued`, `running`, `waiting` | `failed` | `task.failed` |
| `cancel` | `queued`, `running`, `waiting` | `cancelled` | `task.cancelled` |
| `retry` | `failed` | `queued` | `task.retry_requested` |

`completed` and `cancelled` are final. `failed` is final except for the
explicit, counted `retry`. Everything else (for example `completed → running`,
`cancelled → running`, `failed → running`) raises `InvalidTransitionError` and
changes nothing. `start` from `waiting` keeps the original `started_at`.

## 4. Persistence

Schema step **v3** (`storage/schema_v3.sql`) rebuilds the Phase 3 `tasks`
table, because SQLite cannot alter CHECK constraints. The table is `STRICT` and
its CHECKs refuse malformed task/project/request IDs (including a trailing
newline), unknown status/priority/source, `retry_count < 0`,
`retry_count > max_retries`, `max_retries > 10`, non-object JSON in
`metadata`/`last_error`/`result`, malformed timestamps, `updated_at <
created_at`, and status/timestamp incoherence (for example `completed` without
`completed_at`). Triggers forbid `DELETE` and any change to `task_id`,
`request_id`, `project_id`, `parent_task_id`, or `created_at`.

Timestamps are fixed-width UTC with milliseconds (`YYYY-MM-DDTHH:MM:SS.mmmZ`),
so string order is time order. `updated_at` never moves backwards, even if the
clock does.

Indexes: `(project_id, created_at, task_id)` and `(status, created_at,
task_id)`, matching the only two list filters. Listing is one query, with no
per-task project lookups.

Migration: v2 → v3 copies existing Phase 3 rows in place (`accepted → queued`,
`waiting_approval → waiting`, `source = system`, `metadata =
{"migrated_from":"schema_v1"}`). A legacy row without a valid `project_id` or
timestamp cannot be copied: the step fails, the whole transaction rolls back,
and the database stays at v2. A database newer than v3 is refused. Fresh
databases apply v1 → v2 → v3 in order.

## 5. Events

Task events go into the **existing** `events` table through `EventStore`,
with the existing envelope (`contracts.envelope.new_event`) and the per-task
`seq` sequence. There is no second event format or database.

The contract went from **v0.1 to v0.2** (additive MINOR, following the
existing versioning rules): new Siamang-produced types `task.waiting`,
`task.requeued`, and `task.retry_requested`, plus optional `status`, `project`
(and `details` on `task.completed`) on task events, so lifecycle history can be
rebuilt from events alone. Creation emits the contract's existing
`task.accepted` rather than a new `task.created`.

Every lifecycle event carries `status`, `project.id`, and `summary`;
`task.failed` carries `error {code, message, retryable, step?, details}`;
`task.retry_requested` carries `retry_count` and `max_retries`. Payloads carry
the title (inside `summary`) but never the description, metadata, environment
data, tracebacks, or paths.

`engine.history(task_id)` replays a task's events ordered by `seq`.

## 6. Transactions and concurrency

Every mutation is **one** SQLite transaction:

```
BEGIN IMMEDIATE → read row → plan(action, status) → compare-and-set UPDATE
→ append event (next seq) → COMMIT
```

Any exception (invalid transition, payload rejected by the contract schema,
event append failure, constraint violation) rolls back both the state change
and the event. A state change without its event, or an event without its
state change, cannot be committed. `Database.transaction` is re-entrant (inner
calls become `SAVEPOINT`s), so repository and event-store writes join the
engine's transaction.

`BEGIN IMMEDIATE` serializes writers across connections and processes. The
`UPDATE` also checks that the row still equals what was read. Of two callers
that `start` the same task at once, exactly one succeeds; the other re-reads
`running` and gets `InvalidTransitionError` (tested with four threads, each
with its own connection).

The `EventBus`, when given, is notified **after** commit, so subscribers never
see a rolled-back event. Persistence does not go through the bus, because
that would put subscriber code inside the transaction. A subscriber error is
logged and does not undo committed state. Do not also attach an `EventStore`
to that bus, or task events would be stored twice.

## 7. Failure

`fail(task_id, error)` accepts a `TaskFailure` or any exception.

* `TaskFailure(code, message="", retryable=False, step=None, details={})`:
  `code` must look like an error code (`TOOL_FAILED`, `not_repository`, …).
  `message` has control characters removed, is redacted with the shared log
  redactor, has URL userinfo stripped, and is capped at 1 000 chars.
* `TaskFailure.from_exception(exc)`: a SIAMANG-style error (`code`,
  `message`, `retryable` attributes, as in the model/Git/tool layers) keeps
  them, redacted; anything else becomes `INTERNAL`, not retryable, with only
  `details.cause = <exception type name>`. No traceback, raw exception, or
  exception text is stored.

## 8. Retry

`retry(task_id)` is an explicit state operation: `failed → queued`,
`retry_count + 1`, event `task.retry_requested`. It refuses with
`RetryLimitReachedError` when `retry_count == max_retries`, and with
`NotRetryableError` when the last failure has `retryable = false`. Nothing is
executed or scheduled: no automatic retry loop, backoff, model, tool, or Git
call exists.

## 9. Recovery

After a crash, a task that was `running` **stays `running`**. State alone
cannot tell whether its work finished, so the engine never marks it completed,
failed, or re-queued by itself. `engine.recovery()` (and `siamang tasks
recovery`) returns a read-only `RecoveryReport` of `running`, `waiting`, and
`queued` tasks and logs a warning when running tasks exist. An operator (or
Phase 12) then decides explicitly: `cancel`, or `fail` and then `retry`.

## 10. Project relationship

A task belongs to exactly one project. `create` validates `project_id` format
(strict, `\Z`-anchored), then calls `ProjectRegistry.get`: unknown projects
raise `TaskProjectNotFoundError`, archived ones raise
`TaskProjectArchivedError` and are never restored automatically. Paths are
never accepted in place of a project ID. `TaskEngine` requires the registry
to share its `Database`, so the project check and the insert are in one
transaction.

**No foreign key** `tasks.project_id → projects` (decision K5). The Project
Registry stays independent, as in Phase 07 (P11). Projects cannot be deleted
(trigger), so a dangling reference cannot appear through normal operation.
Application-level validation plus the `(project_id, …)` index cover lookups,
and a FK would make the v3 rebuild depend on project rows that legacy
databases may not have.

## 11. Security boundary

Enforced by `tests/unit/test_tasks_security.py` and
`tests/unit/test_tasks_architecture.py`:

* Task sources import no `subprocess`, `socket`, `os`, `pathlib`, `shutil`,
  `http`, `urllib`, `asyncio`, `threading`, `importlib`, `pickle`, Git
  libraries, or LLM SDKs, and reference no `system`, `popen`, `environ`,
  `getenv`, `listdir`, `scandir`, `eval`, `exec`, `open`, `GitClient`,
  `ToolRegistry`, `ModelRouter`, `invoke`, or `generate`. Deliberate-break
  snippets prove that the checker catches each.
* A full lifecycle (create, start, wait, requeue, fail, retry, complete,
  cancel, list, history, recovery) runs with tripwires on `subprocess`,
  `os.system/popen/scandir/listdir`, `socket.connect`, `GitClient.run`,
  `ToolRegistry.invoke`, `ModelRouter.select`, `Selection.generate`, and
  `ModelProvider.generate`. None fire.
* Hostile requests are refused before anything is stored: malformed or
  trailing-newline IDs, paths as project IDs, unknown/archived projects,
  invalid status/priority/source, out-of-range retries, oversized,
  credential, command, shell, argv, path, or NaN metadata.
* The schema rejects the same bad rows on raw `INSERT` (defence in depth).
* Exception text containing secrets never reaches the database file.
* The engine exposes no `execute`/`run`/`dispatch`/`invoke` API.

The shared ID validators (`contracts.schema.ID_RE`, `PROJECT_ID_RE`,
`METADATA_KEY_RE`) now end in `\Z` instead of `$`, so `prj_x\n` is no longer
accepted (Phase 09 finding, fixed with regression tests).

## 12. CLI

```bash
siamang tasks [--json] [--project PRJ] [--status S] [--limit N] [--offset N]   # = tasks list
siamang tasks get <task_id> [--events] [--json]
siamang tasks recovery [--json]                  # running/waiting/queued; changes nothing
siamang tasks cancel <task_id> --yes [--reason TEXT] [--json]
siamang tasks retry <task_id> --yes [--json]     # failed → queued; nothing is executed
```

Read commands (`list`, `get`, `recovery`) never create the database (same
convention as `projects`). `cancel` and `retry` change lifecycle state only
and refuse without `--yes` (exit 2). There is **no** `tasks run`, `start`,
`complete`, or `create` command: driving a task belongs to future execution
layers. Errors print `error: <code>: <message>` and exit 1.

## 13. Future integration (not implemented)

```
Phase 11 Agent Interface ─┐
Phase 12 Orchestrator ────┴──► TaskEngine.start / wait / complete / fail
                               (and ToolRegistry.invoke, ModelRouter, separately)
```

The engine stays the state authority. An orchestrator picks `queued` tasks,
calls `start`, runs agents and tools **outside** the engine, then reports
`complete(TaskResult(..., details={...}))` or `fail(TaskFailure(...))`.
Granting the Tool Registry's `task` actor any permission, mapping
`GitError`/`ToolError` codes to task failures, and project-hint resolution
are left to those phases.

## 14. Phase 10 limitations

* No execution of any kind: no agent, orchestrator, planner, tool or model
  call, Git, worktree/branch creation, approval, or channel integration.
* No automatic retry, backoff, scheduling, or recovery policy; running tasks
  after a crash need an explicit decision.
* `retry` is refused when the last failure is non-retryable; there is no
  override.
* Tasks are durable: no delete or purge (trigger), and no archival.
* `parent_task_id` is a reference only: no subtask semantics or cascades.
* A duplicate `request_id` returns the existing task (`duplicate=True`)
  without appending a new `task.accepted` event.
* Tasks created in the same millisecond list in `task_id` order, which is
  deterministic but not necessarily creation order.
* The CLI cannot create, start, complete, or fail tasks; only the Python API
  can.
* The schema migration has no downgrade path; a failed v3 step rolls back to
  v2.
* Writers wait up to SQLite's `busy_timeout` (5 s); there is no distributed
  lock.
* Bus notification is synchronous, in-process, and best effort.
