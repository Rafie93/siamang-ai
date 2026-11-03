# Planner (Phase 13)

**Status:** implemented (`src/siamang/planner/`). Runtime is still
stdlib-only. No schema change (v3), no contract change (v0.2), no new
config, no events, no model provider.

## 1. Purpose

The Planner answers **"what work needs to be done to satisfy this task?"**
with a validated, immutable, acyclic `Plan`. A plan is **data**: execution
is a separate concern for later phases.

```
Planner      = WHAT should happen
Orchestrator = WHO / WHEN (one agent, one step; Phase 12)
Agent        = HOW the work is done (Phase 11)
Tool         = ACTION on the environment (Phase 09)
```

## 2. Responsibilities

* Validate a `PlanningRequest`.
* Read the task through `TaskEngine.get`: it must exist, belong to the
  requested project, and not be final.
* Check the project through `ProjectRegistry`: it must exist and be active.
* Ask an allowlisted planning strategy for steps.
* Bound them with `PlanningPolicy`.
* Return a `Plan` that has validated itself.

## 3. Non-responsibilities

The Planner does **not**:

* execute anything, or invoke agents, the orchestrator, tools, or models;
* read files, walk directories, or call Git (project-aware analysis is the
  future System Analyst's job);
* change task or project state, retry, or schedule;
* grant permissions or choose agents or models;
* persist plans or emit events;
* integrate an API or messaging channel.

## 4. Architecture

```
                 TASK (queued / running / waiting / failed)
                   │  TaskEngine.get (read only)
                   ▼
               PLANNER ──► ProjectRegistry.get (exists, active)
                   │  ──► PlanningStrategy.propose (allowlisted, pure)
                   │  ──► PlanningPolicy (bounds)
                   ▼
              VALIDATED PLAN  (data; in memory only)
                   │
             ┌─────┴─────┐
          STEP 1       STEP 2 ...      (ordered; dependencies acyclic)
             └─────┬─────┘
                   ▼  (later phase, not Phase 13)
              ORCHESTRATOR ──► AGENT
```

| Module | Role |
|---|---|
| `planner/errors.py` | `PlanningError` + 9 codes |
| `planner/request.py` | `PlanningRequest`, shared text/JSON validation helpers |
| `planner/step.py` | `PlanStep`, `StepRole`, positional step IDs |
| `planner/plan.py` | `Plan` with graph validation, `content` / `content_hash` |
| `planner/policy.py` | `PlanningPolicy`, `DEFAULT_POLICY` |
| `planner/strategy.py` | `PlanningStrategy` (ABC), `TemplateStrategy`, `KNOWN_STRATEGIES` allowlist |
| `planner/service.py` | `Planner.plan` |

Dependency direction (enforced by `tests/unit/test_planner_architecture.py`):

```
cli ──► planner ──► tasks (definition, errors, lifecycle; the TaskEngine type, .get only)
                 └─► projects, ids, log
planner ─✗─► agents, orchestrator, tools, models, git, storage, bus, config
agents, orchestrator, tasks, projects, models, tools, storage, git, contracts ─✗─► planner
only cli/main.py and cli/checks.py import siamang.planner
```

The module that runs planning is `service.py`. An existing boundary test
forbids module files whose name contains `planner`; the package directory is
`siamang.planner`.

## 5. Request

`PlanningRequest(task_id, project_id, instruction, input={}, constraints=(),
metadata={})`, frozen. It means: "create a plan for this existing task".

* `task_id` / `project_id`: the Task Engine's strict validators.
* `instruction`: 1–10 000 characters (the policy default is stricter at
  4000), no control characters except `\n`/`\t`. Opaque text:
  `"run rm -rf /"`, `"open /etc/passwd"`, `"call git"` are planning input,
  never acted on.
* `input` / `metadata`: JSON objects under the Task Engine metadata policy
  (≤ 16 KiB, ≤ 64 keys, ≤ 5 levels, no NaN, no credential keys,
  command/argv/path/cwd/shell keys refused at any depth). The planner also
  refuses, at any depth: `agent`, `agent_id`, `tool`, `model`, `provider`,
  `retry`, `workflow`, `callback`, `executor`, `execute`, `run`, `git`,
  `module`, `import`, `function`, `class`, `handler`, `orchestrator`, and
  similar keys.
* `constraints`: up to 20 single-line strings.
* `from_dict` refuses unknown fields, and refuses execution-style fields with
  a dedicated message.

## 6. Plan

`Plan(plan_id, task_id, project_id, objective, steps, constraints=(),
metadata={})`, frozen. `steps` is a tuple of frozen `PlanStep`s, and the
caller's list is copied.

* `plan_id`: `pln_<ULID>` from `siamang.ids` (new `plan` prefix). Identity:
  new on every call, **not** deterministic.
* `objective`: the normalized instruction.
* `constraints`: the request's constraints, also copied onto each step.
* `metadata`: `{"strategy": "template.basic", "strategy_version": "1"}`.
* `execution_order()`: the step IDs in a valid order (the list order).
* `content()` / `content_hash` (`sha256:`): everything except `plan_id`.
* `to_dict()`: JSON-ready, includes `plan_id` and `content_hash`.

## 7. Plan step

`PlanStep(step_id, title, description, role, dependencies=(),
constraints=(), metadata={})`, frozen.

* `step_id`: positional and deterministic within a plan: `step_01`,
  `step_02`, … It is not an agent ID.
* `role` (`StepRole`): `analysis`, `design`, `implementation`, `testing`,
  `review`, or `documentation`. It is the *kind* of work. It assigns no
  agent: which agent does a step is an orchestration concern.
* `title` ≤ 120 characters on one line; `description` ≤ 2000 (policy
  default 1000).
* `dependencies`: step IDs, unique, never the step itself.
* `metadata`: the same policy and execution-key rules as the request.

## 8. Validation

A `Plan` validates itself on construction, so an invalid plan cannot exist:

| Check | Error |
|---|---|
| `plan_id`, `task_id`, `project_id` well formed | `planning_invalid_plan` |
| at most 50 steps (hard cap), exact `PlanStep` objects | `planning_too_many_steps` / `planning_invalid_plan` |
| step IDs unique | `planning_invalid_plan` (duplicates in `details`) |
| dependencies name steps of this plan, never the step itself | `planning_invalid_plan` |
| dependency graph acyclic | `planning_dependency_cycle` (cycle's step IDs in `details`) |
| every dependency listed before its dependent | `planning_invalid_plan` |
| metadata policy | `planning_invalid_plan` |

The service also checks identity (the plan is built from the request's own
task and project IDs, after confirming the task belongs to that project) and
applies `PlanningPolicy`:

| Policy (default) | Error |
|---|---|
| `max_steps = 12` (≤ 50) | `planning_too_many_steps` |
| `max_instruction_length = 4000` (≤ 10 000) | `planning_policy_denied` |
| `max_description_length = 1000` (≤ 2000) | `planning_policy_denied` |
| `allow_empty_plan = False` | `planning_policy_denied` |
| plannable statuses: queued, running, waiting, failed (never completed/cancelled) | `planning_policy_denied` |

A policy cannot exceed the hard caps or allow planning final tasks.

## 9. Dependency graph

Dependencies form a directed graph that must be acyclic. Cycle detection is
an iterative depth-first search, so a 50-step chain cannot hit the recursion
limit, and a cycle is reported with its step IDs. The planner also requires
the list order to be a topological order, so a consumer can run steps in
list order and never meets a dependency it has not yet seen. Parallel
branches are allowed: two steps may depend on the same earlier step.

## 10. Deterministic planning

The only strategy, `template.basic`, is a fixed four-step decomposition:

| Step | Role | Title | Depends on |
|---|---|---|---|
| `step_01` | analysis | Analyze the requested change | — |
| `step_02` | analysis | Identify affected components | `step_01` |
| `step_03` | design | Define implementation approach | `step_02` |
| `step_04` | testing | Define validation and test requirements | `step_03` |

For the same normalized request (same task, project, trimmed instruction,
and constraints), `content()` and `content_hash` are identical. No random
value, timestamp, path, environment variable, or thread ID is part of plan
content. Only `plan_id` differs, and the tests distinguish identity from
content. The template is **not** intelligent analysis and states no facts
about the project. Step 1 says that no project files are inspected at
planning time.

## 11. Security boundary

Enforced by `tests/unit/test_planner_security.py` and
`test_planner_architecture.py`:

* **Static checks with deliberate breaks.** Planner sources import no
  `subprocess`, `os`, `pathlib`, `socket`, `sqlite3`, `importlib`, `glob`,
  `shutil`, `random`, `uuid`, `time`, `asyncio`, storage, Git, models,
  tools, agents, orchestrator, or LLM frameworks. They use no `eval`,
  `exec`, `compile`, `open`, or `getattr`. They reference no `GitClient`,
  `GitWorkspaceManager`, `ToolRegistry`, `ModelProvider`, `AgentRegistry`,
  `Orchestrator`, or `Database`. They call no Task Engine mutator,
  `execute`, `invoke`, `generate`, `select`, `run`, or `archive`.
* **Runtime tripwires.** Planning (with a hostile instruction) and a refused
  request run with tripwires on processes, `os.listdir/scandir/walk/getenv`,
  sockets, `GitClient.run`, `ToolRegistry.invoke`, the router and
  providers, `AgentRegistry.invoke`, `Orchestrator.execute`,
  `Database.transaction`, `EventStore.append`, and every Task Engine
  mutator. None fire, and no file is opened.
* **Strategies are allowlisted by ID.** No class, instance, or callable is
  accepted. A strategy receives only the request and the frozen `Task`
  snapshot, and its output is bounded and validated. A crashing strategy
  yields `planning_internal` with only the exception type name.
* **Logs** carry only task, project, and plan IDs, the step count, and the
  outcome.

## 12. Relationship with the Orchestrator

The planner never calls the orchestrator, and the orchestrator does not
import the planner. A plan is structured so that a later phase can consume
it: run steps in `execution_order()`, pick an agent per step `role`, call
`Orchestrator.execute` once per step, and apply results to the task through
the Task Engine. **None of that is implemented in Phase 13.**

## 13. Future model-backed planning boundary

`PlanningStrategy.propose(request, task) -> tuple[PlanStep, ...]` is the
seam. A future model-backed strategy would be added to `KNOWN_STRATEGIES`,
reach a model only through the Model Router, and return proposed steps that
pass exactly the same policy and plan validation:

```
PlanningRequest ─► strategy (model-backed, future) ─► proposed steps ─► policy ─► Plan validation ─► Plan
```

Free-form model text is never executed. Only validated structure leaves the
planner. Not implemented in this phase.

## 14. Limitations

* Planning is a fixed template, the same four steps for every task. It does
  not understand the instruction and knows nothing about the project.
* The instruction is carried as the objective but does not shape the steps.
* Plans are in memory only: no persistence, history, or event; `plan_id` is
  not stored anywhere.
* Nothing consumes plans yet: no plan execution, and no mapping from step
  role to agent.
* `PlanStep.role` values are a fixed enum. New kinds of work need a code
  change.
* The plan is built from a task snapshot. A task that changes afterwards is
  not re-checked.
* The CLI only shows the policy; there is no command to plan a task.

## 15. Examples

```python
planner = Planner(task_engine)                      # strategy="template.basic", DEFAULT_POLICY
plan = planner.plan(PlanningRequest(
    task_id=task.task_id, project_id=project.project_id,
    instruction="Add login validation", constraints=("no new dependencies",)))

plan.execution_order()   # ('step_01', 'step_02', 'step_03', 'step_04')
plan.steps[2].role       # StepRole.DESIGN
plan.content_hash        # same for the same request; plan.plan_id differs per call
task_engine.get(task.task_id).status   # unchanged (e.g. still queued)
```

```text
$ siamang planner
strategy                  template.basic (deterministic)
max steps                 12
max instruction length    4000
max step description      1000
empty plan allowed        no
plannable task status     failed,queued,running,waiting
executes / changes tasks  no
persists / emits events   no
```

## Phase 13 decisions

IDs use `PL` because `P1–P11` are the Phase 07 Project Registry decisions.

| # | Decision |
|---|---|
| PL1 | Planner is decomposition-only (WHAT, not WHO/HOW) |
| PL2 | Planner does not execute: no agent, orchestrator, tool, or model call |
| PL3 | Planner does not mutate tasks or projects; a queued task stays queued |
| PL4 | Planner does not inspect the filesystem or Git |
| PL5 | Planner does not depend on agents or the orchestrator (upstream of execution) |
| PL6 | Plans validate themselves; an invalid plan cannot be constructed |
| PL7 | Plans are acyclic, and list order is a valid execution order |
| PL8 | Planning content is deterministic; `plan_id` is identity, not content |
| PL9 | No persistence and no events (schema v3, contract v0.2) |
| PL10 | No model provider; `PlanningStrategy` is the future model boundary, allowlisted by ID |
| PL11 | Bounded by `PlanningPolicy` under fixed hard caps (50 steps, 10 000-char instruction, 2000-char description) |
| PL12 | Final tasks (completed, cancelled) are not planned; queued, running, waiting, and failed are |
| PL13 | Request validation reuses the Task Engine policy, plus planner execution keys at any depth |
| PL14 | Read-only CLI `siamang planner [--json]` plus a `validate` check; no `planner run/execute/create` |
| PL15 | The service module is `service.py` (boundary test on module names) |
