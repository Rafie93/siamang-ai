# Reviewer (Phase 17)

**Status:** implemented (`src/siamang/reviewer/`). Runtime is still
stdlib-only. No schema change (v3), no contract change (v0.2), no new config,
events, tools, or model use.

> **Reviewer does not approve changes. Reviewer does not apply changes.
> Reviewer does not repair changes. Reviewer does not execute code.**
> Phase 17 Reviewer makes an independent, static engineering judgment about a
> proposed change that QA has already verified, and produces a ReviewResult.
> It reads no project file, uses no model, and calls no other service.

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Purpose

QA answers "does this proposal pass the defined static checks?". The
Reviewer answers a different question: **given everything we know, is this
change a sound response to the task?** It judges alignment, scope,
maintainability, consistency, risk, and feasibility, and it treats the
`QAResult` as evidence. It does not repeat QA's checks.

```
Task
  ├── Plan
  ├── SystemAnalysis
  └── Programmer ──► ProposedPatch ──► QA ──► QAResult
                          │                      │
                          └──────────┬───────────┘
                                     ▼
                                  Reviewer ──► deterministic judgments ──► ReviewResult
                                                                                │
                                                                                X   nothing proceeds automatically
```

| Layer | Question |
|---|---|
| Programmer (15) | HOW should the change be implemented? (proposes) |
| QA (16) | Does the proposal pass the defined static checks? (verifies) |
| **Reviewer (17)** | Is the change a sound response to the task, judged from the evidence? (judges) |
| Loop (18, future) | Should the Programmer try again? (coordinates iteration) |
| Approval (19, future) | MAY the change be applied? (authorizes) |

`QA pass ≠ review pass`, and `review pass ≠ approval`.

## 2. Responsibility and non-responsibility

The Reviewer **does**: accept Task IDs, Plan, SystemAnalysis, Patch, and
QAResult as immutable data; refuse inconsistent or tampered artifacts; judge
the change deterministically; return an immutable, bounded `ReviewResult`.

The Reviewer **does not**: approve, reject, accept, merge, apply, commit,
deploy, repair, retry, regenerate, or loop. It does not call the Programmer,
QA, Planner, System Analyst, an Agent, the Orchestrator, tools, a model, Git,
storage, or the event bus. It does not change the task, project, registry,
or any input artifact. It does not read the project tree. It is not an
Agent.

## 3. Inputs

`ReviewRequest(task_id, project_id, plan, analysis, patch, patch_hash,
qa_result, qa_hash, metadata={})`, frozen.

* `plan`, `analysis`, `patch`, and `qa_result` must be **exact** `Plan`,
  `SystemAnalysis`, `Patch`, and `QAResult` objects: immutable data snapshots,
  never live services, and no subclasses.
* `patch_hash` / `qa_hash` are the `content_hash` values the caller recorded
  when the Programmer and QA produced the artifacts. Both are recomputed.
* `metadata` refuses (at any depth) every QA/Programmer key (execution,
  path, credential, agent, tool, model, Git, write, repair, retry, review,
  approval) plus `accept`, `merge`, `deploy`, `release`, `push`, `status`,
  `verdict`, `override`, `force`, `skip_qa`, `qa_status`, `severity`. It is
  frozen at every depth and copied from the caller.

Validation before any judgment (each refusal raises a `ReviewerError`; the
first wins):

| Step | Refusal |
|---|---|
| request is a `ReviewRequest` | `reviewer_request_invalid` |
| task exists (`TaskEngine.get`, read only) | `reviewer_task_not_found` |
| task belongs to `project_id` | `reviewer_request_invalid` |
| task not final (`completed`/`cancelled`) | `reviewer_policy_denied` |
| project registered and active (`ProjectRegistry`) | `reviewer_project_not_found` / `reviewer_project_archived` |
| plan re-validates; same task and project | `reviewer_plan_invalid` |
| analysis re-validates; same task and project | `reviewer_analysis_invalid` |
| patch: `pch_` ID, same task and project; within `max_changes` and `max_total_bytes`; every change re-validates under the Phase 15 rules (paths, forbidden files, secrets, text, hashes) and is unaltered (no forced mutable state); no conflicts; recomputed hash equals `patch_hash` | `reviewer_patch_invalid` / `reviewer_policy_denied` |
| QAResult re-validates (status re-derived from its findings); unaltered (deep-frozen); recomputed hash equals `qa_hash`; `qa_` ID; same task, project, `patch_id`, `patch_hash`; QA strategy `qa.static`; QA's recorded plan and analysis content hashes equal these; QA's change count and snapshot entries match this patch | `reviewer_qa_invalid` |

Unlike QA, a defect *inside* the patch is a refusal here, not a finding. QA
reports defects; the Reviewer only judges a change whose artifacts are
consistent with each other, and never produces a review of a forged or
mismatched chain.

## 4. Outputs

`ReviewResult(review_id, task_id, project_id, plan_id, analysis_id,
patch_id, patch_hash, qa_id, qa_hash, qa_status, status, findings,
dimensions, not_verified, policy, metadata)`.

| Field | Meaning |
|---|---|
| `review_id` | `rev_<ULID>`, identity only |
| `plan_id`, `analysis_id`, `patch_id`, `qa_id` | the artifacts reviewed (identity) |
| `patch_hash`, `qa_hash` | the verified content hashes |
| `qa_status` | QA's status, carried as evidence |
| `status` | `pass`, `concern`, `fail`, or `inconclusive` (§6) |
| `findings` | `ReviewFinding`s, errors first, at most `max_findings` |
| `dimensions` | the dimensions judged (§5) |
| `not_verified` | `runtime_behavior`, `semantic_correctness`, `integration_behavior`, `production_behavior`, `user_acceptance`, `approval` (cannot be dropped) |
| `policy` | the `ReviewerPolicy` in force |
| `metadata` | strategy `review.static` and version, plan and analysis content hashes, changes reviewed, operation counts, proposed bytes |

`ReviewFinding(code, severity, category, message, path, inconclusive,
metadata)`:

* `code`: stable dotted slug (table in §5);
* `severity`: `error` (blocking review concern), `warning`, `info`;
* `category`: `verification`, `alignment`, `scope`, `maintainability`,
  `consistency`, `risk`, `security`, `feasibility`;
* `path`: a safe project-relative path or `None`; a hostile path is never
  echoed;
* `message`: fixed template text; one containing credential material,
  machine paths, control characters, or over 300 characters is replaced by
  "Finding details withheld";
* `inconclusive`: only on warnings: "the evidence is not enough".
* `metadata`: bounded (≤ 1 KiB), frozen: counts and QA finding codes only.

## 5. Review dimensions

All judgments are deterministic and static. They use names, structure,
counts, and the supplied artifacts, never file contents read from disk and
never execution. Every one of them is a *judgment*, not proof.

| Dimension | Code | Severity | Judgment |
|---|---|---|---|
| verification | `qa.failed` | error | QA failed: unresolved verification errors (cites QA codes) |
| | `qa.inconclusive` | warning, inconclusive | QA could not finish: verification incomplete |
| | `qa.warnings_open` | warning | QA's non-blocking warnings remain unresolved |
| | `qa.passed` | info | QA passed: evidence, not proof |
| alignment | `alignment.unrelated_change` | warning | path neither relates by name to the task (objectives, constraints, analysis components, evidence; word stems) nor lies where the analysis pointed |
| | `alignment.unrelated_patch` | **error** | *no* change in the patch relates to the task |
| scope | `scope.broad_change` | warning | more changes than `broad_change_count` |
| | `scope.directory_spread` | warning | more top-level directories than `max_directories` |
| maintainability | `maintainability.large_file` | warning | a newly created file longer than `max_file_lines` |
| | `maintainability.many_new_files` | warning | more created files than `max_new_files` |
| | `maintainability.duplicate_content` | warning | identical content proposed for two files |
| consistency | `consistency.naming_style` | warning | created file's name style (snake/kebab/camel/pascal) differs from known files of the same type |
| | `consistency.unfamiliar_file_type` | warning | created file type not among the files known for the project |
| risk | `risk.deletion` | warning | a file is deleted |
| | `risk.dependency_change` | warning | dependency manifest or lock file |
| | `risk.configuration_change` | warning | build, CI, deployment, or configuration file |
| | `risk.public_interface` | warning | module/API interface file (`__init__.py`, `index.*`, `*.d.ts`, `*.proto`, OpenAPI, ...) |
| | `security.sensitive_area` | warning | authentication, sessions, permissions, cryptography paths |
| feasibility | `feasibility.empty_patch` | warning, inconclusive | nothing to judge |
| | `feasibility.empty_file` | warning | proposed content empty or whitespace |
| | `feasibility.step_role` | warning | change attributed to an analysis or review step |
| | `feasibility.tests_absent` | warning | the plan has a testing step, but code changes come without test changes |
| (bound) | `review.findings_truncated` | warning, inconclusive | more findings than `max_findings` |

Risks are concerns, never verdicts: the only blocking judgments are QA
failure and a patch with no relation to the task.

## 6. Status semantics

```
any error finding                         -> fail
no error, any inconclusive warning        -> inconclusive
no error, nothing inconclusive, a warning -> concern
otherwise                                 -> pass
```

`ReviewResult` validates on construction that `status` matches this rule
and that QA evidence is honoured: a QA `fail` can only give `fail`; a QA
`inconclusive` can never give `pass` or `concern`. QA `pass` is still
reviewed independently and may give any status.

* **pass**: no blocking concern, nothing inconclusive, no warning, based on
  the evidence available. Not proof of correctness, not approval.
* **concern**: warnings a human should look at; not enough to fail.
* **fail**: a blocking review concern.
* **inconclusive**: the evidence is not enough for a safe judgment.

There is no `approved`, `rejected`, `accepted`, `merged`, or `deployed`.

## 7. Security

* **Paths**: every change re-validates under the Phase 15 rules: no absolute,
  `~`, `..`, `%`, backslash, drive, UNC, control/format characters, non-NFC,
  or over-long paths; never `.git/`, `.ssh/`, `.env*`, keys, credentials,
  or secrets files; no case-only or file/directory conflicts. Violations are
  refusals with a fixed message: the path is never echoed into the error,
  details, findings, CLI, or logs.
* **Symlinks and special files**: the Reviewer never touches the
  filesystem, so it cannot follow one. QA's snapshot refuses them
  (`project.path_blocked`), and that QA failure makes the review fail.
* **Secrets**: proposed content goes through the linear-time Phase 15
  detector on re-validation; secret-bearing patches are refused. Findings
  carry codes, counts, and safe paths only; QA finding metadata is never
  copied (only QA finding *codes* are cited). Logs carry IDs, status, counts,
  and error codes.
* **Tamper detection**: recomputed `patch_hash` and `qa_hash`; fields forced
  past validation (including a forged QA status) and mutable state forced
  into a frozen artifact are refusals; QA's own records bind it to the plan,
  analysis, and patch.
* **No machine paths**: `root_path` is never read; results contain
  project-relative paths only.

## 8. Immutability and hashing

* `ReviewRequest.metadata`, `ReviewFinding.metadata`, and
  `ReviewResult.policy` and `metadata` are frozen at every depth; lists
  become tuples. Every mutable input (metadata, findings lists, policy
  status sets) is copied, so mutating it afterwards changes nothing.
* `ReviewResult.content_hash` is the `sha256:` of canonical JSON of
  everything except `review_id` and the random artifact IDs. The same
  inputs, project state, and policy give the same hash, even for a
  regenerated identical proposal with new IDs. The decision uses no clock,
  randomness, network, or machine state.

## 9. Failure semantics

* Invalid input raises a `ReviewerError` (11 codes, `reviewer_*`) with a
  fixed redacted message, a cause (code or exception type name), and
  details (field names), never values.
* Review concerns are findings, never exceptions.
* An unexpected failure in a judgment raises `reviewer_internal` with the
  exception type only.

## 10. Read-only guarantee

Proof (`tests/unit/test_reviewer_security.py`):

* static rules forbid `os`, `pathlib`, `io`, `subprocess`, `socket`,
  `importlib`, `random`, `time`, `open`, `eval`, `exec`, `compile`,
  `getattr`/`hasattr`, `Path`, `root_path`, file reads, `while` loops, and
  every service, model, Git, storage, snapshot, inspector, and event name.
  Deliberate-break snippets prove the rules catch violations;
* runtime tripwires make every write-mode `open`, filesystem mutation,
  `subprocess`, socket, new import, `eval`/`exec`/`compile`,
  `GitClient.run`, `ModelRouter.select`, `ModelProvider.generate`,
  `Programmer.program`, `QAService.verify`, `Planner.plan`,
  `SystemAnalyst.analyze`, agents, tools, Orchestrator, event bus, event
  store, and every Task Engine mutator explode, and count every
  `open`/`os.open`/`scandir`/`listdir`/`stat`. The Reviewer still produces
  `pass`, `concern`, `fail`, and `inconclusive`, with **zero** files opened,
  listed, or stat-ed; the project tree is byte-identical, and the task,
  project, patch, and QA result are unchanged;
* in a Git repository, status, HEAD, branches, stash, worktrees, `.git`,
  and the working tree are byte-identical afterwards;
* network calls: 0 (socket `connect`, `create_connection`, `getaddrinfo`).

## 11. Limits

`ReviewerPolicy` (defaults; hard cap in parentheses). No environment or
config override.

| Limit | Default (cap) | Exceeded |
|---|---|---|
| `max_changes` | 20 (50) | refusal |
| `max_total_bytes` | 1 MiB (4 MiB) | refusal |
| `max_findings` | 100 (500) | cut, errors first; inconclusive |
| `broad_change_count` | 10 (50) | warning |
| `max_directories` | 3 (50) | warning |
| `max_new_files` | 5 (50) | warning |
| `max_file_lines` | 400 (100 000) | warning |

Messages ≤ 300 characters, finding metadata ≤ 1 KiB, results ≤ 1 MiB. Name
matching is linear-time. Reviewable task statuses: every non-final status.

## 12. CLI

* `siamang reviewer [--json]`: strategy, dimensions, statuses, what is not
  verified, and limits. Read-only: it never opens the database, reads a
  project, or reviews anything. There is no
  `reviewer run`/`approve`/`apply`/`fix`/`merge`.
* `siamang validate`: a `Reviewer` check builds findings and results in
  memory and fails if the policy could read, write, execute, use models,
  approve, apply, or repair.

## 13. Architecture

| Module | Role |
|---|---|
| `errors.py` | `ReviewerError` + 11 codes |
| `request.py` | `ReviewRequest` |
| `finding.py` | `ReviewFinding`, `Severity`, `Category` |
| `result.py` | `ReviewResult`, `ReviewStatus`, `derive_status`, `DIMENSIONS`, `NOT_VERIFIED` |
| `policy.py` | `ReviewerPolicy`, `DEFAULT_POLICY` |
| `checks.py` | the deterministic judgments |
| `service.py` | `ReviewerService.review` |

```
cli ──► reviewer ──► tasks (TaskEngine type, .get only), projects (exists/active), ids, log
              ├─► planner.plan / step / request            (Plan as data, validation helpers)
              ├─► analyst.analysis                          (SystemAnalysis as data)
              ├─► programmer.change / patch / safety        (Phase 15 domain: re-validation rules)
              └─► qa.result / request / finding / checks    (QAResult as data; pure helpers)
reviewer ─✗─► qa.service, programmer.service/access/strategy/context/snapshot, planner.service,
              analyst.service/inspection, models, agents, orchestrator, tools, storage, bus,
              contracts, config, git
qa, programmer, analyst, planner, agents, orchestrator, tasks, projects, ... ─✗─► reviewer
```

The Reviewer depends on earlier phases as *data and rules* only; no earlier
phase depends on it. Pipeline orchestration across phases is not Phase 17's
job.

## 14. Future boundaries

* **Loop (Phase 18)**: a `fail` or `concern` is reported and the Reviewer
  stops. There is no retry, repair, regeneration, iteration counter, or
  Programmer call.
* **Approval (Phase 19)**: the Reviewer authorizes nothing. `pass` is not
  approval; there is no approval state, approve/reject, apply, commit, or
  merge. The Approval System will make its own provenance and before-hash
  checks.
* This service has no API, event emission, or persistence. The HTTP runtime
  stores and serves its results.

## 15. Known limitations

A static Reviewer cannot establish runtime correctness, full semantic
correctness, production behavior, real integration behavior, user
acceptance, or approval.

* Alignment, scope, consistency, and risk are judged by **names and
  structure only**; a well-named but wrong change can pass, and an unusual
  but correct one can raise concerns.
* Proposed content is not diffed against the current file (the Reviewer
  does not read the project), so "large change" means a long proposed file,
  not a large diff.
* Recorded hashes detect alteration after recording. They do not prove who
  produced an artifact: a caller that fabricates a whole consistent chain
  (patch, QA result, and their hashes) is not detectable without provenance,
  which belongs to the Approval System.
* Secret detection is pattern-based.
* Results live in memory only.

## Phase 17 decisions

| # | Decision |
|---|---|
| RV1 | The Reviewer judges a QA-verified proposal and returns a `ReviewResult`; it never approves, applies, repairs, retries, or executes |
| RV2 | Static and deterministic; no model, no network, and no new model access layer |
| RV3 | No project reads at all: judgments use only the supplied artifacts (QA already compared the project) |
| RV4 | Plan, SystemAnalysis, Patch, and QAResult are exact, immutable inputs, re-validated and never repaired |
| RV5 | Recorded `patch_hash` and `qa_hash` are required; a QAResult must be bound to the same task, patch, plan, and analysis |
| RV6 | Inconsistent or tampered artifacts are refusals; concerns about a consistent change are findings |
| RV7 | QA is evidence, not repeated: QA `fail` → `fail`; QA `inconclusive` → never `pass`/`concern`; QA `pass` is reviewed independently |
| RV8 | Status `pass`/`concern`/`fail`/`inconclusive` derived from findings and cross-checked; no approval vocabulary |
| RV9 | Risks are warnings; only QA failure and a wholly unrelated patch block |
| RV10 | `not_verified` always lists what a static review cannot establish |
| RV11 | Findings never echo hostile paths, secrets, machine paths, file contents, or QA finding metadata |
| RV12 | Deep immutability everywhere; content hash excludes `review_id` and random artifact IDs |
| RV13 | Bounded by `ReviewerPolicy` under hard caps; resource bounds refuse, judgment thresholds warn |
| RV14 | No persistence and no events (schema v3, contract v0.2) |
| RV15 | Not an Agent, not wired into the Orchestrator; nothing upstream imports it |
| RV16 | Read-only CLI `siamang reviewer [--json]` plus a `Reviewer` check in `validate` |
