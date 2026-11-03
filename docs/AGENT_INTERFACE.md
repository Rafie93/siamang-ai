# Agent Interface (Phase 11)

**Status:** implemented (`src/siamang/agents/`). Runtime is still stdlib-only.
No schema change (v3), no contract change (v0.2), no new config.

The Agent Interface defines what an agent **is**, what it **receives**, what
it may **reach**, and what it **returns**. It is a stable contract for the
specialized agents of later phases, plus one validating entry point,
`AgentRegistry.invoke`.

> **An agent is a typed execution capability, not an autonomous application.**
> **Only the reference agent `agent.echo` is registered here.** Planner, analyst, programmer, QA, and reviewer are separate services run by the HTTP runtime.
> **No workflow exists**: no loop, no agent-to-agent call, no retry, no planning.
> **Agents never change task state** and emit no task events. The Task Engine stays authoritative.
> **No API or messaging channel lives in this layer.**

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## Architecture

```
(future) Orchestrator, Phase 12
        │  invoke(agent_id, AgentRequest, AgentContext)
        ▼
AgentRegistry (agents/registry.py) ── allowlist (agents/implementations.py)
        │
        ▼
Agent.run(request, context) ──► AgentResult
        │
        ├── context.task      Task snapshot (frozen; from TaskEngine.get)
        ├── context.project   AgentProject (resolved through ProjectRegistry; no root_path)
        ├── context.models    ModelAccess ──► ModelRouter.select ──► Selection.generate ──► provider
        └── context.tools     ToolAccess  ──► ToolRegistry.invoke ──► PermissionPolicy ──► tools
```

| Module | Role |
|---|---|
| `agents/errors.py` | `AgentError` + 12 typed codes |
| `agents/definition.py` | `AgentDefinition`, `AgentCapability`, definition/input validation, output policy |
| `agents/request.py` | `AgentRequest` |
| `agents/result.py` | `AgentResult`, `AgentStatus` |
| `agents/interface.py` | `Agent` (ABC, synchronous `run`) |
| `agents/context.py` | `AgentContext`, `AgentProject`, `ModelAccess`, `ToolAccess`, `build_context` |
| `agents/implementations.py` | `KNOWN_IMPLEMENTATIONS` allowlist |
| `agents/reference.py` | `ECHO` definition, `EchoAgent` (reference only) |
| `agents/registry.py` | `AgentRegistry`, `build_agent_registry` |

Dependency direction (enforced by `tests/unit/test_agents_architecture.py`):

```
cli ──► agents ──► tasks (definition, errors, lifecycle; never tasks.engine)
                ├─► projects, contracts.schema, log
                ├─► models (router, request, response, capabilities, errors; never providers)
                └─► tools  (registry, permissions, definition, errors; never git_tools/implementations)
agents ─✗─► storage, git, bus, config, any future layer
projects, tasks, models, tools, storage, git, contracts ─✗─► agents
only cli/main.py and cli/checks.py import siamang.agents
```

## 1. Agent definition

`AgentDefinition` is a frozen dataclass of **data only**:

| Field | Rule |
|---|---|
| `agent_id` | `agent.<role>[.<variant>…]`, lowercase, `\Z`-anchored; segments like `run`, `exec`, `shell`, `eval`, `subprocess`, `autonomous`, `sudo` are refused |
| `role` | lowercase identifier equal to the ID's second segment; metadata only (no role behaviour) |
| `name`, `description` | 1–80 / 1–500 characters |
| `version` | `1`, `1.2`, or `1.2.3`; metadata for future compatibility, no migration machinery |
| `implementation` | an implementation **ID string** (never a callable) |
| `capabilities` | non-empty set of `AgentCapability` |
| `input_schema`, `output_schema` | contract mini-schema (`F`), output non-empty; input fields named like commands, argv, paths, or credentials are refused |
| `model_requirements` | Phase 06 `Capability` set; when non-empty it must include `text_generation` |
| `tool_permissions` | Phase 09 `Permission` set; when non-empty it must include `tool.invoke`; write/network/process permissions are refused |
| `enabled` | boolean |
| `metadata` | Project Registry metadata policy (≤ 16 KiB, ≤ 64 keys, ≤ 5 levels, no credential/executable keys) |

`to_dict()` leaves out the implementation binding. There is no `handler`,
`callable`, `factory`, or module path field.

## 2. Agent interface

```python
class Agent(ABC):
    definition: AgentDefinition
    @abstractmethod
    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult: ...
```

Synchronous, like the rest of SIAMANG: no `async` was introduced for future
speculation. The registry instantiates agents (`cls(definition)`); no other
code calls `run` directly, and `run` is called in exactly one place
(`AgentRegistry._run`), never in a loop.

## 3. Agent request

`AgentRequest(task_id, project_id, instruction, input={}, constraints=(), metadata={})`

* `task_id` / `project_id`: required, validated with the Task Engine's strict validators.
* `instruction`: 1–10 000 characters of prose, no control characters except `\n`/`\t`.
* `input`: a JSON object checked strictly against the agent's `input_schema`
  (unknown fields refused). It follows the Task Engine metadata rule:
  keys naming commands, argv, paths, or credentials (`command`, `argv`,
  `shell`, `cwd`, `root_path`, `git_args`, `python`, `callable`, `api_key`,
  …) are refused **at any depth**.
* `constraints`: up to 20 single-line strings.
* `AgentRequest.from_dict` is the strict constructor for untrusted input:
  execution-style fields (`shell_command`, `shell`, `argv`, `command`,
  `cwd`, `root_path`, `git_args`, `python`, `callable`, …) get a dedicated
  refusal, and any other unknown field (for example `git_command`) is
  refused as unknown.

## 4. Agent context

`AgentContext` is a frozen, `__slots__` dataclass with exactly these fields:
`agent_id`, `task`, `project`, `models`, `tools`, `permissions`, `metadata`.

* There is no `get(name)`, `services`, `__getattr__`, or other service locator.
* `task` must be a `Task` (the frozen Phase 10 domain object), not a dict
  or the engine.
* `project` must be an `AgentProject` (id, name, type, description,
  metadata). The full `Project` is refused, so `root_path` never reaches an
  agent: workspace access is possible only through tools.
* `models` / `tools` must be exactly `ModelAccess` / `ToolAccess` (not
  subclasses), bound to this agent (and, for tools, to this project).
  A `GitClient`, `Database`, `sqlite3.Connection`, `ModelRouter`,
  `ToolRegistry`, `GitWorkspaceManager`, `ProjectRegistry`, module, or
  function is refused with `agent_context_invalid`.
* `permissions` must equal what the policy actually grants (an
  informational field cannot carry a claim).
* `require_models()` / `require_tools()` raise `agent_model_unavailable` /
  `agent_tool_unavailable` when the service is absent.

**Project access (one approach).** `build_context(definition, task=,
projects=, router=None, tools=None, metadata=None)` resolves the task's
project through `ProjectRegistry.get` (unknown or archived → refused) and
gives the agent the narrow view. Agents never query SQLite, and the context
never holds a registry or connection.

**Not a sandbox.** This is an in-process Python boundary. It holds against
mistakes and against agent code that follows the rules the security tests
enforce on implementation modules: no forbidden imports, no private
attribute access (`context.models._router`), no `getattr`/`eval`/`exec`, no
`invoke`.

## 5. Agent result

`AgentResult(agent_id, task_id, status, output={}, error=None, metadata={})`
with `success` derived (`status == completed`).

| `AgentStatus` | Meaning | Rules |
|---|---|---|
| `completed` | output produced | no error; output checked against `output_schema` (unknown fields refused) |
| `failed` | could not | `error` is an `AgentError` |
| `waiting` | needs something first (input, approval) | no error |

Output is a JSON object of plain data (≤ 256 KiB, ≤ 8 levels, no NaN, no
objects). It is looser than the metadata policy because output is a product,
not configuration. `agent_id`/`task_id` may be `None` only on a failed
result whose request or agent could not be identified.

**AgentResult ≠ Task.** The statuses are a subset of the Phase 10 vocabulary
and local to one execution. Nothing applies a result to a task: a future
orchestrator decides whether it means `complete`, `fail`, `wait`, or
something else.

## 6. Agent errors

Typed, following the Model/Git/Tool error convention (`code`, fixed
redacted `message`, `agent_id`, `cause`, `retryable`, `details`, `to_dict`):

`agent_not_found`, `agent_disabled`, `agent_input_invalid`,
`agent_context_invalid`, `agent_permission_denied`,
`agent_model_unavailable`, `agent_tool_unavailable`,
`agent_execution_failed`, `agent_result_invalid`, plus
`agent_definition_invalid`, `agent_duplicate`,
`agent_implementation_not_found`.

An arbitrary exception from `run` becomes `agent_execution_failed` with
only the exception type name as `cause`: no traceback, message, path, or
credential. A `ModelError` becomes `agent_model_unavailable` with the model
error code as `cause` and its `retryable` kept.

## 7. Agent registry

In-memory and code-owned, like the Model and Tool registries; nothing is persisted.

* `register(definition)`: only exact `AgentDefinition` instances; the
  implementation ID must be in `KNOWN_IMPLEMENTATIONS`; duplicates are refused.
  The registry instantiates the allowlisted class itself.
* `get`, `exists`, `list` (sorted, disabled ones included).
* `invoke(agent_id, request, context) -> AgentResult` never raises for
  agent-level outcomes. Order (each step can only deny):

| # | Check | Error |
|---|---|---|
| 1 | not already inside an agent invocation (contextvar) | `agent_permission_denied` |
| 2 | agent registered | `agent_not_found` |
| 3 | agent enabled | `agent_disabled` |
| 4 | request is an `AgentRequest` | `agent_input_invalid` |
| 5 | context is an `AgentContext` for this agent | `agent_context_invalid` |
| 6 | `request.task_id == context.task_id` and `request.project_id == context.project_id` | `agent_context_invalid` |
| 7 | context task is `running` | `agent_context_invalid` |
| 8 | strict input validation | `agent_input_invalid` |
| 9 | stored instance is exactly the allowlisted class | `agent_implementation_not_found` |
| 10 | `run` | the agent's `AgentError`, or `agent_execution_failed` |
| 11 | result type, identity, output contract | `agent_result_invalid` |

No retry, persistence, event, or task change happens in `invoke`. Result
metadata adds `agent_version`, `project_id`, and `duration_ms`.

`build_agent_registry()` registers only `agent.echo`.

## 8. Capabilities

`AgentCapability`: `reasoning`, `text_generation`, `structured_analysis`,
`code_analysis`, `code_generation`, `testing`, `review`, `planning`. They
describe identity for future routing and display, and grant nothing.

## 9. Permissions

**Capability ≠ permission, and declared ≠ granted.** `tool_permissions` is
what an agent would ask for. `ToolAccess` builds the `ToolContext` itself
(actor type `agent`, actor ID = agent ID, the declared permissions, the
context's project), and `ToolRegistry` intersects that with the
`PermissionPolicy` ceiling. There is no second authorization system.
`DEFAULT_POLICY` grants `agent` **nothing**, so every agent tool call is
denied until a deployment deliberately grants a ceiling (tested both ways).

## 10. Model access

`ModelAccess.generate(ModelRequest) -> ModelResponse` is
`router.select(request).generate(request)`, the Phase 06 path, unchanged.

* An agent may not pin `model_id`: the router selects (`agent_permission_denied`).
* A request may need only capabilities declared in `model_requirements`
  (`agent_permission_denied`, missing ones listed in `details`).
* No vendor SDK, HTTP client, or provider object is reachable from agent
  code (static checks).

## 11. Tool access

`ToolAccess.invoke(tool_id, input) -> ToolResult` is exactly
`ToolRegistry.invoke` with the agent's context. Every Phase 09 step applies:
strict input (no `argv`/`cwd`/…), project scope (another project →
`tool_scope_denied`), permission ceiling, project existence and activity,
and the allowlisted implementation. The result is returned as is, so the
agent decides what a denial means for its own result.

## 12. Task relationship

An agent works on exactly one task of one project. The request and the
context must agree on both IDs, and the context's task must be `running`
(an orchestrator starts the task first). Agents read the `Task` snapshot
and never receive the `TaskEngine`. `invoke` never calls
`start`/`complete`/`fail`, never appends events, and never writes to the
database (checked with tripwires on `Database.transaction`,
`EventStore.append`, and every engine mutator).

## 13. Reference agent

`agent.echo` (`implementation = "reference.echo"`, `metadata.reference =
true`): input `{"message": "hello"}`, output `{"message": "hello"}`. It
declares no model requirements and no tool permissions, so its context has
neither. It is not AI, and it exists only to prove `AgentRegistry →
AgentContext → Agent.run() → AgentResult`. It appears in `siamang agents`
and in `validate` as the only, reference agent.

## 14. Security boundaries

Enforced by `tests/unit/test_agents_security.py`, `test_agents_context.py`,
`test_agents_registry.py`, and `test_agents_architecture.py`:

* **No callable injection.** Definitions hold an implementation ID; the
  allowlist maps IDs to SIAMANG-owned classes. Lambdas, functions, classes,
  instances, dicts, and definition subclasses are refused at `register`. A
  swapped-in instance (even a subclass) is refused at `invoke`. The
  allowlist is a read-only mapping.
* **No dynamic loading.** Agent sources contain no `importlib`, `__import__`,
  `getattr`/`setattr`, `eval`, `exec`, `compile`, `pkgutil`, or `inspect`. The
  registry has no load/discover/scan/plugin API.
* **No process, filesystem, network, raw Git, or raw database.** Agent
  sources import none of `subprocess`, `os`, `pathlib`, `socket`, `http`,
  `urllib`, `sqlite3`, `siamang.storage`, `siamang.git`, provider adapters,
  or LLM SDKs. Deliberate-break snippets prove the checks catch each case.
* **Runtime tripwires.** An `agent.echo` invocation (successful and failed)
  runs with tripwires on `subprocess`, `os.system/popen/scandir/listdir/getenv`,
  `socket.connect`, `GitClient.run`, `ToolRegistry.invoke`,
  `ModelRouter.select`, `Selection.generate`, `ModelProvider.generate`,
  `Database.transaction`, `EventStore.append`, and every `TaskEngine`
  mutator. None fire. A probe agent that calls `os.system` is caught.
* **No agent-to-agent calls.** The context carries no registry, and
  implementation modules may not import the registry or call `invoke`. At
  runtime a nested `invoke` from inside `run`, through the same or a
  different registry, is refused with `agent_permission_denied`.
* **No secrets out.** Error messages and details are redacted; exception
  text never reaches a result.

## 15. Why specialized agents are deferred

Each specialized agent needs decisions that belong to later phases: which
models suit which role (benchmarking), which tool ceiling each role gets,
how mutating tools are gated (Phase 19 approval), and how agents are
sequenced (Phase 12). Building them now would either hard-code those
decisions or invent a workflow inside the agents. Phase 11 fixes only the
contract they will implement.

## 16. Future Orchestrator relationship

```
TaskEngine.start(task) ─► build_context(definition, task=engine.get(id), projects, router, tools)
                       ─► AgentRegistry.invoke(agent_id, AgentRequest(...), context)
                       ─► orchestrator interprets AgentResult
                       ─► TaskEngine.complete / fail / wait (with TaskResult/TaskFailure details)
```

The orchestrator (Phase 12) chooses agents, sequences them, grants policy
ceilings, maps `AgentResult` to task transitions, and records execution as
task events. Agents stay single-step capabilities.

## Phase 11 limitations

* Only the reference `agent.echo` exists; no AI agent or role behaviour.
* Nothing calls `invoke` outside tests: no orchestrator, loop, or CLI `agents run`.
* The context boundary is in-process (convention + static checks), not a
  sandbox; a malicious implementation could reach private attributes. That
  is why implementations are allowlisted, code-owned classes.
* The context's `Task` is a snapshot. A task changed after `build_context`
  is not re-read; the orchestrator owns freshness.
* Agents get no tool access under `DEFAULT_POLICY`; granting a ceiling is a
  deployment decision for Phase 12+.
* `AgentRequest.input` and request metadata share the Task Engine's key
  rules, so a key literally named `path` or `args` is refused even when
  harmless.
* Agent output is capped at 256 KiB; larger artefacts need a later design.
* No execution history: invocations are logged, not persisted or emitted as events.
* The agent-to-agent guard is per thread/async context (contextvar); work
  handed to a new thread inside `run` is not covered (agents may not import
  `threading`).
