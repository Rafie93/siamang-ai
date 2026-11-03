# Approval (Phase 19)

**Status:** implemented (`src/siamang/approval/`). Runtime is still
stdlib-only. No schema change (v3), no contract change (v0.2: the existing
`approval.*` events are not emitted), no new config, events, or tools.

> **Approval authorizes. Approval does not apply. Approval does not execute.
> Approval does not commit. Approval does not deploy.**
> An authorized approver makes an explicit decision (`approve` or `reject`)
> about exactly one loop's final proposal. The decision is bound to the
> whole evidence chain by IDs and recomputed content hashes. Nothing is
> approved automatically, and nothing can be overridden. Not one byte of
> the proposed patch changes.

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Responsibility

```
Programmer = proposes   QA = verifies   Reviewer = judges   Loop = coordinates bounded iteration
Approval   = authorizes                 Execution = applies   (future, not this phase)
```

`ApprovalResult` answers WHO decided, WHAT was decided on (task, project,
plan, analysis, loop, patch ID + hash, QA, review), on WHAT EVIDENCE, under
WHICH POLICY, and WHICH DECISION. It never answers "has it been applied?".

## 2. API

```python
service = ApprovalService(task_engine, policy=ApprovalPolicy(authorized_approvers={"owner"}))
request = ApprovalRequest(task_id, project_id, plan, analysis, loop_result)  # chain verified here
result  = service.decide(request, ApprovalDecision("approve", "owner", "QA and review pass."))
service.verify(result, request)                                               # raises unless still valid
```

* `decide` needs **both** a request and an explicit `ApprovalDecision`.
  The decision has no default; there is no `approve(request)` shortcut, and
  nothing like "QA pass + review pass ⇒ approved". A passing loop only makes
  a proposal **eligible**.
* `ApprovalDecision(decision, approver_id, reason=None)`: `approve` or
  `reject` only. A reason is optional for `approve` and **required** for
  `reject`. It is single-line text within the policy bound. Control
  characters, credential material, and machine paths are **refused**, not
  rewritten. A reason is a human note, not evidence.
* The request has no field for an approver, role, `is_admin`,
  `authorized`, or override. Authority comes only from the service's
  policy.

## 3. Evidence requirements (strict, no override)

An `approve` requires all of these (`policy.REQUIREMENTS`):

| Requirement | Meaning |
|---|---|
| `chain_verified` | every artifact re-validates and binds (§4) |
| `patch_hash_verified` | the patch hash is recomputed from the immutable patch, never taken from a caller |
| `review_rederived` / `qa_static_checks_rederived` | the evidence is what its producers would say (§5) |
| `final_patch_present` | the loop produced a final patch, QA result, and review |
| `qa_pass`, `review_pass`, `loop_pass`, `loop_success` | QA `pass`, review `pass`, loop `pass` with stop reason `success` |
| `approver_authorized` | the approver is in the policy's set |

If any requirement fails, the decision is refused. A consistent chain with
non-passing evidence (QA fail, review fail, concern, or inconclusive; a loop
stopped on `max_iterations`, `no_progress`, ...) raises
`approval_not_eligible`. It can still be **rejected** by an authorized
approver; rejecting a valid chain is a legitimate decision. There is no
`force_approve`, `emergency`, `skip_qa`, `skip_review`, magic approver,
override flag, or expiry. A secret or forbidden path in the patch fails
Phase 15 re-validation, and no approver can authorize past that.

## 4. Provenance and integrity

`ApprovalProvenance` holds `task_id`, `project_id`, `plan_id` + hash,
`analysis_id` + hash, `loop_id` + hash, `final_iteration`, `patch_id` + hash,
`qa_id` + hash, and `review_id` + hash. It contains only IDs and the
repository's existing `sha256:` content hashes, never file contents.
`ApprovalRequest` records it at construction.

Every artifact is checked under its own phase's rules. A failure raises
`approval_provenance_invalid`:

* **Re-validation**: Plan, SystemAnalysis, Patch (every Phase 15 rule:
  paths, forbidden files, secrets, text, conflicts), QAResult, ReviewResult,
  and LoopResult are rebuilt. Fields forced past validation, or mutable
  state forced in (lists, dicts), are refused. QA, review, and loop must
  carry exactly their producer's fixed fields (checks/dimensions,
  `not_verified`, strategy).
* **Binding**: all artifacts share the task and project. The loop, QA, and
  review were made against this plan and analysis (ID and recorded content
  hash). QA and review name this patch by ID **and** hash, and the review
  names this QA result by ID and hash. The final artifacts are the loop's
  **last evaluated round**. A patch from round 1 of a two-round loop, or
  final artifacts taken from another loop, cannot be approved.
* **Staleness**: `decide` and `verify` recompute the provenance from the
  artifacts. If anything changed after the request was made (an artifact
  altered in place, the request pointed at another loop), it is refused.

## 5. Anti-fabrication (re-derived evidence)

Consistency alone cannot stop a caller from fabricating a whole consistent
chain. Phase 18 documented this limitation. Approval closes the part that
can be checked from the artifacts themselves:

* **Reviewer**: the review's findings must equal
  `reviewer.checks.judge(...)` recomputed under the review's own recorded
  policy. That function is pure and is the same code the Reviewer runs. A
  review "pass" with its findings stripped is refused.
* **QA**: QA's static checks that need no project access (patch integrity,
  Phase 15 rules, policy limits, plan steps, analysis agreement and scope)
  are re-run under QA's recorded policy. Every finding they produce must be
  in the recorded QA result. A QA "pass" over a patch that breaks QA's
  static rules is refused.

What cannot be re-derived from the artifacts is **not** claimed:

* QA's comparison of the recorded before-state with the project on disk
  (Approval reads no files);
* that a model, rather than a person, produced the patch;
* who produced any artifact.

## 6. Authority vs identity (trust model)

| Property | Phase 19 |
|---|---|
| **Integrity** (unchanged since hashed) | yes: recomputed content hashes, staleness check, re-validation |
| **Provenance** (from the expected stage, in one chain) | yes for binding and re-derivable evidence (§4–5); producer identity is not provable |
| **Authority** (may this approver decide?) | yes: an explicit, finite `authorized_approvers` set in the service's policy |
| **Authenticity** (is it really that person?) | **no**: there is no authentication |

`approver_id` is caller-supplied authorization context, **not** an
authenticated identity. An `ApprovalResult` proves that *an authorized
approval context approved artifact X*. It does not prove that *a real,
authenticated person named X* did. Authentication belongs to the
HTTP API layer. The policy:

* `authorized_approvers`: explicit IDs, at most 50. An empty set means
  **nobody** (the default policy authorizes no one). There is no wildcard,
  no "everyone", and no built-in `admin`/`root`; those are ordinary IDs,
  authorized only when listed.
* `max_reason_length`: 300 (hard cap 600).
* The policy is frozen. No environment, config, CLI, or request input can
  change it.

## 7. Replay and the seal

* Scope is narrow: one project, task, plan, analysis, loop, final round,
  patch ID **and** hash, QA, and review. The same ID with a different hash,
  or the same hash under a different ID, is not transferable.
* `ApprovalResult.approval_hash` is the `sha256:` of its canonical content
  (everything except `approval_id` and `seal`). `seal` is an HMAC-SHA256
  over the approval ID and that hash, with a key held only by the issuing
  `ApprovalService` (random per instance, or injected). `verify` refuses:
  * a result issued by another service instance;
  * a result whose decision, approver, reason, or scope was altered or
    re-targeted (for example `approval_id` A with patch B);
  * a result checked against a different chain;
  * a result whose evidence changed after approval;
  * a result whose approver the current policy no longer authorizes.
* The seal is a **process-local integrity seal**, not a digital signature,
  certificate, or proof of identity. It does not survive a restart unless
  the key is injected, and there is no persistence. No RSA, ECDSA, PKI, or
  key management was added.
* There is no time-based expiry, since the repository has no trusted clock.
  Replay is prevented by artifact binding instead.

## 8. Errors are never decisions

| Code | When |
|---|---|
| `approval_request_invalid` | malformed request or decision, unsafe reason, reason over the policy bound |
| `approval_task_not_found`, `approval_project_not_found`, `approval_project_archived` | unknown task or project, archived project |
| `approval_unauthorized` | approver not in the policy (for approve **and** reject) |
| `approval_provenance_invalid` | re-validation, binding, staleness, or re-derivation failed |
| `approval_not_eligible` | consistent chain without passing evidence (approve only), or a closed task |
| `approval_result_invalid` | `verify`: forged, altered, foreign, or re-targeted result |
| `approval_internal` | unexpected failure (exception type only) |

Invalid evidence is never turned into a human `rejected`. Every error uses
fixed messages and field names. It never contains approver IDs, reasons,
paths, secrets, or contents. Logs carry IDs, decision, and error codes only.

## 9. No-write, no-execution, no-network guarantee

Proof (`tests/unit/test_approval_security.py`):

* The static rules forbid `os`, `pathlib`, `io`, `subprocess`, `socket`,
  `time`, `datetime`, `random`, JWT/crypto libraries, `open`, `eval`,
  `exec`, `getattr`, `while`, and every service, model, agent, tool, Git,
  storage, event, apply, commit, merge, deploy, persist, and emit name. No
  override or magic-approver identifier exists in the code.
  Deliberate-break snippets prove the rules work.
* Runtime tripwires turn into errors every write, process, import,
  `eval`/`exec`, `GitClient.run`, model router and provider,
  `Programmer.program`, `QAService.verify`, `ReviewerService.review`,
  `LoopService.run`, Planner, Analyst, agents, tools, Orchestrator, event
  bus and store, and Task Engine mutator. They also count every `open`,
  `scandir`, `listdir`, and `stat`. Approval still approves, rejects, and
  verifies, with **zero** files opened. Afterwards the task, project, plan,
  analysis, loop result, patch, QA result, review, and project tree are
  unchanged.
* In a Git repository, status, HEAD, branches, stash, worktrees, `.git`,
  and the working tree are byte-identical.
* Network calls: 0. Model calls: 0.

## 10. Immutability and determinism

`ApprovalDecision`, `ApprovalPolicy` (with a frozenset copy of the
caller's approvers), `ApprovalProvenance`, `ApprovalRequest`, and
`ApprovalResult` (with read-only `evidence`) are frozen. Mutating
caller-owned or upstream objects later never changes an issued result.
The same decision on the same chain gives the same `approval_hash`; only
`approval_id` and `seal` differ. The service stores only the task engine,
the policy, and its seal key. It keeps no record of decisions, so
`approve A`, `reject B`, `approve C` cannot affect one another.

## 11. CLI

`siamang approval [--json]` is a read-only view of the policy. There is no
`approve`, `reject`, `grant`, `apply`, or `--approver`/`--force`. A
decision needs a structured `ApprovalDecision` in code. `siamang validate`
has an `Approval` check that fails on a default approver, a wildcard, an
override, a reason-less rejection, or an applying policy.

## 12. Phase 20 boundary and limitations

* This service has no API, WebSocket, UI, event emission, persistence,
  authentication, patch application, commit, merge, or deployment. The HTTP
  runtime adds authentication, persistence, and publishing around it. An
  `ApprovalResult` is in-memory input for a future execution layer, which
  must make its own before-hash check against the files it changes.
* No authentication: `approver_id` is trusted caller context.
* No signatures: the seal is process-local.
* Producer identity and QA's project-state comparison are not re-derivable
  (§5).
* Static evidence cannot establish runtime correctness. Approval is not
  execution.

## Phase 19 decisions

| # | Decision |
|---|---|
| AP1 | Approval authorizes one loop's final proposal by explicit decision; it never applies, executes, commits, merges, or deploys |
| AP2 | No automatic, default, or implicit approval; a passing loop is only eligible |
| AP3 | Approve requires QA, review, and loop pass with stop reason `success`; there is no override of any kind |
| AP4 | Authority comes only from an explicit, finite approver set in the service policy; empty means nobody; no wildcard or magic actor |
| AP5 | `approver_id` is caller context, not authentication; documented as such |
| AP6 | Provenance = IDs + recomputed content hashes of every artifact, bound to the loop's last evaluated round |
| AP7 | Beyond consistency, the Reviewer's judgment and QA's static checks are re-derived from the artifacts (pure `checks.judge`, QA check functions) |
| AP8 | Staleness: provenance is recomputed at decide/verify time; any change in between is refused |
| AP9 | Errors are never decisions; invalid evidence never becomes `rejected` |
| AP10 | Reject is a legitimate decision on any consistent chain; it needs a reason |
| AP11 | Results are sealed (HMAC-SHA256, service-held key) so `verify` refuses forged, re-targeted, or foreign results; not a signature |
| AP12 | No expiry, persistence, events, API, or authentication in Phase 19 |
| AP13 | The Reviewer's check sequence was extracted as pure `checks.judge` (behaviour unchanged) so it can be re-derived without the service |
| AP14 | Read-only CLI `siamang approval [--json]` and an `Approval` check in `validate`; no decision command |
