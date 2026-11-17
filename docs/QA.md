# QA (Phase 16)

**Status:** implemented (`src/siamang/qa/`). Runtime is still stdlib-only.
No schema change (v3), no contract change (v0.2), no new config, events,
tools, or model use.

> **Phase 16 QA verifies a Proposed Patch and produces a QAResult. It does
> not modify the project, execute project code, invoke Programmer, perform
> retries, review the result, or approve the change.**

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Purpose

QA answers: **does this proposed change pass the defined technical,
security, and structural checks?** It takes one Phase 15 `Patch`, plus the
Task, Plan, and SystemAnalysis it was made for, and returns an immutable,
bounded `QAResult`.

```
Task
  ├── Plan
  ├── SystemAnalysis
  └── Programmer ──► ProposedPatch
                          │
                          ▼
                         QA  ──► deterministic checks ──► QAResult
                                                              │
                                                              X   nothing proceeds automatically
                                                              │
                                                     (future) Phase 17 Reviewer
```

| Layer | Question |
|---|---|
| Programmer (15) | HOW should the change be implemented? (proposal) |
| **QA (16)** | Does the proposal pass the defined static checks? |
| Reviewer (17, future) | Is the result acceptable at a higher level? |
| Loop (18, future) | Should the Programmer try again after a QA failure? |
| Approval (19, future) | MAY the change be applied? |

`pass` ≠ approved; `fail` ≠ rejected.

## 2. Inputs

`QARequest(task_id, project_id, plan, analysis, patch, patch_hash,
metadata={})`, frozen.

* `plan`, `analysis`, and `patch` must be **exact** `Plan`,
  `SystemAnalysis`, and `Patch` objects: immutable data snapshots, never
  live services, and no subclasses.
* `patch_hash` is the `Patch.content_hash` the caller recorded when the
  Programmer produced the patch. QA recomputes it: an altered or forged
  patch never passes, even when the forgery is internally consistent.
* `metadata` refuses (at any depth) the Programmer's execution, path,
  credential, agent, tool, model, Git, write, and approval keys, plus
  `fix`, `repair`, `retry`, `regenerate`, `loop`, `review`, `approve`,
  `reject`, `run_tests`, `pytest`, `execute`, `sandbox`, ... It is frozen at
  every depth.

Validation before any check (each refusal raises a `QAError`):

| Step | Refusal |
|---|---|
| request is a `QARequest` | `qa_request_invalid` |
| task exists (`TaskEngine.get`, read only) | `qa_task_not_found` |
| task belongs to `project_id` | `qa_request_invalid` |
| task not final (`completed`/`cancelled`) | `qa_policy_denied` |
| project registered and active (`ProjectRegistry`) | `qa_project_not_found` / `qa_project_archived` |
| plan re-validates (every invariant runs again); same task and project | `qa_plan_invalid` |
| analysis re-validates (findings and components too); same task and project | `qa_analysis_invalid` |
| patch identity: `pch_` ID, same task and project | `qa_patch_invalid` |
| registered root usable | `qa_project_unavailable` |

Caller-supplied relationships are never trusted: every ID is compared with
what the Task Engine and Project Registry return.

## 3. Outputs

`QAResult(qa_id, task_id, project_id, patch_id, patch_hash, status,
findings, checks, not_verified, policy, snapshot, metadata)`.

| Field | Meaning |
|---|---|
| `qa_id` | `qa_<ULID>`, identity only |
| `patch_hash` | hash of the patch as supplied (`None` only if it could not be computed, which forces `fail`) |
| `status` | `pass`, `fail`, or `inconclusive`, derived from the findings (§6) |
| `findings` | `QAFinding`s, errors first, at most `max_findings` |
| `checks` | the checks that ran (§5) |
| `not_verified` | what static QA never establishes: runtime behaviour, semantic fulfilment of the task, correctness of the code, approval |
| `policy` | the `QAPolicy` in force |
| `snapshot` | per change: recorded before-state, current state and hash, plus inspector usage |
| `metadata` | strategy `qa.static` and version, plan and analysis content hashes, change counts |

`QAFinding(code, severity, category, message, path, inconclusive,
metadata)`:

* `code`: stable dotted slug (table in §5);
* `severity`: `error` (blocking), `warning`, `info`;
* `category`: `patch`, `security`, `structure`, `consistency`, `scope`,
  `policy`;
* `path`: a safe project-relative path or `None`. A hostile path (absolute,
  traversal, control characters, ...) is **never echoed**;
* `message`: fixed template text. A message that contains credential
  material or a machine path is refused and replaced by "Finding details
  withheld";
* `inconclusive`: only on warnings, meaning "QA could not establish this".

## 4. Architecture

| Module | Role |
|---|---|
| `errors.py` | `QAError` + 11 codes (`qa_*`) |
| `request.py` | `QARequest` |
| `finding.py` | `QAFinding`, `Severity`, `Category`, `safe_path` |
| `result.py` | `QAResult`, `QAStatus`, `derive_status`, `CHECKS`, `NOT_VERIFIED` |
| `policy.py` | `QAPolicy`, `DEFAULT_POLICY` |
| `checks.py` | the deterministic checks |
| `service.py` | `QAService.verify` |

```
cli ──► qa ──► tasks (TaskEngine type, .get only), projects, ids, log
            ├─► planner.plan, planner.request            (Plan as data, validation helpers)
            ├─► analyst.analysis                          (SystemAnalysis as data)
            └─► programmer.change / patch / safety / errors / request / policy / snapshot
                (the Phase 15 *domain*: re-validation rules and the read-only ProjectSnapshot)
qa ─✗─► programmer.service / access / strategy / context, models, planner.service, analyst.service,
        agents, orchestrator, tools, storage, bus, contracts, config, git
programmer, analyst, planner, agents, orchestrator, tasks, projects, ... ─✗─► qa
```

QA reuses the Phase 15 rules rather than re-implementing weaker copies. It
re-validates through them, and reads the project only through
`ProjectSnapshot`, which goes through the Phase 14 `ProjectInspector`.

## 5. Static checks

All checks are deterministic. They read their inputs and append findings,
and nothing else.

| Check | Findings |
|---|---|
| **Patch integrity** | `patch.hash_mismatch` (recorded hash differs), `patch.unhashable`, `patch.change_invalid` (a change fails Phase 15 re-validation: unknown operation, bad before-hash or before-state, invalid content, ...), `patch.after_hash_mismatch`, `patch.change_altered` (fields changed after validation, or mutable state forced in), `patch.conflict` (same path twice, case-only duplicate, file/folder collision, duplicate IDs, bad summary), `patch.changes_invalid`, `patch.empty` (inconclusive) |
| **Security** | `patch.change_invalid` with category `security` (unsafe path, forbidden file, credential material), `project.path_blocked` (symlink in the path, case-only clash with an existing entry, directory or special file as target) |
| **Policy** | `policy.too_many_changes`, `policy.file_too_large`, `policy.total_too_large`, `policy.path_too_long`, `qa.findings_truncated` (inconclusive) |
| **Task consistency** | task, plan, analysis, and patch all for the same task and project (input refusals, §2) |
| **Plan consistency** | `plan.unknown_step`: a change names a step not in the plan |
| **Analysis consistency** | `analysis.plan_mismatch` (warning): the analysis was made against a different plan |
| **Project consistency** | `project.before_hash_mismatch` (file changed since the proposal), `project.target_missing`, `project.create_target_exists`, `project.before_unverified` (inconclusive: proposal had no verified before-state), `project.state_unverified` (inconclusive: current state unreadable or existence unknown) |
| **Scope** | `scope.outside_analysis` (warning): the path is outside every component location and cited path of the analysis, and is not a file the owner's specification or approved design names (`metadata.planned_paths`). This is judged by names only and is never blocking |

The task-semantics question ("does this change implement the task?") is
not answered. QA lists it under `not_verified` instead of claiming it.

## 6. Status semantics

```
any error finding                      -> fail
no error, any inconclusive warning     -> inconclusive
otherwise                              -> pass
```

`QAResult` validates that `status` matches this rule, so a result cannot say
`pass` while carrying an error. A missing patch hash forces `fail`.
Truncation sorts errors first, so a failure survives any `max_findings`, even
1. "No findings" means every defined deterministic check passed. It is
**not** proof of correctness: `not_verified` says what was not checked.

## 7. Security

* **Paths**: Phase 15 rules, applied again: no absolute, `~`, `..`, `%`,
  backslash, drive, UNC, control/format characters, non-NFC text, or
  over-long paths. Never `.git/`, `.hg/`, `.svn/`, `.ssh/`, `.gnupg/`,
  `.aws/`, `.env*`, keys, certificates, `credentials.*`, or `secrets.*`.
* **Symlinks** are never followed, and **case-only collisions** are
  detected, both against the patch itself and against existing entries.
* **Secrets**: proposed content, reasons, and evidence go through the
  linear-time Phase 15 detector. Secret-looking files are never opened.
  Existing files are read only to hash them; their contents never reach a
  finding, the snapshot, metadata, a hash input, or a log. Logs carry IDs,
  status, counts, and error codes only.
* **No machine paths**: findings and results contain project-relative paths
  only.

## 8. Immutability and hashing

* `QARequest.metadata`, `QAFinding.metadata`, and `QAResult.policy`,
  `snapshot`, and `metadata` are frozen at **every** depth (read-only
  mappings and tuples). The shared `freeze_metadata` is shallow; QA does not
  use it. Mutable inputs are copied, so changing them afterwards does not
  change the result or its hash.
* `QAResult.content_hash` is the `sha256:` of canonical JSON of everything
  except `qa_id` and `patch_id` (both random). The same input and project
  state give the same hash; a regenerated identical patch with a new
  `patch_id` gives the same QA content. Any substantive difference (patch
  hash, a finding, the project state) changes it. Hash inputs contain no
  file contents.

## 9. Failure semantics

* Invalid input raises a `QAError` with a typed code, a fixed redacted
  message, a cause (code or exception type name), and details (field
  names), never values.
* Everything wrong inside the patch is a finding, never an exception, and
  malformed data never yields `pass`.
* An unexpected failure inside a check raises `qa_internal` with the
  exception type only: no traceback, secret, environment value, or path.

## 10. Read-only guarantee

QA never creates, modifies, or deletes files or directories. It never
changes permissions, Git (commit, checkout, branch, stash, worktree), the
Project Registry, the task, the plan, the analysis, or the patch, and it
emits no events.

Proof (`tests/unit/test_qa_security.py`):

* the static rules forbid `os`, `pathlib`, `shutil`, `subprocess`,
  `socket`, `importlib`, `open`, `eval`, `exec`, `compile`, `getattr`, and
  `while` loops, plus every service, model, Git, storage, and event name.
  Deliberate-break snippets prove the rules catch violations;
* runtime tripwires make every write-mode `open`, `os`/`Path`/`shutil`
  mutation, `subprocess`, socket, new import, `eval`/`exec`/`compile`,
  `GitClient.run`, `ModelRouter.select`, `ModelProvider.generate`,
  `Programmer.program`, `Planner.plan`, `SystemAnalyst.analyze`, agents,
  tools, Orchestrator, event bus, event store, and every Task Engine
  mutator explode. QA still produces `pass`, `fail`, and `inconclusive`
  results, the project tree is byte-identical, and the task is unchanged;
* in a Git repository, status, HEAD, branches, stash, worktrees, the
  `.git` directory, and the working tree are byte-identical afterwards.

## 11. Execution prohibition

QA is **static**. It never runs project code, tests, `pytest`, `npm`,
`composer`, `python`, `node`, shells, scripts, or binaries, and it has no
sandbox or execution framework. It uses no model. Runtime verification is
future architectural work.

## 12. Limits

`QAPolicy` (defaults; hard cap in parentheses). No environment or config
override.

| Limit | Default (cap) |
|---|---|
| `max_changes` | 20 (50) |
| `max_file_size` | 256 KiB (1 MiB); larger current files are `unverified` |
| `max_total_bytes` | 1 MiB (4 MiB) |
| `max_path_length` | 512 (1024) |
| `max_findings` | 200 (500) |

Plan (≤ 50 steps), analysis (≤ 512 KiB), metadata (≤ 16 KiB), and results
(≤ 4 MiB) are bounded by their own hard caps. Current files are read
through the snapshot budget: (changes + 1) × file size, capped at 8 MiB.
Verifiable task statuses: every non-final status.

## 13. CLI

* `siamang qa [--json]`: strategy, checks, statuses, what is not verified,
  and limits. Read-only: it never opens the database, reads a project, or
  runs anything. There is no `qa run`/`test`/`apply`/`fix`/`review`/
  `approve`.
* `siamang validate`: a `QA` check builds findings and results in memory.

## 14. Future boundaries

* **Reviewer (Phase 17)**: higher-level review and a `ReviewResult`. Not
  implemented. QA stops at `QAResult`.
* **Loop (Phase 18)**: a `fail` result is reported and QA stops. There is
  no retry, repair, re-generation, iteration counter, or Programmer call.
* **Approval (Phase 19)**: QA does not authorize anything. `pass` is not
  approval, and the Approval System will make its own before-hash check
  when applying.
* This service has no API, event emission, or persistence. The HTTP runtime
  stores and serves its results.

## 15. Known limitations

* Static QA cannot prove runtime correctness, and semantic analysis is
  limited to structure and names.
* Snapshot inspection is best effort: the project can change between QA and
  any later step.
* Secret detection is pattern-based.
* Some results are honestly `inconclusive` (unverified before-states,
  unreadable files, empty patches, truncated findings).
* Scope is judged by names against the analysis. It is a warning, never a
  verdict.
* No runtime execution, real model adapter, or automatic repair exists.
* Results live in memory only.

## Phase 16 decisions

| # | Decision |
|---|---|
| QA1 | QA verifies a proposed patch and returns a `QAResult`; it never modifies, executes, retries, reviews, or approves |
| QA2 | QA is static and deterministic; no model is used and no new model access layer exists |
| QA3 | Plan, SystemAnalysis, and Patch are exact, immutable inputs, re-validated and never repaired |
| QA4 | The recorded `patch_hash` is required; any mismatch fails, even for self-consistent forgeries |
| QA5 | QA reuses the Phase 15 domain rules and `ProjectSnapshot` instead of weaker copies; never the Programmer service |
| QA6 | Input refusals raise; defects inside the patch are findings; malformed data never passes |
| QA7 | Status is derived from findings (`fail` > `inconclusive` > `pass`) and cross-checked on construction |
| QA8 | `pass` is not proof of correctness: `not_verified` states what static QA never establishes |
| QA9 | Findings never echo hostile paths, secrets, machine paths, or file contents |
| QA10 | Deep immutability everywhere; content hash excludes `qa_id` and `patch_id` |
| QA11 | Bounded by `QAPolicy` under hard caps; truncation keeps a failure and is itself inconclusive |
| QA12 | No persistence and no events (schema v3, contract v0.2) |
| QA13 | QA is not an Agent and is not wired into the Orchestrator; nothing upstream imports it |
| QA14 | Read-only CLI `siamang qa [--json]` plus a `QA` check in `validate`; no run/test/apply |
| QA15 | Mutable state forced into a validated change counts as tampering |
