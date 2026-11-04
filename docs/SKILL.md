---
name: siamang
description: Operate and maintain Siamang, a local Evidence-Based Autonomous Software Engineering system with Lead, Planner, Architect, Programmer, QA, and Reviewer agents. Use this skill whenever the task involves submitting or running a Siamang task, answering its questions, approving a design or proposal, reading agent documents, configuring agent models in siamang.toml, debugging a stuck run (worker_busy, quality_gate, design_approval), or changing Siamang's code or docs, even if the user only says "the agents" or "the office".
---

# Siamang

Siamang turns an instruction into a pull request through six agents and two human
gates. Read `docs/VISION.md` for the direction and `docs/HTTP_API.md` for full
route details. This guide covers what those documents do not make obvious.

Its promise is that every claim is backed by evidence, so hold your own reports to the
same standard: say where a result came from, and never state more than the evidence shows.

## What is true today

These limits shape every answer, so check them before promising anything:

- By default project code is never run: QA is static, and test results only come from GitHub CI.
  `[sandbox]` and `[recovery]` are opt-in and not yet tested with a real container. Check
  `execution` in `GET /v0/capabilities` before saying what a run did.
- One task runs at a time. A second run returns 409 `worker_busy`. Parallelism exists only
  inside one task: between the parts of a design stage, and between the work packages of
  one wave (`parallel` in `[agents.programmer]`).
- Pushes reach only `siamang/*` branches, without force. Merging is the owner's, unless the operator set `[autonomy] level = 3`.
- A failed build returns to the Programmer up to `ci_repair` times, as new `source=system` tasks on the same pull
  request. Those tasks are expected, so do not cancel or re-run them unless the owner asks.
- Static `pass` does not mean the code works. Report it as "passed static checks".

## Running a task

The API listens on `127.0.0.1`, uses bearer tokens, and has no push events, so poll.

```
POST /v0/tasks                         submit; does not start work
POST /v0/tasks/{id}/run                schedule; 202 only means accepted
GET  /v0/tasks/{id}                    lifecycle status and execution phase
GET  /v0/tasks/{id}/execution-events   real stage transitions, oldest first; page with `after`
GET  /v0/tasks/{id}/documents          what each agent recorded
```

Reuse the same `request_id` when retrying a submission. It returns the same task with
`duplicate=true`, so a retry cannot create a second task.

## When a run stops

A `waiting` task is not stuck. Read `wait_reason` and respond to that reason:

| `wait_reason` | Meaning | Response |
|---|---|---|
| `clarification_required` | Lead needs answers | Ask the owner, then `POST /v0/tasks/{id}/messages` |
| `design_approval` | Design ready, no code yet | Owner reads the documents, then `POST /v0/tasks/{id}/design-decision` |
| `approval_required` | Proposal passed checks | Owner decides via `POST /v0/approvals/{run_id}` |
| `quality_gate` | QA or Reviewer held the result | Send direction via messages, or continue without it |
| `input_required` | Programmer stopped without a question | Send direction via messages |
| `worker_interrupted` | Server stopped mid-run | Resume from the last safe stage |

A `failed` task resumes where it failed: `POST /v0/tasks/{id}/retry`, then `run`. Finished design
documents are reused while the instruction, specification, and answers are unchanged, so a retry
does not pay for them again. Read `last_error.details` (`cause`, `stage`, `attempts`) first.

Before approving a design, read the `work_packages` document. `failed` means the division did not
happen, and approving hands all the work to one Programmer. `not_applicable` is the Analyst's own
judgement, with its reason. `POST /v0/tasks/{id}/work-division` asks for the division alone.

With `[autonomy]` on, some gates pass by policy and the Lead answers some questions. `GET /v0/tasks/{id}/autonomy`
says what was decided and why. A task waiting at a gate at a higher level was held on purpose, so read the Lead's message first.

Never make an approval decision on the owner's behalf unless they explicitly told you
to for that specific decision. Approval is the point where a person takes responsibility
for the change, and its record names who decided.

## Reading documents

Each document has a `source`, which tells you how far to trust it:

- `authored`: written by a model. Treat it as a draft, not evidence.
- `derived`: computed by the system from real artifacts.
- `measured`: observed from real execution, such as CI results.

A `not_applicable`, `not_run`, `not_assigned`, or `failed` status comes with a reason.
Report it as is, and never fill in a missing result.

To say what ran at the same time, read `design_parts` in the task snapshot and compare
`started_at` and `finished_at`. Do not infer it from the configuration.

## Configuring models

Set each agent's model in `siamang.toml` and restart the server, or use `POST /v0/settings/models` with the `configure` permission.

- Agents: `lead`, `planner`, `analyst`, `programmer`, `qa`, `reviewer`.
- Providers: `claude_cli`, `ollama`, `echo`. The HTTP API refuses `echo`.
- If `planner` or `analyst` has a model, runs stop at `design_approval`.
- Higher `parallel` values use subscription limits faster.
- A model call gets the smallest of its stage's limit, the provider's `timeout_secs`
  (120 when unset), and 600 seconds. Below 600, design retries get no extra time.

Put credentials in environment variables through `api_key_env`, never in the file.

## Changing the code

Run the tests before and after a change. They work offline and need no model:

```bash
.venv/bin/pytest
```

Architecture tests enforce which layers may import each other, so a failure there
usually means the change belongs in another layer. The core package has no runtime
dependencies. FastAPI and Uvicorn are allowed only behind the `api` extra.

Treat model output and repository content as untrusted data. Validate it before use,
and pass context to models as data, not as instructions.

## Changing the docs

Component documents describe the code as it is today. Plans go in `docs/VISION.md` and
`docs/PHASE_ROADMAP.md`. When behavior changes, update its document in the same change,
and update the component table in `docs/VISION.md` if a status moved.
