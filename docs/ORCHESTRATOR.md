# Orchestrator (Phase 12)

**Status:** implemented (`src/siamang/orchestrator/`). Runtime is still
stdlib-only. No schema change (v3), no contract change (v0.2), no new
config, no new events.

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Purpose

The Orchestrator coordinates **one execution step**: for an explicit
request, it runs one already-registered agent once, for one task that is
already running, and returns a structured result.

```
Task (running, via TaskEngine)
      │
      ▼
Orchestrator.execute(OrchestrationRequest)
      │   TaskEngine.get · ProjectRegistry.get · AgentRegistry.get · build_context   (read only)
      ▼
AgentRegistry.invoke(agent_id, AgentRequest, AgentContext)        ← exactly once
      │
      ▼
Agent.run ──► ModelAccess ─► ModelRouter      ToolAccess ─► ToolRegistry ─► PermissionPolicy
      │
      ▼
OrchestrationResult  (mirrors the AgentResult; the task is untouched)
```

> **A coordination service, not an AI brain.** It plans nothing, chooses
> no "next" agent, loops over nothing, retries nothing, changes no task
> state, persists nothing, and emits no events.

## 2. Responsibilities

* Validate an `OrchestrationRequest`.
* Read the task through `TaskEngine.get` and check that it is `running` and
  belongs to the requested project.
* Check the project through `ProjectRegistry` (exists, active).
* Resolve the agent through `AgentRegistry.get` (registered, enabled,
  allowed by policy).
* Build the context with Phase 11 `build_context`.
* Call `AgentRegistry.invoke` once.
* Translate the outcome into an `OrchestrationResult` with typed errors.

## 3. Non-responsibilities

The Orchestrator does **not** own task lifecycle (Task Engine), project
identity (Project Registry), agent binding or validation (Agent Registry),
model selection (Model Router), tool authorization (Tool Registry +
PermissionPolicy), planning or decomposition (Phase 13 Planner), sequencing
several agents (later workflow phases), approval (Phase 19), retries,
scheduling, persistence, events (Phase 23), or channels such as the API
or messaging.

## 4. Request and result

`OrchestrationRequest(task_id, project_id, agent_id, instruction, input={},
constraints=(), metadata={})`, frozen.

* `agent_id` is an agent ID (`agent.<role>`), for example `agent.echo`. A
  class name, module or import path, implementation ID (`reference.echo`),
  list, or callable is refused.
* The other fields are validated by constructing the `AgentRequest` this
  request becomes, so the Task/Agent rules apply unchanged: strict task and
  project IDs; input and metadata are JSON objects that refuse command,
  argv, path, and credential keys at any depth. The orchestrator adds `git`,
  `git_command`, `module`, `import`, `function`, `class`, `handler`, and
  similar keys, also at any depth.
* `constraints` follow the Agent layer: up to 20 short text lines (not a
  mapping).
* `from_dict` refuses unknown fields, and refuses execution-style fields
  (`shell`, `command`, `argv`, `cwd`, `root_path`, `path`, `git`,
  `git_command`, `python`, `callable`, `function`, `module`, `import`,
  `agents`, …) with a dedicated message.

`OrchestrationResult(orchestration_id, task_id, project_id, agent_id,
status, agent_result, error, metadata)`, frozen, JSON-ready via
`to_dict()`.

* `orchestration_id`: `orc_<ULID>` from `siamang.ids` (new `orchestration`
  prefix). Not persisted.
* `status` (`completed` / `failed` / `waiting`) mirrors the single
  `AgentResult`, or is `failed` when the orchestration was refused before
  any agent ran (then `agent_result` is `None`).
* `metadata`: `duration_ms`, `agents_invoked` (0 or 1).

## 5. Task validation

The task is read with `TaskEngine.get` (no SQL, `Database`, `EventStore`,
or `TaskRepository`). Unknown → `task_not_found`. A task of another project
→ `request_invalid`. Any status other than `running` → `task_not_running`.
A `queued` task is **not** started: the caller starts it through the Task
Engine first.

## 6. Project validation

`ProjectRegistry.get` (the registry the Task Engine itself uses): unknown →
`project_not_found`, archived → `project_archived` (never restored).
Paths are never accepted as project identifiers. Phase 11 `build_context`
resolves the project again and gives the agent the narrow `AgentProject`
view without `root_path`.

## 7. Agent resolution

`AgentRegistry.get(agent_id)` → `agent_not_found`; a disabled definition →
`agent_disabled`; an `OrchestrationPolicy(allowed_agents=…)` allowlist, if
set → `policy_denied`. The orchestrator never instantiates an agent class,
imports a module, or uses `getattr`: the registry binds implementations from
its allowlist.

## 8. AgentContext construction

The Phase 11 public builder:
`build_context(definition, task=…, projects=…, router=…, tools=…,
metadata={"orchestration_id": …})`. The context keeps exactly the Phase 11
fields (`agent_id`, `task`, `project`, `models`, `tools`, `permissions`,
`metadata`). The orchestrator passes the model router and tool registry it
was given through untouched. A raw service (for example a `GitClient` as
`tools`) is refused by the builder → `context_invalid`.

## 9. One agent per orchestration

One request names one task and one agent, and `execute` calls
`AgentRegistry.invoke` exactly once, or not at all if refused earlier. The
policy constant `agents_per_orchestration = 1` is not configurable. The
security tests check that the service source has exactly one `invoke` call,
that it is on the Agent Registry, and that there are no `for`/`while` loops.

## 10. No autonomous loop

No loop, retry, continuation, or chaining. A `completed` echo is simply
returned. Nothing infers a next agent from a result. A retryable agent error
is reported with `retryable=true` and is not retried.

## 11. No task mutation

`execute` never calls `TaskEngine.create/start/wait/requeue/complete/fail/
cancel/retry`. An agent's `completed` does not complete the task, `failed`
does not fail it, and `waiting` does not move it to `waiting`. Tested by
comparing the task and its event count before and after successful,
waiting, failed, and refused executions, and with tripwires on every
mutator.

## 12. Model and tool boundaries

The orchestrator imports nothing from `siamang.models` or `siamang.tools`,
and calls no router, provider, or tool registry. Agents reach models only
through `ModelAccess → ModelRouter` and tools only through `ToolAccess →
ToolRegistry → PermissionPolicy` (Phase 11). No model selection and no
tool permission logic exist here.

## 13. Security

Enforced by `tests/unit/test_orchestrator_security.py` and
`test_orchestrator_architecture.py`:

* **Static checks with deliberate breaks.** Orchestrator sources import
  no `subprocess`, `os`, `pathlib`, `socket`, `sqlite3`, `importlib`,
  `threading`, `asyncio`, storage, git, models, tools, config, or agent
  implementation modules. They use no `getattr`/`eval`/`exec`/`open`. They
  reference no `Database`, `EventStore`, `GitClient`, `ToolRegistry`,
  `ModelRouter`, `ModelProvider`, or `EchoAgent`. They call no task
  mutator, `generate`, `select`, `run`, or `append`. Exactly one `invoke`
  is allowed, and only on the Agent Registry.
* **Runtime tripwires.** Successful and refused executions run with
  tripwires on processes, the filesystem, the network, `GitClient.run`,
  `ToolRegistry.invoke`, the router and provider, `Database.transaction`,
  `EventStore.append`, and every Task Engine mutator. None fire.
* **Nesting.** A `ContextVar` refuses `execute` inside `execute`
  (`policy_denied`). An agent run directly by the registry that calls the
  orchestrator is stopped by the Phase 11 agent guard
  (`agent_invocation_failed`, cause `agent_permission_denied`).
* **Logs** carry only the orchestration, task, project, and agent IDs and
  the status. Never the instruction, input, metadata, or output.

## 14. Error handling

Typed `OrchestrationError` with codes: `request_invalid`,
`task_not_found`, `task_not_running`, `project_not_found`,
`project_archived`, `agent_not_found`, `agent_disabled`,
`agent_invocation_failed`, `policy_denied`, `context_invalid`,
`result_invalid`, `internal`.

An agent error is translated, keeping the agent code as `cause` and
keeping `retryable` and `details` (field names, never values):

| Agent code | Orchestration code |
|---|---|
| `agent_not_found` / `agent_disabled` | same name |
| `agent_context_invalid` | `context_invalid` |
| `agent_result_invalid` | `result_invalid` |
| anything else (`agent_execution_failed`, `agent_model_unavailable`, `agent_input_invalid`, `agent_permission_denied`, …) | `agent_invocation_failed` |

A result whose agent or task does not match the request →
`result_invalid`. Any unexpected exception → `internal` with only the
exception type name. Messages are fixed and redacted: no traceback, secret,
path, or raw output.

## 15. Persistence decision

None (O6). No table, migration, or record of orchestrations or agent
executions. Schema stays v3. `orchestration_id` exists only in the result,
the log line, and the agent's context metadata.

## 16. Event decision

None (O7). No `orchestration.*` event type, no `EventStore.append`, no bus
publish. Execution history as events belongs to a later workflow/event phase
(23).

## 17. CLI

`siamang orchestrator [--json]` shows the policy and the orchestratable
agents. It never opens the database or runs an agent. There is **no**
`orchestrator run/execute/invoke`. `siamang validate` reports
`Orchestrator  OK`.

## 18. Phase 12 decisions

| # | Decision |
|---|---|
| O1 | Synchronous orchestration (`execute`), no async, threads, queues, or workers |
| O2 | One agent per orchestration; exactly one `AgentRegistry.invoke` |
| O3 | No autonomous loop, retry, or continuation |
| O4 | No task mutation; the Task Engine owns lifecycle |
| O5 | `AgentRegistry` is the only agent execution boundary; context via Phase 11 `build_context` |
| O6 | No persistence |
| O7 | No orchestration events |
| O8 | No direct model or tool access; router and tools are passed through to the context untouched |
| O9 | The task must already be `running`; policy admits nothing else in Phase 12 |
| O10 | No agent chaining; nested orchestration refused (ContextVar), plus the Phase 11 agent guard |
| O11 | Request validation reuses `AgentRequest`, with extra execution-style keys (`git`, `module`, `import`, `function`, …) refused at any depth |
| O12 | Service module named `service.py`: an existing boundary test (`test_no_out_of_scope_modules`) forbids any module file whose name contains `orchestrator`; the package directory is `siamang.orchestrator` |
| O13 | Agent IDs follow the repository (`agent.echo`); `reference.echo` is the implementation ID and is refused as an `agent_id` |
| O14 | Agent errors are translated with the agent code kept as `cause` (§14) |
| O15 | Read-only CLI `siamang orchestrator [--json]` plus a `validate` check; no `run/execute/invoke` |

## Known limitations

* The only agent is the reference `agent.echo`. Nothing in the product
  calls `execute` yet (no API, CLI execution, or workflow).
* The task snapshot is read once per execution. A task changed concurrently
  (for example cancelled) while the agent runs is not re-checked, and the
  result does not reflect it.
* `router`/`tools` are not type-checked by the orchestrator itself. A wrong
  object is only refused when an agent that declares that need is built a
  context (`context_invalid`); agents without that need never see it.
* Orchestrations are visible only in logs: no history, audit trail, or event.
* The nesting guard is per thread/async context; agents may not import
  `threading` (Phase 11), so a nested call from another thread is not
  otherwise blocked.
