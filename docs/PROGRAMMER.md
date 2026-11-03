# Programmer (Phase 15)

**Status:** implemented (`src/siamang/programmer/`). Runtime is still
stdlib-only. No schema change (v3), no contract change (v0.2), no new
config, no events, no new tools, no vendor provider.

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Purpose

The Programmer answers: **given the Task, the Plan, and the System Analysis,
what code changes should be made?** It returns an immutable, bounded
`ProgrammingResult` holding a `Patch` of `ProposedChange`s. The patch is a
**proposal in memory**. Nothing is written or applied.

```
Planner        = WHAT work is required
System Analyst = WHAT the system currently contains
Programmer     = HOW the change should be implemented      (this phase: proposals only)
Approval       = MAY the change be applied?                (future, Phase 19)
Workspace      = APPLY the approved change                 (future)
QA             = DOES the applied change work?             (future, Phase 16)
```

```
                 TASK
                   │
                   ▼
                 PLAN
                   │
                   ▼
            SYSTEM ANALYSIS
                   │
                   ▼
              PROGRAMMER
                   │
             ┌─────┴─────┐
             ▼           ▼
 ProgrammerModelAccess  Constraints / ProgrammingPolicy
             │
             ▼
         ModelRouter
             │
             ▼
        ModelProvider
             │
             ▼
      Proposed Changes (Patch)
             │
             X   <-- not applied in Phase 15
             │
             ▼
       [future Approval]
             │
             ▼
      [future Workspace]
```

## 2. Responsibilities

* Validate a `ProgrammingRequest` that carries a validated `Plan` and
  `SystemAnalysis` as data.
* Read the task through `TaskEngine.get`: it must exist, belong to the
  project, and not be final (`completed`/`cancelled`).
* Resolve the project through `ProjectRegistry`: it must exist and be
  active. Its registered `root_path` is the only filesystem authority.
* Re-validate the Plan and the SystemAnalysis and check that both are for
  this task and project.
* Build a bounded, scrubbed model context and ask a router-selected model
  **once** through `ProgrammerModelAccess`.
* Parse the reply strictly, check every change against the plan, the path
  rules, the secret rules, and a read-only snapshot of the project, and
  return a validated result.

## 3. Non-responsibilities

The Programmer does **not**:

* write, create, modify, delete, or rename files or directories, or apply a
  patch;
* run Git (no add, commit, checkout, reset, merge, push, pull, fetch, or
  worktree), and does not even read Git state;
* run commands, shells, tests, servers, or installers, or execute, import,
  or evaluate generated code;
* call the Task Engine's mutators, the Orchestrator, agents, tools, the
  Planner, the System Analyst service, QA, a Reviewer, or an Approval
  System;
* retry, fall back to another model, self-correct, or loop;
* persist results or emit events;
* act as an Agent (there is no `agent.programmer`).

## 4. Architecture

| Module | Role |
|---|---|
| `errors.py` | `ProgrammingError` + 16 codes (`programmer_*`) |
| `safety.py` | path rules, forbidden files, secret detection, text checks, hashing, deep freeze |
| `request.py` | `ProgrammingRequest` |
| `change.py` | `ProposedChange`, `Operation`, `BeforeState` |
| `patch.py` | `Patch` (self-consistent change set) |
| `result.py` | `ProgrammingResult`, `ProgrammingStatus` |
| `policy.py` | `ProgrammingPolicy`, `DEFAULT_POLICY` |
| `access.py` | `ProgrammerModelAccess`: the only path to a model |
| `snapshot.py` | `ProjectSnapshot`: read-only before-state and context files, via `ProjectInspector` |
| `context.py` | `ProgrammingContext`, `build_context`: bounded, scrubbed model input |
| `strategy.py` | `ProgrammingStrategy` (ABC), `ModelStrategy`, `parse_output`, `KNOWN_STRATEGIES` |
| `service.py` | `Programmer.program` |

Dependency direction (enforced by `tests/unit/test_programmer_architecture.py`
and `tests/unit/test_programmer_security.py`):

```
cli ──► programmer ──► tasks (definition, errors, lifecycle; TaskEngine type, .get only)
                    ├─► projects, ids, log
                    ├─► planner.plan / planner.step (Plan as data), planner.request (validation helpers)
                    ├─► analyst.analysis (SystemAnalysis as data), analyst.request (key rules),
                    │   analyst.inspection + analyst.policy + analyst.errors (ProjectInspector, read-only)
                    └─► models.router / request / response / errors / capabilities  (access.py, strategy.py)
programmer ─✗─► agents, orchestrator, tools, storage, bus, config, contracts, git,
                planner.service, analyst.service, analyst.strategy, analyst.git_state,
                models.provider, models.providers, models.factory, models.registry
everything except cli ─✗─► programmer
```

## 5. ProgrammingRequest

`ProgrammingRequest(task_id, project_id, instruction, plan, analysis,
input={}, constraints=(), metadata={})`, frozen.

* `plan` and `analysis` are required and must be **exact** `Plan` and
  `SystemAnalysis` objects (subclasses, dicts, and `None` are refused):
  immutable snapshots, never live services. `from_dict` never parses them
  from data.
* Validation reuses the Analyst, Planner, and Task Engine helpers. At any
  depth of `input`/`metadata` it refuses command, argv, shell, cwd, path,
  credential, agent, tool, model, provider, retry, workflow, callback,
  executor, Git-mutation, and file-selection keys, plus the programmer's
  own: `process`, `subprocess`, `orchestrator`, `approval`, `approve`,
  `apply`, `filesystem`, `write_file`, `delete_file`, `rename_file`,
  `move_file`, `apply_patch`, `chmod`, `symlink`, `exec`, `eval`, ...
* `input`/`metadata` are frozen at every depth.
* The instruction is opaque data.

## 6. Plan input

The Programmer never calls the Planner. It rebuilds the given Plan (and each
step) so that every Plan invariant runs again. A plan altered after
construction is caught here and is **never repaired**. The plan must have
the same `task_id` and `project_id` as the task the Task Engine returned,
and at least one step. Every proposed change names a `step_id` from this
plan. An unknown step makes the model output invalid.

## 7. Analysis input

The SystemAnalysis is rebuilt the same way (findings and components
included) and must be for the same task and project. It is never mutated.
Its semantics are preserved:

* every analysis **unknown** is carried into the result as
  `From analysis: ...`, so it stays an unknown;
* if any finding or component is **inferred**, the result warns that changes
  relying on it rest on assumptions;
* if the analysis recorded a `plan_content_hash` different from the
  supplied plan, the result warns about it;
* the model is told that `inferred`/`unknown` items are not facts and must
  surface reliance on them as warnings or unknowns.

## 8. Model boundary

```
Programmer ──► ProgrammerModelAccess ──► ModelRouter.select ──► Selection.generate ──► ModelProvider
```

The Phase 11 `agents.ModelAccess` is bound to an `AgentDefinition`, and the
Programmer is deliberately not an Agent. `ProgrammerModelAccess` is the
equivalent narrow facade:

* the router selects: a request may not pin `model_id`, and no provider, SDK,
  HTTP client, or vendor name is reachable;
* only `text_generation` and `structured_output` may be required (no tool
  calling, vision, or streaming);
* **single use**: one instance allows one generation, and the flag is set
  *before* the call, so a failure is no licence to call again. The service
  builds a fresh instance per `program()` call: **one invocation = at most
  one model generation**, with no hidden repair, reflection, validation, or
  retry call;
* `ModelError`s are normalized: `timeout` → `programmer_model_timeout`;
  `authentication_error`, `invalid_request`, `context_limit`,
  `unsupported_capability` → `programmer_model_rejected`; everything else →
  `programmer_model_unavailable`. `cause` holds the model error code only.

`ModelStrategy` (`programming.model`) prefers structured output: if an
enabled model offers `structured_output`, it asks for JSON with a schema,
and otherwise for text that must contain the same JSON. Temperature is 0;
the timeout comes from the policy.

## 9. Proposed changes

`ProposedChange(change_id, path, operation, step_id, reason, after_content,
before, before_hash, evidence, metadata)`; `after_hash` is computed, never
passed in.

* `operation`: `create`, `modify`, or `delete`. There is no rename, move,
  chmod, symlink, binary patch, submodule, or Git operation.
* `after_content`: the complete proposed text for create/modify, `None` for
  delete. UTF-8 text only: NUL and other control characters (except tab,
  LF, CR, FF) are refused, as is lone-surrogate text. A modification must
  change the content.
* `before`:
  - `absent`: verified not to exist (create only);
  - `observed`: read through the inspector; `before_hash` is the SHA-256 of
    the file's exact bytes;
  - `unverified`: not established (larger than the read limit, binary, not
    valid UTF-8, budget spent, or, for create, absence not confirmed).
    `before_hash` is `None`: **a hash is never invented**, and the result
    lists an unknown such as "current state not verified; re-check before
    applying".
* The snapshot also refuses outright: a path through a symlink or a file, a
  directory or special file as the target, and a name that differs from an
  existing entry only by case.
* `reason` and `evidence` say why the file changes, which requirement it
  satisfies, and what supports it. Evidence is short, single-line,
  project-relative, redacted, and credential-free.

## 10. Patch representation

`Patch(patch_id, task_id, project_id, summary, changes)`, frozen.

* At most 50 changes (hard cap), unique change IDs.
* **One operation per path.** The same path twice is a conflict
  (create+create, modify+modify, delete+modify, create+delete, ...), and so
  are paths that differ only by case.
* A path that is a directory prefix of another changed path (`src` and
  `src/app.py`) is refused.
* Total proposed content ≤ 4 MiB (hard cap; the policy default is 1 MiB).

`ProgrammingResult(programming_id, task_id, project_id, status, summary,
patch, warnings, unknowns, error, metadata)`:

| status | patch | error | other |
|---|---|---|---|
| `completed` | non-empty | none | |
| `waiting` | none | none | at least one unknown (the model could not responsibly propose code) |
| `failed` | none | programmer error dict | |

`metadata` records the strategy and version, `plan_content_hash`,
`analysis_content_hash`, `context_files`, `withheld_files`, `model_calls`
(0 or 1), `model_used`, `provider_used`, `response_format`, and inspector
`usage`. It contains no request ID, timestamp, or absolute path.

## 11. Path security

Every path, whatever its source (model, caller, test), must be a
project-relative POSIX path of plain segments. Refused:

* absolute paths (`/etc/...`, `/Users/...`), `~` anywhere, Windows drive
  paths (`C:`), UNC and backslash paths;
* `.`/`..`/empty segments and `%` (no encoded traversal);
* control and format characters (NUL, newlines, RTL overrides), non-NFC
  text, segments over 255 characters, and paths over 1024 characters.

**Forbidden files** are never proposed for any operation: anything under
`.git/`, `.hg/`, `.svn/`, `.ssh/`, `.gnupg/`, or `.aws/` (case-insensitive),
and every name the analyst treats as secret-looking: `.env*`, `*.env`,
`.envrc`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, `credential(s).*`,
`secret(s).*`, `.netrc`, `.npmrc`, `service-account*.json`, and so on.
Results never contain absolute project paths.

## 12. Secret handling

* `find_secret` reports the **kind** of credential material, never the
  value: private-key blocks; token shapes (`sk-...`, `AKIA...`, `ghp_...`,
  `glpat-...`, `xox?-...`, `AIza...`); credential-named keys (`API_KEY`,
  `SECRET`, `PASSWORD`, `TOKEN`, `client_secret`, `private_key`, ...)
  assigned a quoted literal of 8 or more characters, or an env-file style
  `KEY=value` at line start. Placeholders (`${...}`, `<...>`, `changeme`,
  `your-...`, `example`, `test`, ...) and environment lookups are not
  secrets. Ordinary code such as `password = request.form["password"]` or
  `tokenizer = "bert-base-uncased"` passes. It is defensive detection, not a
  guarantee, and it runs in linear time.
* A change whose content, reason, or evidence contains credential material
  is refused, as is a result whose summary, warnings, or unknowns do.
* **Model context:** secret-looking files are never opened (inspector
  rule). Context files containing credential material or machine paths are
  withheld from the model and reported as unknowns. All other strings have
  the project root and home-directory paths replaced by placeholders, have
  credential-looking text withheld, and then pass through the shared
  redactor. The context never contains `root_path`.
* Logs carry only task, project, and programming IDs, the status, the
  number of changes, and error codes. They never carry the instruction,
  file contents, or model text.

## 13. Model output validation

Model output is **untrusted data**. `parse_output`:

1. refuses a `length` (truncated) or `content_filter` reply;
2. bounds the raw reply (`max_model_response`) before parsing;
3. accepts exactly one JSON object (one surrounding Markdown code fence is
   removed). Invalid JSON, a list, deep nesting, or huge numbers give
   `programmer_model_invalid_output`, never an exception;
4. requires exactly the keys `status`, `summary`, `changes`, `warnings`,
   `unknowns`. Each change has exactly `path`, `operation`, `step_id`,
   `reason`, and optional `content` and `evidence`. **Any other key**
   (`command`, `shell`, `apply`, `before_hash`, ...) invalidates the whole
   reply;
5. checks types, `completed` ⇒ at least one change, and `waiting` ⇒ none.

The service then builds each `ProposedChange` (path, forbidden-file,
secret, text, step, and snapshot checks), the `Patch` (conflicts and
bounds), and the policy limits. Any failure gives a `failed` result. A
partial patch is never returned. Nothing in a reply is executed, imported,
evaluated, or used as a filesystem path outside these checks. A reply such
as `{"command": "rm -rf /"}` is simply invalid output.

## 14. Resource limits

`ProgrammingPolicy` (defaults; hard cap in parentheses). No environment or
config override exists; out-of-range values, booleans, and floats are
refused.

| Limit | Default (cap) | When exceeded |
|---|---|---|
| `max_changes` | 20 (50) | invalid output |
| `max_file_size` | 256 KiB (1 MiB) | `programmer_policy_denied` |
| `max_total_output` | 1 MiB (4 MiB) | `programmer_policy_denied` |
| `max_summary` | 2000 (4000) | `programmer_policy_denied` |
| `max_warnings` / `max_unknowns` | 50 / 50 (100 / 100) | model's own: denied; merged notes: capped with an "omitted" note |
| `max_instruction_length` | 4000 (10 000) | refused before any work |
| `max_context_files` | 8 (32) | further files withheld (unknown) |
| `max_context_file_size` | 32 KiB (256 KiB) | file withheld (unknown) |
| `max_context_bytes` | 256 KiB (1 MiB) | files dropped from the end; if still too large, refused |
| `max_model_response` | 2 MiB (8 MiB) | invalid output |
| `model_timeout_secs` | 120 (600) | passed to the adapter, which enforces it |

Programmable task statuses default to every non-final status; `completed`
and `cancelled` can never be allowed.

## 15. Deterministic hashes

* `ProposedChange.after_hash` and `before_hash`: `sha256:` of exact UTF-8
  bytes.
* `Patch.content_hash`: canonical JSON of everything except `patch_id`.
* `ProgrammingResult.content_hash`: everything except `programming_id` and
  the `patch_id`.

The same input, project snapshot, and generated content give the same hash.
Different proposed content gives a different hash. `programming_id`
(`prg_<ULID>`) and `patch_id` (`pch_<ULID>`) are identity only. The hashes
are change fingerprints, not a security mechanism.

## 16. No-write boundary

* The Programmer has no write API. No programmer module imports `os`,
  `pathlib`, `shutil`, `subprocess`, `socket`, `sqlite3`, `importlib`, or
  the storage, bus, Git, tool, agent, or orchestrator layers, and none uses
  `open`, `eval`, `exec`, `compile`, `getattr`, or `__import__`. A static
  test enforces this, with deliberate-break proofs.
* File reads go only through the Phase 14 `ProjectInspector`: registered
  root, relative paths, symlinks never followed, secret files never opened,
  budgeted.
* Runtime tripwires: with every write-mode `open`, `os.remove`/`rename`/
  `mkdir`/`rmdir`/..., `Path.write_text`/`write_bytes`/`mkdir`/..., `shutil`,
  `subprocess`, sockets, `eval`/`exec`/`compile`, `importlib`, `GitClient.run`,
  `ToolRegistry.invoke`, `AgentRegistry.invoke`, `Orchestrator.execute`,
  `Planner.plan`, `SystemAnalyst.analyze`, `EventBus.publish`, the event
  store, and every Task Engine mutator set to explode, the Programmer still
  produces an in-memory patch, and the project tree is byte-identical
  afterwards. In a Git repository, status, HEAD, branches, stash,
  worktrees, and the `.git` directory are unchanged.
* No `workspace.write`, `file.write`, `patch.apply`, `git.commit`,
  `git.branch`, or `worktree.create` tool exists.

## 17. Future Approval System

A `Patch` is designed to be judged later: one operation per path, an
explicit before-state, `before_hash` for observed files (so "the project
changed after this proposal" is detectable), `after_hash`, step IDs,
reasons, evidence, and the plan and analysis hashes in the result metadata.
Phase 19 decides **whether** a patch may be applied, and a separate,
controlled mutation layer applies it. Neither exists yet.

## 18. Future QA and Agent wrapper

* **QA (Phase 16, [QA.md](QA.md))** statically verifies a proposed patch
  (recorded hash, Phase 15 re-validation, plan, analysis, and project
  consistency) without applying or running it. The QA ↔ Programmer loop
  (Phase 18) belongs to orchestration, not to this service. The Programmer
  never retries and never imports QA.
* **Agent wrapper:** a future phase may wrap the Programmer as an Agent and
  let the Orchestrator chain Planner → Analyst → Programmer. Phase 15 adds
  neither, and the Orchestrator does not import the Programmer.

## 19. Limitations

* No workspace mutation, no patch application, no QA, no approval, no
  retry, no autonomous loop, no persistent patch history, no Agent
  wrapper, no Orchestrator integration. This is intentional.
* The model sees at most a few small files, chosen only from paths the
  analysis cited. A `modify` of a file the model was not shown is possible;
  the model is told to warn in that case, and the before-hash still pins
  what was there.
* Secret detection is pattern-based. It catches common formats, not every
  possible credential.
* The snapshot is best effort, with no locks: the project can change between
  proposal and approval. `before_hash` exists to detect exactly that.
* A file containing U+FFFD, or one that is not valid UTF-8, is
  `unverified`.
* No real model adapter ships with Siamang. Tests use a fake provider, and
  the `echo` adapter cannot produce a valid patch.
* Results live in memory only. The CLI shows only the policy; there is no
  command that programs or writes.

## Phase 15 decisions

IDs use `PR` because `P` and `PL` are already taken.

| # | Decision |
|---|---|
| PR1 | The Programmer produces proposed changes, not workspace mutations |
| PR2 | Plan and SystemAnalysis are immutable inputs, re-validated and never repaired |
| PR3 | Model access goes through `ProgrammerModelAccess`, the Programmer's equivalent of `ModelAccess` |
| PR4 | Provider selection remains the ModelRouter's responsibility; no model pinning |
| PR5 | Model output is untrusted data: strictly parsed, exact keys, fully validated |
| PR6 | Every change is project-relative, never a forbidden file, and checked against a read-only snapshot |
| PR7 | Every change and patch is bounded by `ProgrammingPolicy` under hard caps |
| PR8 | Patch operations are validated: create/modify/delete only, one operation per path |
| PR9 | The Programmer does not execute generated code |
| PR10 | The Programmer does not invoke QA |
| PR11 | The Programmer does not invoke Approval |
| PR12 | The Programmer does not mutate the Task |
| PR13 | The Programmer does not mutate Git (and does not read it) |
| PR14 | No persistence and no events (schema v3, contract v0.2) |
| PR15 | The Programmer remains separate from the Agent abstraction |
| PR16 | One `program()` call makes at most one model generation; no retry or repair call |
| PR17 | `before_hash` only for observed files; otherwise `unverified`, never an invented hash |
| PR18 | Read-only CLI `siamang programmer [--json]` plus a `Programmer` check in `validate`; no run/apply/write |
