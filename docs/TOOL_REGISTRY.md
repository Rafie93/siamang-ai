# Tool Registry + Permission Boundary (Phase 09)

**Status:** implemented (`src/siamang/tools/`). Runtime is still stdlib-only.

The Tool Registry is the single, auditable boundary through which future
tasks and agents will discover and invoke tools. It **declares** tools,
**authorizes** calls (deny by default), **scopes** them to a registered,
active project, and **invokes** allowlisted, SIAMANG-owned implementations.

> **This registry does not run tasks, agents, an orchestrator, or a planner.**
> **This registry does not let a model choose tools or generate tool arguments.**
> **This registry holds no mutating tool and no shell, process, network, or filesystem tool.**

The registry answers *what tools exist, what they need, and who may call
them*. It never decides *which* tool to use or *what* to do next; those
decisions belong to Phases 10–13.

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Architecture

```
(future) Task Engine / agent / orchestrator
        │  invoke(tool_id, ToolContext, input)
        ▼
ToolRegistry  (tools/registry.py) ── PermissionPolicy (tools/permissions.py)
        │                          ── ProjectRegistry   (project exists, active, in scope)
        ▼
ImplementationTable (tools/implementations.py): allowlisted adapter methods only
        ▼
GitToolAdapter (tools/git_tools.py)
        ▼
GitWorkspaceManager ──► GitClient ──► git      (Phase 08, unchanged)
```

| Module | Role |
|---|---|
| `tools/errors.py` | `ToolError` + 14 typed codes |
| `tools/permissions.py` | `Permission`, `ActorType`, `ToolContext`, `PermissionPolicy`, `DEFAULT_POLICY` |
| `tools/definition.py` | `ToolDefinition` (immutable data), definition and strict input validation |
| `tools/implementations.py` | `KNOWN_IMPLEMENTATIONS` allowlist, `ImplementationTable` |
| `tools/git_tools.py` | the five Git tool definitions, plus `GitToolAdapter` |
| `tools/registry.py` | `ToolRegistry` (catalog, discovery, `invoke`), `ToolResult`, `build_tool_registry` |

Dependency direction (enforced by `tests/unit/test_tools_architecture.py`):

* `tools` may import only `projects`, `git` (in the adapter and allowlist
  modules only), `contracts.schema`, and `log`.
* `tools` never imports `storage` (projects are resolved through
  `ProjectRegistry`), `models`, `bus`, `cli`, `config`, or
  `git.client`.
* `projects`, `git`, `storage`, `models`, `bus`, and `contracts` never import
  `tools`. Only the CLI does.
* Future layers (`agents`, `planner`, `orchestrator`, `tasks`, ...) sit
  **above** `tools`. `tools` must not import them.

## 2. Tool definition

`ToolDefinition` is a frozen dataclass. Its schemas and metadata are
read-only mappings and its capabilities a `frozenset`. It is validated when
built:

| Field | Rule |
|---|---|
| `tool_id` | `namespace.name[.name…]`, lowercase, at most 4 segments. Segments such as `run`, `exec`, `command`, `shell`, `raw`, `eval`, `spawn`, `system`, `script` are refused, so `git.run`, `git.exec`, `git.command`, `git.shell` cannot exist |
| `name` / `description` | 1–80 / 1–500 characters |
| `version` | `1`, `1.2`, or `1.2.3` |
| `category` | must equal the `tool_id` namespace |
| `operation` / `mutating` | `read`/`write`; `mutating` must equal `operation == write` |
| `implementation` | an implementation **ID string** (never a callable) |
| `capabilities` | permissions required besides `tool.invoke` |
| `requires_project` / `requires_workspace` | a workspace implies a project |
| `input_schema` | flat, strict (§4) |
| `output_schema` | non-empty; checked against every result |
| `enabled` | disabled tools are listed but cannot be invoked |
| `metadata` | plain JSON only (a callable is refused) |

`to_dict()` is the public description (CLI). It leaves out the
implementation binding.

## 3. Registry

In-memory and code-owned. `build_tool_registry(projects=..., git=...)`
registers the five Git tools. Nothing is persisted: no definitions,
permissions, or invocation history (no schema change; schema stays **v2**).

`register(definition)` refuses, in Phase 09:

* anything that is not a `ToolDefinition`;
* a mutating tool, or capabilities `workspace.write`, `git.write`,
  `network`, `process.execute`, `filesystem.write` (`check_read_only`);
* an implementation ID not in `KNOWN_IMPLEMENTATIONS`;
* a duplicate `tool_id` (`tool_duplicate`).

### Implementation allowlist (no callable injection)

The registry is **not** a `string → function` map.

* `KNOWN_IMPLEMENTATIONS` (a read-only mapping) fixes each implementation ID
  to `(adapter class, method name)`: `git.inspect`, `git.status`,
  `git.diff`, `git.diff_cached`, `git.worktrees` → `GitToolAdapter`.
* `ImplementationTable` accepts a binding only if it is a **bound method of
  exactly that class** whose function is that class's own method for that
  ID. Lambdas, builtins (`print`, `subprocess.run`, `os.system`), unbound
  functions, subclass overrides, another method of the same adapter, and
  strings are refused. Unknown IDs (`git.push`, `git.run`) are refused.
* `get()` checks the binding again at use, so a swapped-in callable is never
  returned.
* `ImplementationTable.build(git=manager)` is the only constructor the
  application uses.

## 4. Input and output contracts

Schemas reuse the contract mini-schema (`siamang.contracts.schema.F`). On
top of it, tool inputs are **strict**:

* The input must be an object with string keys. Unknown fields are refused.
* Input fields may only be scalar kinds: `str`, `text`, `int`, `number`,
  `bool`, `enum`, `id`. No `object`, `list`, or `any`, so no free-form
  blobs.
* These fields can **never be declared** and are refused with a dedicated
  message: `command(s)`, `cmd`, `argv`, `args`, `arguments`, `shell`,
  `shell_command`, `git_args`, `script`, `python`, `callable`, `function`,
  `exec`, `executable`, `binary`, `program`, `env`, `environment`, **`path`,
  `paths`, `root_path`, `cwd`, `directory`, `repository_path`**, `pathspec`,
  `url`. A tool can never be given a path that bypasses the Project
  Registry, or arguments that turn it into a generic executor.
* `project_id` must fully match `prj_[A-Za-z0-9][A-Za-z0-9_.:-]{0,123}`
  (`fullmatch`, so a trailing newline, shell syntax, `../`, `/`, and
  spaces are rejected).
* The validated input is a **copy**. The adapter receives it as a read-only
  mapping.

Each result is checked against `output_schema` before it is returned
(`tool_result_invalid`).

## 5. Permission model

```python
ToolContext(actor_type="system", actor_id="cli", permissions={...}, project_id="prj_...")
```

| Permission | Meaning | Granted in Phase 09 |
|---|---|---|
| `tool.list`, `tool.inspect` | discovery | system, user |
| `tool.invoke` | required for every invocation | system, user |
| `project.read`, `workspace.read`, `git.read` | what the Git tools need | system, user |
| `workspace.write`, `git.write`, `network`, `process.execute`, `filesystem.write` | write/side effects | **nobody**; no tool may even declare them |
| `filesystem.read` | reserved | nobody |

* **Effective permissions = context permissions ∩ the policy ceiling for
  the actor type.** A caller cannot grant itself anything. Asking for every
  permission yields only what the ceiling allows.
* `DEFAULT_POLICY` is code-owned: `system` and `user` get the read-only
  set; **`agent` and `task` get nothing**. Agents and tasks do not exist yet.
  Phase 10/11 will grant them explicitly by changing the policy, under the
  same mechanism. There is no `if agent: allow()` anywhere. A custom
  `PermissionPolicy` applies to every actor type the same way (tested).
* An empty requirement is refused, not treated as a silent allow.
* `ToolContext` is validated: known actor type, safe `actor_id`, known
  permission names, and `project_id` either `None` or a valid project ID.
* There is no authentication, user database, roles, or tokens. This is the
  domain boundary only.

`GitClient` uses `subprocess` internally. That is an implementation detail
below the boundary. No tool requires, and no caller receives,
`process.execute`.

## 6. Invocation boundary

`ToolRegistry.invoke(tool_id, context, input) -> ToolResult` is the only
way to run a tool. Each step can only deny:

| # | Check | Denial code |
|---|---|---|
| 1 | tool exists | `tool_not_found` |
| 2 | tool enabled | `tool_disabled` |
| 3 | context is a `ToolContext` | `tool_context_invalid` |
| 4 | `project_id` present (project tools) | `tool_project_required` |
| 4 | strict input validation | `tool_input_invalid` |
| 5 | `context.project_id == input.project_id` | `tool_scope_denied` |
| 6 | permissions (ceiling ∩ context ⊇ `tool.invoke` + capabilities) | `tool_permission_denied` (the missing permissions are listed in `details`) |
| 7 | project exists via `ProjectRegistry` | `tool_project_not_found` |
| 7 | project is active | `tool_project_archived` |
| 8 | allowlisted implementation is bound | `tool_implementation_not_found` |
| 9 | execute | `tool_execution_failed` (`cause` = lower-layer code), or `tool_scope_denied` for a protected repository |
| 10 | result matches `output_schema` | `tool_result_invalid` |

Permissions (step 6) are checked **before** the project lookup (step 7),
which differs from the order in the Phase 09 brief. A caller without rights
gets `tool_permission_denied` even for a nonexistent project, so it cannot
probe which projects exist.

`invoke` returns a `ToolResult` for every outcome. It never raises for
these:

```python
ToolResult(tool_id, success, data, error, metadata)
# metadata: actor ("system:cli"), project_id, tool_version, duration_ms
```

`ToolError.to_dict()` = `{code, message, tool_id, cause, retryable,
details}`. Messages are fixed text. They never contain tracebacks, command
lines, environment, paths, or input values. An unexpected exception inside
an implementation becomes `tool_execution_failed`, with `cause` set to only
the exception's type name. Errors are re-raised outside `except`, so nothing
is chained.

Logs (`component=tools`) carry the tool ID, the actor, and the outcome code
or duration. They never include inputs or paths.

`list_tools(context)` needs `tool.list`; `get_tool(tool_id, context)` needs
`tool.inspect`.

## 7. Project scoping

* Project tools take `project_id` only. `git.status(path="/Users/...")` is
  impossible: `path` cannot be declared, and unknown fields are refused.
* The registry resolves the project through **`ProjectRegistry.get`**
  (never SQL). It refuses unknown projects and **archived** projects,
  without restoring or modifying them.
* The caller's `ToolContext.project_id` must equal the input's
  `project_id`. A context with no project scope cannot use project tools.
* The adapter receives the already-resolved `Project` and passes it to
  `GitWorkspaceManager` (no second lookup, no path).
* The protected repository guard (Phase 08) still applies. It surfaces
  as `tool_scope_denied`, with `cause=protected_repository`.

## 8. Registered tools

All five: `category=git`, `operation=read`, `mutating=false`,
`enabled=true`, `requires_project=true`, `requires_workspace=true`,
`metadata.network=false`, capabilities `project.read` + `workspace.read` +
`git.read` (plus `tool.invoke`).

| Tool | Input | Calls | Output |
|---|---|---|---|
| `git.inspect` | `project_id` | `GitWorkspaceManager.inspect` | location, head, status, remotes (credentials removed) |
| `git.status` | `project_id` | `.status` | clean, head, upstream/ahead/behind, counts, entries |
| `git.diff` | `project_id`, `cached?: bool` (default false) | `.diff(cached=…)` | cached, files, patch, truncated |
| `git.diff_cached` | `project_id` | `.diff(cached=True)` | same as `git.diff` |
| `git.worktrees` | `project_id` | `.worktrees` | `{worktrees: [...]}` |

`GitToolAdapter` has exactly these public methods plus `bindings()`. It
requires a `GitWorkspaceManager`, so a `GitClient` cannot be passed in.
`GitClient.run(args)` is never reachable from the tool layer.

## 9. Explicitly unavailable

No tool, implementation, or permission grant exists for: `git.run`,
`git.exec`, `git.command`, `git.shell`, push, pull, fetch, clone, merge,
rebase, reset, clean, stash, commit, branch creation, worktree creation, any
shell or arbitrary process, any arbitrary filesystem read/write, any network
access, or arbitrary Python callables. There is no `siamang tools run`.
Adding any of these is a future, explicit change. It requires a new
allowlisted implementation **and** a policy change, and Phase 09's
`check_read_only` would have to be lifted deliberately.

## 10. CLI

```
siamang tools [list] [--json]        # catalog; never runs a tool
siamang tools get <tool_id> [--json] # one definition (no implementation internals)
```

Listing builds the catalog only. It does not open the database, start a
process, or invoke anything (tested with `subprocess.run` patched to fail).
`siamang validate` includes a `Tools` check: the catalog builds and nothing
in it is mutating.

## 11. Security tests

`test_tools_security.py`, `test_tools_definition.py`,
`test_tools_registry.py`, `test_tools_architecture.py`:

* AST rules over `tools/`. Forbidden imports: `subprocess`, `os`,
  `pathlib`, `shutil`, `glob`, `socket`, `http`, `urllib`, `importlib`,
  `pickle`, `ctypes`, `threading`, `asyncio`, `dotenv`, Git libraries,
  `siamang.git.client`, `siamang.storage`. Forbidden names: `getattr`,
  `eval`, `exec`, `compile`, `__import__`, `open`, `os.system`/`popen`,
  `exec*`/`spawn*`/`fork`, `environ`/`getenv`, `listdir`/`scandir`/`walk`/
  `iterdir`/`glob`/`rglob`, `GitClient`. No `.run(...)` calls.
* **Deliberate breaks**: each rule is fed mutated source
  (`import subprocess`, `getattr(self._manager, name)`, `os.system`,
  `GitClient().run([...])`, `open('.env')`, ...) and must flag it.
  Mutating the real source to a permissive policy fallback makes 5 tests
  fail; adding a `getattr` dispatch to the adapter makes 1 fail.
* Callable injection is refused (§3).
* Runtime: every argv that reaches Git while all five tools run is an
  allowlisted read command with `protocol.allow=never` and a clean
  environment, with no push/pull/fetch/clone/ls-remote/submodule/commit/
  reset.
* Directory listing, `os.system`/`popen`/`exec*`/`spawn*`/`fork`, and any
  open inside the project are patched to fail, and all tools still work.
  A `.env` secret, a token in the environment, and a token in a remote URL
  never reach results or logs.
* Shell syntax in `project_id`, `command`, `argv`, `path`, or `cached`
  never executes (marker file test). The autouse socket block keeps
  everything offline.

## 12. Boundary for the Task Engine (Phase 10) and later

* Phase 10 calls `ToolRegistry.invoke` with a `ToolContext` of actor type
  `task` (and `task_id` as `actor_id`). It must first **grant** `task` a
  ceiling in the policy. No code path special-cases tasks or agents.
* Tool results are JSON-ready (`ToolResult.to_dict()`). Phase 10 decides
  whether and how invocations are persisted or emitted as task events.
  Phase 09 emits no events and adds nothing to the contract.
* Mutating tools (worktree add, branch, commit) need a new allowlisted
  implementation, new capabilities, removal of the read-only rule, and
  Phase 19 approval in front of them.
* LLM tool calling (Phase 11+) must go through the same `invoke`. A model
  may produce a tool ID and input, but both are validated exactly like any
  other caller's.
