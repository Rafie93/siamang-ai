# Loop (Phase 18): QA → Programmer → QA

**Status:** implemented (`src/siamang/loop/`). Runtime is still stdlib-only.
No schema change (v3), no contract change (v0.2), no new config, events, or
tools. No change to the Programmer, QA, or Reviewer.

> **Loop does not apply patches. Loop does not approve patches. Loop does
> not execute code. Loop does not commit. Loop does not deploy. Loop does
> not run indefinitely.**
> The Loop is a bounded coordinator. It asks the Programmer for a proposal,
> has QA verify it and the Reviewer judge it, and, if the review is not a
> pass and the policy allows it, gives bounded feedback to one more
> Programmer call. Its final patch is still only a **proposal**.

> **Current state and direction.** This document describes the code as it is today. The HTTP runtime ([HTTP_API.md](HTTP_API.md)) publishes approved work as a branch and pull request. Which limits here will change, and when, is in [VISION.md](VISION.md).

## 1. Responsibility

```
Programmer = proposes        QA = verifies        Reviewer = judges
Loop       = coordinates bounded iteration
Approval   = authorizes      (Phase 19)           Execution = applies (future)
```

The Loop is not an Agent, an autonomous executor, a patch applier, an
approval system, a task scheduler, or a self-healing system. It has no
memory, planning, tool selection, or goal discovery. It follows the fixed
state machine in §3.

## 2. Inputs and wiring

`LoopService(tasks, programmer, qa, reviewer, policy=DEFAULT_POLICY)`: the
TaskEngine (read only), a `Programmer`, a `QAService`, and a
`ReviewerService` are injected once. The service keeps no run state.

`LoopRequest(task_id, project_id, instruction, plan, analysis, input={},
constraints=(), metadata={})`, frozen:

* `plan` / `analysis` must be exact `Plan` / `SystemAnalysis` objects, for
  the request's task and project;
* `instruction`, `input`, and `constraints` go to every Programmer call
  unchanged;
* `input` and `metadata` refuse, at any depth, every Reviewer/QA/Programmer
  key plus `loop_feedback`, `feedback`, `max_iterations`, `budget`,
  `unlimited`, `policy`, `stop_reason`, ... A request can therefore neither
  widen the policy nor inject feedback. Both are deep-frozen copies, and
  `input` is at most 8 KiB.

## 3. State machine

```
START
  ↓  (round n ≤ max_iterations, and every call budget can pay for a whole round)
PROGRAM ── Programmer.program(ProgrammingRequest + feedback of round n−1 as typed input data)
  ├── no patch (failed / waiting) ────────────────► STOP programmer_failed / programmer_waiting
  ├── patch content hash seen in any earlier round ► STOP no_progress   (not verified or reviewed again)
  ↓
QA ─────── QAService.verify(patch, its own content hash)
  ↓
REVIEW ─── ReviewerService.review(patch, QA result, their own content hashes)
  ├── pass ───────────────────────────────────────► STOP success
  ├── status not in retry_statuses ───────────────► STOP not_retryable  (inconclusive → STOP inconclusive)
  ├── n == max_iterations ────────────────────────► STOP max_iterations
  ├── a call budget is spent ─────────────────────► STOP call_budget
  └── otherwise: feedback(n) ──► PROGRAM (round n+1)
```

The round loop is a `for` over `range(1, max_iterations + 1)`; there is no
`while`. Every transition back to PROGRAM requires a retryable status,
remaining iterations, remaining budgets, and new content. PASS, NO_PROGRESS,
MAX_ITERATIONS, and CALL_BUDGET are terminal. A Programmer that fails or
waits is never retried.

QA `fail` still goes to the Reviewer. The Phase 17 contract accepts a
failed QA result and always turns it into a review `fail`, whose findings,
together with QA's, become the feedback.

## 4. Artifact chain

```
Task → Plan → SystemAnalysis → Programmer → Patch N → QA N → Review N → Feedback N → Programmer → Patch N+1 → …
```

Before it is used, every returned artifact is checked against the round
that produced it:

| Artifact | Checks (a failure raises `loop_chain_broken`, `iteration_n`) |
|---|---|
| ProgrammingResult | exact type; re-validates; same task and project; `programming_id` never seen before |
| Patch | exact type; `pch_` ID never seen before; same task and project; content hash computable |
| QAResult | exact type; re-validates, in validated form (enum status, tuples), nothing mutable forced in; same task and project; same `patch_id` and patch content hash; QA's recorded plan and analysis hashes; `qa_id` never seen before |
| ReviewResult | the same, plus same plan and analysis IDs, same `qa_id`, same QA content hash, same QA status; `review_id` never seen before |

So QA(N) cannot be attached to Patch(N+1), Review(N) cannot be attached to
QA(N+1), and a component cannot replay an earlier result. Feedback N is
built locally, sent only to round N+1, and records `from_iteration` and
`previous_patch_hash`. A component refusing its input raises
`loop_component_refused` (`cause` = the component's error code). An
unexpected component exception raises `loop_internal` with only its type
name. The run stops in both cases. Nothing is retried, swallowed, or turned
into a result.

## 5. Feedback

`LoopFeedback(source, iteration, code, severity, message, target)`, built
only from the current round's QA and Reviewer **findings**. Task, plan, and
analysis text, file contents, and model output are never used.

* **Included**: `error` and `warning` findings. `info` findings and the
  Reviewer's `verification` findings (a mirror of QA's status) are left out.
* **Ordered**: errors first, then QA before Reviewer, then finding order.
  Lines are de-duplicated by (source, code, target).
* **Bounded**: `max_feedback_items` (20, cap 50) and `max_feedback_bytes`
  (4 KiB, cap 6 KiB). A cut sets `truncated`.
* **Re-checked as hostile**: codes must be dotted slugs. A message with
  control characters, credential material, or machine paths, or longer
  than 300 characters, becomes "Finding details withheld". A hostile path
  is dropped.

The Programmer receives the feedback as typed data under the reserved key
`loop_feedback` in `ProgrammingRequest.input`:

```json
{"kind": "automated_review_findings", "from_iteration": 1, "previous_patch_hash": "sha256:…",
 "previous_review_status": "concern", "truncated": false,
 "notes": [{"source": "reviewer", "code": "feasibility.tests_absent", "severity": "warning",
            "message": "…", "target": null}]}
```

The block then goes through the Programmer's own input validation (key
rules, deep freeze, 16 KiB cap) and context scrubbing before any model sees
it. Nothing in the Loop evaluates, executes, or routes feedback to a tool.
For the model it is data, like the rest of the context.

## 6. Outputs

`LoopIteration(number, task_id, project_id, outcome, programming_id,
programming_status, patch_id, patch_hash, qa_id, qa_status, review_id,
review_status, feedback, feedback_truncated)` records one round.
`outcome` is `evaluated`, `no_progress`, `programmer_failed`, or
`programmer_waiting`. Its fields are cross-checked: a round never verified
has no QA or review, and only an evaluated round has feedback.

`LoopResult(loop_id, task_id, project_id, plan_id, analysis_id, status,
stop_reason, iterations, final_patch, final_qa_result, final_review_result,
calls, not_done, policy, metadata)`:

* **`status`**: the **last evidence**, never better. It is the final
  ReviewResult's status (`pass`/`concern`/`fail`/`inconclusive`), or
  `inconclusive` when no round was evaluated. Lifecycle reasons are kept
  apart in **`stop_reason`**: `success`, `not_retryable`, `inconclusive`,
  `max_iterations`, `call_budget`, `no_progress`, `programmer_failed`,
  `programmer_waiting`.
* `final_*` are the artifacts of the last **evaluated** round. A
  no-progress or failed final round keeps the previous round's evidence.
* `calls` = `{programmer, qa, reviewer}`; it must match the rounds.
* `not_done` = `patch_application`, `approval`, `commit`, `deployment`,
  `code_execution`, `runtime_behavior`.

Validated on construction: `pass` if and only if `stop_reason` is `success`
and the final QA and review both passed. So `QA fail + max_iterations →
pass`, `fail → concern → pass`, and `inconclusive → pass` are impossible,
even for a hand-built result. `to_dict()` is an audit summary of rounds,
identities, hashes, statuses, the feedback given, and why the loop stopped.
It contains no file contents. Only `final_patch` (the proposal itself)
holds proposed text.

## 7. Hard bounds (`LoopPolicy`)

| Bound | Default | Hard cap |
|---|---|---|
| `max_iterations` | 3 | 5 |
| `max_programmer_calls` | 3 | 5 |
| `max_qa_calls` | 3 | 5 |
| `max_reviewer_calls` | 3 | 5 |
| `max_feedback_items` | 20 | 50 |
| `max_feedback_bytes` | 4096 | 6144 |
| feedback message length | 300 (fixed) | |
| `retry_statuses` | `concern`, `fail` | may add `inconclusive`; never `pass` |

One round = exactly 1 Programmer call, 1 QA call, and 1 Reviewer call (the
Programmer itself makes at most one model generation per call). Every limit
is an integer in `[1, cap]`: `None`, `0`, negatives, floats, infinity, NaN,
and booleans are refused. There is no environment, config, CLI, or request
override.

## 8. No-progress detection

The patch's **content hash** (`Patch.content_hash`, which excludes
`patch_id`) is compared with every earlier round's hash, not just the
previous one. A repeat stops the loop with `no_progress` before QA or the
Reviewer runs again. So a regenerated patch with a new ID but the same
content is no progress, and so is an A → B → A cycle.

## 9. Security and the no-write guarantee

Proof (`tests/unit/test_loop_security.py`, `test_loop_architecture.py`):

* The static rules forbid `os`, `pathlib`, `io`, `subprocess`, `socket`,
  `time`, `threading`, `random`, `open`, `eval`, `exec`, `getattr`, `while`,
  async, and every model, agent, tool, Orchestrator, Git, storage, event,
  project-registry, snapshot, approval, apply, commit, deploy, persist,
  emit, and schedule name. Deliberate-break snippets prove the rules work.
  The only acting calls are `Programmer.program`, `QAService.verify`,
  `ReviewerService.review` (and the read `TaskEngine.get`). The service
  assigns `self.*` only in `__init__`.
* Runtime tripwires turn every write-mode `open`, filesystem mutation,
  `subprocess`, socket, new import, `eval`/`exec`/`compile`, `GitClient.run`,
  Planner, SystemAnalyst, agents, tools, Orchestrator, event bus, event
  store, and Task Engine mutator into an error. Whole runs (real Programmer
  with a scripted fake model, real QA, real Reviewer) still finish as
  `pass`, `concern`, `fail`, and `inconclusive`. Afterwards the project
  tree, task, project, plan, analysis, and final artifacts are unchanged.
* In a Git repository, status, HEAD, branches, stash, worktrees, `.git`,
  and the working tree are byte-identical.
* Network calls: 0. The Loop never selects a model or provider: model
  access stays inside the Programmer, through `ModelRouter`.
* Logs carry IDs, statuses, stop reasons, and round counts, never task
  text, feedback, patch content, or secrets.

## 10. Determinism, isolation, immutability

* Same request, policy, and component outputs give the same status,
  stop reason, and round sequence, with the same `content_hash` (which
  excludes `loop_id` and every random artifact ID). The decision uses no
  clock, randomness, network, or machine state.
* All run state lives in local variables of one `run` call. Two runs on one
  service, including after a failed run, do not affect each other.
* Request, feedback, iterations, and result are frozen at every depth.
  Caller-owned inputs and lists are copied. Earlier rounds are never
  modified when a later one fails.

## 11. CLI

`siamang loop [--json]` shows the strategy, the round, statuses, stop
reasons, limits, and `not_done`. It is read-only: it never opens the
database, reads a project, calls a model, or runs a round, and there is no
`loop run`/`apply`/`approve`/`commit`. `siamang validate` has a `Loop` check
that fails on an unbounded, applying, or approving policy.

## 12. Phase 19 boundary

The Loop has no approval state, token, or human wait, and no `approve`,
`reject`, `apply`, `commit`, or `merge`. Its output is a `LoopResult` whose
`final_patch` is a **proposed** patch, not an approved, applied, or
committed one. A loop `pass` means "QA and the Reviewer found nothing
further", not "authorized". The Approval System (Phase 19) decides that,
with its own provenance and before-hash checks.

## 13. Known limitations

* The Loop verifies the **consistency** of the chain, not the honesty of
  its components. A Reviewer or QA object that returns a well-formed,
  self-consistent result with fabricated findings is believed, because
  components are trusted by injection. Provenance belongs to Phase 19.
* Feedback quality depends on QA's and the Reviewer's static findings,
  which are judged by names and structure. Whether a model uses the
  feedback well is not verified.
* No runtime execution: a passing loop says nothing about runtime
  behaviour.
* A component refusal or chain break ends the run with an error rather than
  a partial `LoopResult`. Fail-closed, at the cost of the earlier rounds'
  record.
* Results live in memory only (no persistence or events until Phase 20/23).

## Phase 18 decisions

| # | Decision |
|---|---|
| LP1 | The Loop coordinates bounded Programmer → QA → Reviewer rounds and returns a `LoopResult`; it never applies, approves, commits, executes, or deploys |
| LP2 | Components are injected; only the Loop depends on them; Programmer, QA, and Reviewer never depend on the Loop or on each other's services |
| LP3 | Feedback goes through the Programmer's existing `ProgrammingRequest.input` as typed data under a reserved key; no Programmer, QA, or Reviewer change |
| LP4 | A finite state machine over `range(max_iterations)`; one round = one call each; budgets checked before a round starts |
| LP5 | `status` is the last evidence and never better; `stop_reason` carries lifecycle; `pass` iff `success` with QA and review pass |
| LP6 | No progress = a repeated patch content hash against every earlier round; it is never re-verified |
| LP7 | Every returned artifact is re-validated and bound to its round; reuse of any identity breaks the chain |
| LP8 | Chain breaks and component refusals raise (fail closed); outcomes of a consistent chain are results |
| LP9 | Feedback comes only from findings, is re-checked as hostile, ordered, de-duplicated, and bounded |
| LP10 | Policy defaults 3 rounds (cap 5); no unlimited value or override; `pass` never retried; `inconclusive` only if listed |
| LP11 | Stateless service; all run state local; deep immutability; deterministic content hash |
| LP12 | No persistence or events (schema v3, contract v0.2); not an Agent; not wired into the Orchestrator |
| LP13 | Read-only CLI `siamang loop [--json]` and a `Loop` check in `validate` |
