# SIAMANG HTTP API — Phase 20

Implemented on 12 October 2026. The API is a separate optional FastAPI transport
over the existing Python services. It produces engineering proposals, static QA,
review, and explicit approval decisions. For GitHub projects an approved patch becomes a commit on the
task's branch and a pull request. It never opens a shell or deploys. By default it does not merge or run project code; the
operator's file can turn on a sandbox ([SANDBOX.md](SANDBOX.md)) and policy gates up to a merge ([AUTONOMY.md](AUTONOMY.md)).

## Install and start

```sh
.venv/bin/python -m pip install -e '.[dev,api]'
.venv/bin/python -m siamang.api.main --port 8765
# After reinstalling the package, the equivalent console entry point is siamang-api.
```

Python >= 3.11; macOS/Linux (the runtime uses a POSIX file lock). The entry point
binds only `127.0.0.1`, with one worker and access logging disabled. Importing the
core does not require FastAPI. API configuration is required before startup;
missing credentials or a seal key fail closed, rather than starting anonymously.

`SIAMANG_CONFIG` selects the usual TOML config. `SIAMANG_STORAGE_PATH` overrides
the database path. The API adds versioned supplemental tables to the core SQLite
database: `api_schema` (v7), `api_autonomy_decisions`, `api_runs`, append-only `api_run_events`, `api_features`, `api_task_features`, `api_documents`, `api_design_decisions`, and `api_changes` (see [Event stream](#event-stream)). Core
schema remains v3 and lifecycle contract remains v0.2. Back up the database
before first use on an existing installation. No user database was migrated as
part of development or validation.

### Server-owned authentication and authorization

Each operator has a distinct randomly generated bearer token (at least 32
characters, preferably `secrets.token_urlsafe(48)`). Store only its SHA256 digest
in the server's identity configuration. Never place the token in a frontend
bundle, source control, URL, or query string. Token possession authenticates the
configured identity; this is local service authentication, not a multi-factor
proof of a human's identity. Revocation/rotation takes effect on server restart.

Set `SIAMANG_API_IDENTITIES` to a JSON object, for example (replace placeholders):

```json
{
  "owner": {
    "token_sha256": "<64 lowercase hex characters>",
    "permissions": ["read", "submit", "run", "cancel", "retry", "approve"],
    "projects": ["prj_replaceWithRegisteredProjectID"]
  }
}
```

Add `configure` to let an identity change which model an agent uses; nothing else needs it.

Scopes are finite project IDs. An explicit server-owned `"*"` scope allows all
projects, but there is no default wildcard or permission. Separate operator and
approver credentials are supported and recommended when needed. HTTP bodies
cannot contain `approver_id`, policy, seal key, permissions, or override fields.

Set `SIAMANG_API_SEAL_KEY` to at least 32 bytes encoded as hex. Generate once with
`secrets.token_hex(32)` and preserve it in a protected server configuration
outside the repository. The same key is required after restart to verify saved
evidence and decisions. Key replacement intentionally makes old evidence
unusable. The key is never returned by any endpoint.

Optionally set `SIAMANG_API_ORIGINS` to a JSON array of exact origins, e.g.
`["http://localhost:5173"]`. Default: no cross-origin access. Wildcards are
refused. All `/v0` endpoints require bearer authentication and their named
permission. Errors have the form `{"error":{"code":"..."}}`; request input,
token values, tracebacks, and raw domain exception messages are not echoed.
Responses use `Cache-Control: no-store`. Remote exposure/TLS, OAuth, sessions,
and rate limits are outside this local-first release.

## Model configuration

The bundled echo provider is never eligible for HTTP engineering runs and is
never a fallback. Without a configured real provider, `POST .../run` returns
503 `model_not_configured`, leaving the task queued.

An Ollama adapter supports bounded, non-streaming `/api/chat` generation and
`/api/tags` health checks. See [Ollama's chat contract](https://docs.ollama.com/api/chat).
A `claude_cli` adapter uses the operator's own logged-in CLI. See docs/MODEL_LAYER.md.

```toml
[storage]
path = "data/siamang.db"

[providers.local]
type = "ollama"
base_url = "http://127.0.0.1:11434"
timeout_secs = 120

[models.engineering]
provider = "local"
provider_model = "REPLACE_WITH_AN_INSTALLED_OLLAMA_MODEL"
capabilities = ["text_generation", "structured_output"]
priority = 10
```

Choose a model actually installed on the operator's Ollama server. No model is
downloaded automatically. HTTP is restricted to loopback; other origins require
HTTPS. URL credentials, paths, query strings, fragments, redirects, and ambient
HTTP proxies are refused/disabled. Generation is capped by timeout and response
size. Ollama calls were tested using isolated responses, not a live LLM;
inference quality and the user's model installation are not verified.

## Agent documents and the design gate

Each agent owes the outputs listed in `src/siamang/documents/catalog.py`, published by
`GET /v0/capabilities` as `documents`. `GET /v0/tasks/{id}/documents` returns what the
latest run recorded. Every document has a `source`:

| source | Written by | Rule |
|---|---|---|
| `authored` | The agent's model, one call per stage, no tools | A draft or opinion, never evidence |
| `derived` | Code, from deterministic pipeline results | Includes the QA and Review verdicts; a model cannot change them |
| `measured` | Real execution only | No model is asked. Stays `not_run` until CI exists |

`status` is `generated`, `not_applicable` (with a reason), `not_run`, `not_assigned`
(the agent has no model in `[agents.*]`), or `failed` (with the error code).

When `planner`, `analyst`, or `designer` has a model, a run stops after briefing, planning, analysis, and designing ([DESIGNER.md](DESIGNER.md))
with `wait_reason=design_approval`, before any code is proposed. `POST /v0/tasks/{id}/design-decision`
(`approve` permission, body `{"decision", "reason"}`) records the decision with a digest of the
documents, then resumes the same run or cancels the task. A model failure while writing the
design fails the task. A failure while writing later documents is recorded, and the proposal
continues to approval. The approved design reaches the Programmer clipped to its 7 KB input
budget, most binding documents first. Model-written review documents are advisory: they do
not yet feed the revision loop or block approval.

### Owner edits to the design

Each catalog entry lists in `blocks` the fenced languages the owner may edit: `mermaid` everywhere, plus `html`
in `wireframes` and `json` in `design_tokens`. Blocks are numbered over those languages only, so an example in
another language, such as a JSON response in `api_spec`, is never a block.

| Route | Permission | Meaning |
|---|---|---|
| `POST /v0/tasks/{id}/documents/{kind}/diagrams/{index}` | `approve` | Body `{"source", "base_sha256"}`. Replaces one block and keeps the earlier text in the append-only `api_document_revisions`. 409 `document_changed` when the document moved on, 422 `diagram_invalid` (empty, over 20,000 characters, a line starting with three backticks, or JSON that is not an object), 404 `diagram_not_found` |
| `GET /v0/tasks/{id}/document-edits` | `read` | What changed, per block, and the edit `mode`: `gate`, `stopped`, or `revision` |
| `POST /v0/tasks/{id}/design-edits/send` | `approve` | A stopped run goes again on the changed design; on a published task the proposals become a revision task (`submit` too) |

At the gate, approving sends the edits. Edited blocks reach the Programmer first, marked `owner_edited`, and take
precedence over the rest of the design.

## Projects: from GitHub, or new and local

`POST /v0/projects` (`submit` permission and an unrestricted `*` scope; body `name`, optional `repository` as
`owner/name` or a github.com URL) registers a project and sets it up in the background in
`<database dir>/workspaces/<project id>`, a directory Siamang owns. The project's `repository` object says which kind
it is in `kind`.

* **`github`** (a repository is given): the repository is cloned. Write access is checked first. Each Lead turn, task
  start, and publication fetches first, so new commits on the repository are picked up without a manual pull.
  Approved work reaches a `siamang/*` branch and a pull request, and CI is followed there.
* **`local`** (no repository, or a blank one): a new, empty project. Siamang runs `git init` with one README commit on
  `main`, and there is no remote: nothing calls `gh` or a network, and there is no CI. Each task still works on its own
  `siamang/*` branch. Approving the result merges it into `main` with `--no-ff` (`publication` becomes `published`,
  `pr_number` and `pr_url` stay `null`), and the folder is left on `main`. A merge that conflicts is aborted and
  reported as `merge_conflict`, never forced. A revision starts fresh from `main`, which already has the earlier work.
  The folder is the only copy, so nothing throws work away: uncommitted changes found there when a task starts or
  merges are kept with `git stash`, and ignored files such as `.env` are left alone. Execution evidence comes from the
  sandbox when it is enabled; without it, the result has none, as for any run. To publish later, add a remote in that
  folder and push `main`.

The Lead then reads a bounded view of the repository and asks what the work needs. The spec moves
through `thinking`, `gathering`, `pending_approval`, `approved`, or `failed`. `POST .../intake/messages`
answers, and `POST .../intake/approve` (`approve` permission) approves. Until then `POST /v0/tasks`
returns 409 `spec_not_approved`. A later message reopens the spec and blocks new tasks until it is approved again.

### The conversation before a task

`POST /v0/projects/{id}/drafts` (`submit`; body `content`, optional `priority`, `feature_id`, `revises_task_id`) starts a
conversation with the Lead, and no task exists yet. The draft moves through `thinking`, `need_answers`, `ready`, or `failed`.
`POST .../drafts/{draft}/messages` answers or corrects, and `POST .../drafts/{draft}/discard` drops it.
`GET .../drafts` lists open drafts. When the Lead understands the work it writes a brief, and
`POST .../drafts/{draft}/confirm` turns that brief into the task and starts it, reporting `scheduled` and `reason`
(for example `worker_busy`). Confirming twice returns the same task. Such a task skips the Lead's question round at run
start. `POST /v0/tasks` stays available for an instruction that needs no conversation.

### Asking one agent

The owner can ask any agent (`lead`, `planner`, `analyst`, `designer`, `programmer`, `qa`, `reviewer`) about a task with
`POST /v0/tasks/{task_id}/agents/{agent}/chat` (`run`), or about the project with
`POST /v0/projects/{id}/agents/{agent}/chat` (`submit`). The body is `content`, and the request answers 202 with
`state: thinking`. A `GET` on either path returns the thread: `items` (`role` is `owner` or `agent`, with `message`, or
`error` when the agent could not answer), `thinking`, and `available`, which is `false` when the agent has no model.

Asking changes nothing. The agent answers with its own model and persona from what it wrote, the task, the project spec,
and the state of the run. It has no tools, never writes a document, and is not run on the pipeline's worker, so a run can
be asked about while it is running. The thread is stored apart from the Lead's conversation, so the run never reads it.
One question per thread is answered at a time (`409 agent_busy`), and an agent without a model returns
`503 agent_has_no_model`. Each answer is metered as the agent's stage `chat`. The stream reports a new message as the part
`agent_chat`.

### Several Programmers on one task

After the design documents, the Analyst's model may divide the work in `work_packages` (a document bound to the design
approval): an optional foundation (a new project only) that runs first and alone, then packages in **waves**. The
packages of one wave run at the same time, up to `parallel` in `[agents.programmer]` (default 3, at most 6); a wave starts only
when every package of the earlier ones has passed, and its copy of the repository holds their finished code. A wave that
does not pass stops the next one. The number of packages grows with the size of the repository (4 up to 10, at most four
waves). Packages of one wave never own the same path; a later wave may own a path of an earlier one. The division carries a
`contract` (shared names, schema, routes, configuration) that every package receives, together with who else works on
the task and what the earlier waves finished. A package that times out is tried once more, then cut in two along its
owned paths, and the halves take its place in the same wave (`attempt` and `wave` are in `task.packages`; a package that cannot be
cut stops for the owner with `programmer_model_timeout`). A package owns its paths exclusively, and a stack still undecided becomes a
question to the owner before the design can be approved. Each package works on its own copy of the repository without
`.git`, through the normal Programmer, QA, and Reviewer loop; a change outside its paths is told once and then stopped. A package that
stops keeps the others' work, and the owner's answer runs only what has not passed. The patches then merge, and the same QA
and Reviewer check the whole once, with one automatic repair package if they hold it. That merged result is the
evidence the unchanged approval seals, published as one pull request with one commit per package. `task.packages` reports each package.
If the division is refused by the code or the model fails, it is asked once more and told why. A division that still fails
leaves `work_packages` with status `failed` and the real reason; `not_applicable` is only ever the Analyst's own judgement
that one Programmer is enough, with its reason. `POST /v0/tasks/{task_id}/work-division` (`run`, refused unless a design
waits at the gate) asks for the division alone and keeps every other design document. A retry of the same brief keeps a
generated division and starts its packages again as `pending`.

The division can name a new application's expected findings as notes through the Reviewer's `advisory_codes`, limited to a code-owned
allowlist that excludes security, QA, and patch findings, and recorded with the review so approval re-derives them.

Work that fits one area stays with one Programmer. A repository with no branch gets one commit with a README on `main` as the base a pull
request needs, the only push that ever reaches a default branch, and only while `git ls-remote` shows no branch.

### CI

After a push, `task.ci` follows the commit's GitHub Actions runs: `watching`, `success`, `failed` with the failed log (redacted), `none` when no
workflow ran within two minutes, or `timeout`. Jobs and artifact names and sizes come only from GitHub's report, and fill the
`measured` documents `ci_results` and `build_artifacts`. Artifacts stay on GitHub, reachable from the run page.

### When CI fails

A failed build returns to the Programmer. `[agents.programmer] ci_repair` (default 2, at most 3, 0 turns it off) bounds how many times.
Each attempt is a new task with `source=system`, the same branch and pull request, and `metadata.ci_repair` naming the attempt, the failed
task, and the root task. It skips the Lead's questions and the design gate, and runs the normal Programmer, QA, and Reviewer loop with the
failed jobs and the end of the redacted log as data under `ci_failure`.

A repair that passes is approved through the unchanged ApprovalService by the policy actor `policy.ci-repair`, which only this path uses, so
the evidence is re-derived and the record names who decided. It is committed to the open pull request's branch. Nothing merges.

A repair is held at `quality_gate` and not pushed when QA or the Reviewer holds it, or when it looks like it hides the failure: changing CI
configuration, deleting a test, adding a skip, or removing more assertions than it adds. This check is a heuristic, not proof of an honest fix.

No repair starts, and the task receives a message saying why, when the attempts are used up, the same failure returns (timestamps, hashes, and
durations do not count), the log cannot be read, or the branch moved since that build. A merged or closed pull request ends it silently. A
repair that finds the worker busy waits up to an hour; after a restart it stays queued and `POST /v0/tasks/{id}/run` starts it.

### Autonomy

`[autonomy]` in the operator's file sets which gates pass by policy and what the Lead may settle ([AUTONOMY.md](AUTONOMY.md)). Off by default.
`GET /v0/tasks/{id}/autonomy` (`read`) returns `level`, `lead`, and `items`: every sealed decision with `gate`, `risk`, `outcome`, `actor`,
`reasons`, `payload`, `evidence`, `decided_at`, and `seal_valid`. `/v0/capabilities` adds `autonomy`, reports `publishing` as `merge_by_policy` at
level 3, and lists the phase `deciding`. A question the Lead forwards may carry `lead_reason`; a message may carry `resolved`.

### When a run stops

Each task gets a `siamang/*` branch. A run that stops short of an approval says why, and
`POST /v0/tasks/{id}/messages` resumes it with `wait_reason` `clarification_required`, `quality_gate`,
`worker_interrupted`, or the legacy `input_required`. A Programmer question becomes a Lead message. `quality_gate` adds a
message listing what QA and the Reviewer still hold. Once the design is approved a resume goes straight back to coding; an
earlier stop goes through the design again. No agent writes closing documents when no patch exists. The Lead asks at most
twice per task.

The runtime selects the files the Programmer reads from the tracked list: every source file when there are at most 8, otherwise the
Analyst's model picks from that list, falling back to files the design names. This reaches the Analyst as an in-process `focus` that
`AnalysisRequest.from_dict` refuses, so an API caller still cannot choose files, and the Analyst and Programmer re-check each path.

After final approval the patch from
the sealed evidence is written to the clone only if every file still matches what the Programmer saw, committed, pushed, and
offered as a pull request. `task.branch.publication` is `none`, `publishing`, `published`, or `failed`;
`POST /v0/tasks/{id}/publish` retries a failure and does not repeat a commit that already reached the branch.

`revises_task_id` on `POST /v0/tasks` joins the base task's branch while its pull request is open.
After a merge or close, the revision uses its own branch from the default branch.

Guarantees from `siamang.repos.client`: pushes reach only `siamang/*` branches and never force. It merges only at autonomy level 3, pinned to the tested commit. Agent
documents and the spec stay in Siamang's database. The pull request carries the task text, patch
summary, file list, and static check status, so those are visible to anyone who can see the repository.
`patch_application: false` in capabilities still means nothing is applied to a registered local folder or a default branch.

## Features and portfolio

| Route | Permission | Purpose |
|---|---|---|
| `GET /v0/office` | read | Every active project with its active and three latest finished tasks, plus `current`, the task the worker is on |
| `GET /v0/portfolio` | read | Every project in scope with task counts, waiting reasons, features, repository, and spec state |
| `GET /v0/projects/{id}/features` | read | Features of a project |
| `POST /v0/projects/{id}/features` | submit | Create a feature (`title`, optional `description`) |

`POST /v0/tasks` accepts an optional `feature_id` of the same project.

## Tokens and time

Every model call and every stretch of an agent's work is recorded as it happens (API schema 8): `api_model_calls` holds one
row per call, failed calls included, and `api_agent_activity` one row per activity. Both are append-only. Nothing is
estimated. A count the provider did not report (a timeout) stays unknown, is counted as a call, and is named, never
added in as zero tokens.

| Route | Permission | Purpose |
|---|---|---|
| `GET /v0/tasks/{id}/usage` | read | The task's totals and one row per agent |
| `GET /v0/projects/{id}/usage` | read | The project's totals, one row per agent and per task, and `outside_tasks` |

Token fields, in totals and in every row: `input_tokens`, `output_tokens`, `total_tokens` (input plus output),
`cache_read_tokens`, `cache_write_tokens`, `calls`, `failed_calls`, `unreported_calls` (calls without a token count) and
`model_ms` (the calls' own durations, added up).

- **Input counts the prompt cache.** Claude reports most of a prompt as cache reads or writes: a real `claude -p` call
  reports `input_tokens: 2` beside `cache_creation_input_tokens: 1103`. `input_tokens` is everything the model read;
  `cache_read_tokens` and `cache_write_tokens` say how much of it went through the cache. For `claude_cli` the counts come
  from `modelUsage`, each model's total over every turn of the call (`--json-schema` takes two); `usage` stands in when
  `modelUsage` lacks the numbers. Ollama reports `prompt_eval_count` and `eval_count`, and has no cache to report.
- **A failed call keeps its tokens when they are known.** A reply that arrived but was unusable (invalid JSON, another
  model) still cost its tokens, and a `claude -p` error that reports usage keeps it.
- **Agent rows** (`agents`): `agent` (`lead`, `planner`, `analyst`, `designer`, `programmer`, `qa`, `reviewer`, in that
  order; `unknown` only for a call made outside any activity, which no code path does), `models` used, the token fields,
  and `active_ms`: how long the agent worked, as the union of its activities, so Programmers on parallel packages count
  once. An activity is timed around the work itself, model or not: QA and the Reviewer check without a model, and
  `verifying` (tests in the sandbox) is QA's. An agent without a model writes no documents, so that stage adds no
  time; an agent that did nothing in the task gets no row.
- **Task totals** add `work_ms` and `waiting_ms`: how long the task's runs were `running`, and `waiting` for the owner,
  from the execution events. Queueing counts toward neither. A run cut off by a restart stops counting at its last
  heartbeat, which startup records as `last_heartbeat` on the `interrupted` event. Tasks from before schema 8 have these
  too, but no tokens.
- **Drafts** belong to the task they became. The project's intake conversation and drafts that never became a task are
  `outside_tasks` (token fields and `active_ms`). A project's totals are its tasks plus `outside_tasks`.
- **`complete`** is `false` when the task or project began before recording did (`since`): its token totals miss that
  earlier work.

`GET /v0/tasks` items and `GET /v0/portfolio` items carry a short `usage`: `input_tokens`, `output_tokens`,
`total_tokens`, `calls`, `unreported_calls`, plus `work_ms` on tasks. Each new row also announces a `changed` event with
the `usage` part.

## Lifecycle, execution stage, and evidence

Lifecycle remains `queued`, `running`, `waiting`, `completed`, `failed`,
`cancelled`. Task Engine still manages state only. The API runtime owns the
proposal workflow, calling the existing Planner, Analyst, and bounded Loop.

A task that fails in a design stage has `last_error.details` with `cause`, `stage` and `attempts`.

Task snapshots add `execution` with `run_id`, `state`, `phase`, `iteration`,
`wait_reason`, `heartbeat_at`, `updated_at`, `created_at`, `activity_known`, and
`stale` when a runtime record exists. Without a runtime record, `phase=null`,
`state=unknown`, and `activity_known=false`; legacy `running` is never inferred
to mean active programming. A runtime heartbeat updates every 3 seconds; beyond
15 seconds it is stale and `activity_known=false`. Heartbeats show worker
liveness, not model token progress or completed work.

| Execution phase | Evidence/meaning |
|---|---|
| queued | An explicit run request was durably scheduled |
| planning | Planner invocation entered |
| analysis | Analyst invocation entered |
| programming | Programmer invocation entered, with iteration number |
| briefing, planning, analysis, designing | A design stage entered; see [Design stages](#design-stages) |
| recovering | The Lead steps in after a failed verification; `attempt` is the recovery round |
| qa | Static QA invocation entered |
| reviewing | Reviewer invocation entered |
| awaiting_approval | Final loop passed; explicit human decision still required |
| blocked | Quality gate failed/inconclusive, or Programmer needs input |
| interrupted | Prior worker exited before finishing, or graceful shutdown interrupted a stage |
| finished | Proposal approved; task result explicitly says patch_applied=false |
| failed | Pipeline error was recorded, with safe failure details |
| cancelled | Explicit operator cancellation or authorized proposal rejection |

### Design stages

A design stage is written as several model calls side by side: at most 3 documents per call and 3 calls at once. Each part's
documents are saved as soon as it ends. A stage gets up to 3 rounds (300, 450, then 600 seconds), and a later round repeats only
the documents that failed. A call gets the smallest of that limit, the provider's `timeout_secs` (120 when unset), and 600 seconds,
so a provider below 600 gives later rounds no extra time.

Each round is its own execution event:

| Field | Meaning |
|---|---|
| `attempt` | The round, 1 to 3; the failed event carries the last one |
| `retry_reason` | The failure that caused this round, for example `timeout` |
| `remaining` | Documents this round repeats |
| `reused` | Documents kept from an earlier run of the task |

A retry (`POST /retry`, then run) resumes from what an earlier run finished. Documents written for the same instruction,
specification, owner answers, and project identity are reused, and a generated work division is kept with its packages back at
`pending`. Any change to those writes everything again. Runs from before this record existed cannot be reused.

`task.design_parts` records each model call of the latest run's design as it happened: `stage` (`division` for the work division),
`part`, `attempt`, `kinds`, `state` (`running`, `done`, `failed`, `interrupted`), `reason`, `model_id`, `timeout_secs`, `started_at`,
and `finished_at`. Overlapping times are the evidence that parts ran side by side.

`wait_reason` differentiates `approval_required`, `quality_gate`, `input_required`,
and `worker_interrupted`. Generic waiting tasks without a runtime record remain
unknown. An active worker cannot be inferred from lifecycle alone.

No work is automatically restarted. Startup marks runtime-owned scheduled/running
attempts interrupted; running tasks move to waiting. Existing waiting approval
evidence survives restart. A POSIX database lock prevents multiple API runtime
processes from falsely treating one another's tasks as interrupted. One proposal
job runs at a time; a busy worker returns 409. Submission and execution are
separate operations. Cancellation is cooperative between stages: an already
started model call may finish before the worker stops; its proposal is discarded.

## Actual routes

| Method | Route | Permission / behavior |
|---|---|---|
| GET | `/v0/health` | read; storage health/version and observation timestamp |
| GET | `/v0/capabilities` | read; truthful model configuration, permissions, supported statuses; `execution` = `{sandbox, tools, recovery}`; phases include `verifying` and `recovering` |
| GET | `/v0/settings/models` | read; each agent's model and what can be chosen: configured models plus the tags Ollama has installed, with `cloud` true when the model is not on this machine |
| POST | `/v0/settings/models` | configure and an unrestricted `*` scope; body `agent`, `provider`, `provider_model`. Only an offered, available model is accepted. Edits the `SIAMANG_CONFIG` file in place, adding a `[models.*]` table for a new Ollama tag; the agent's next model call uses it, no restart |
| GET | `/v0/settings/documents` | read; every catalog document with `enabled` and `default_enabled`. Off by default: WBS, complexity, acceptance criteria, dependencies, technical design, UI spec, user flow, accessibility, implementation notes, coverage report, CI results, build artifacts, QA verdict |
| POST | `/v0/settings/documents` | configure and an unrestricted `*` scope; body `kind`, `enabled`. Stored in the database and applied from the next run on: a switched-off document is neither asked of a model nor recorded. `/v0/capabilities` also carries `enabled` per document |
| GET | `/v0/settings/github` | read; `installed`, `connected`, `stored` (a token was saved from Settings), `source` (`siamang` for that token, `gh` for the owner's own gh login, or null), `login`, `name`. Runs `gh api user`; never returns the token |
| POST | `/v0/settings/github` | configure and an unrestricted `*` scope; body `token` (`ghp_`, `gho_` or `github_pat_`). Checked with `gh api user` before it is kept in `~/.config/siamang/github-token` (0600, `SIAMANG_GITHUB_TOKEN_FILE` overrides). From then on every `gh`/`git` call gets it as `GH_TOKEN`. A refused token is `github_token_invalid` (422) |
| POST | `/v0/settings/github/disconnect` | configure and `*`; deletes the stored token, so Siamang falls back to the owner's gh login. The token is not revoked on GitHub |
| GET | `/v0/settings/claude` | read; `installed`, `logged_in`, `method`, `email`, `plan`, `expires_at` from `claude auth status`, plus `login.state` (`idle`, `waiting`, `done`, `failed`) and `login.url`, the fallback link `claude auth login` printed |
| POST | `/v0/settings/claude/login` | configure and `*`; starts `claude auth login --claudeai` on the server machine, which opens the browser there. One at a time (`claude_login_running`, 409), killed after 5 minutes. There is no logout: that login is shared with the owner's Claude Code |
| POST | `/v0/settings/claude/code` | configure and `*`; body `code`, the "Kode autentikasi" claude.ai shows after login. It is written to the waiting `claude auth login` as a pasted line, then GET shows `done` or `failed`. `claude_code_invalid` (422), `claude_login_not_running` (409); the code is never returned |
| GET | `/v0/openapi.json` | read; generated OpenAPI contract, bearer security scheme |
| GET | `/v0/stream?after=0` | read; Server-Sent Events for the projects in scope, see [Event stream](#event-stream) |
| GET | `/v0/projects` | read; scoped registered projects, local root paths withheld |
| GET | `/v0/projects/{project_id}` | read; scoped project |
| GET | `/v0/projects/{project_id}/usage` | read; tokens and time per agent and per task, see [Tokens and time](#tokens-and-time) |
| GET | `/v0/tasks?project_id=...&status=...&limit=100&offset=0` | read; lifecycle + execution snapshots, with a short `usage` |
| POST | `/v0/tasks` | submit; persist a task, no automatic execution |
| GET | `/v0/tasks/{task_id}` | read; task, latest execution record, `packages`, and `design_parts` |
| GET | `/v0/tasks/{task_id}/usage` | read; the task's tokens and time per agent, its draft conversation included |
| GET | `/v0/tasks/{task_id}/events?after_seq=0&limit=100` | read; lifecycle envelope replay |
| GET | `/v0/tasks/{task_id}/execution-events?after=0&limit=100` | read; persisted real stage transitions, oldest first; page with `after` to reach the newest |
| POST | `/v0/tasks/{task_id}/run` | run; schedule queued task, 202 after storage succeeds |
| POST | `/v0/tasks/{task_id}/cancel` | cancel; explicit cooperative cancellation |
| POST | `/v0/tasks/{task_id}/retry` | retry; counted requeue only, no execution |
| POST | `/v0/tasks/{task_id}/work-division` | run; 202, asks for the work division again and keeps every other design document; 409 `design_not_pending` unless a design waits at the gate |
| GET | `/v0/tasks/{task_id}/reports` | read; plan, analysis, loop, patch, static QA, review, plus (each `null` when the run did not record it) `verification`, `recovery`, `programmer_sandbox` |
| GET | `/v0/approvals?project_id=...` | read; waiting proposals with complete evidence and eligibility |
| POST | `/v0/approvals/{run_id}` | approve; explicit approve/reject through ApprovalService |

List limits are 1–100. No fake progress percentage is advertised. Lifecycle events
use per-task `seq`; execution events have their own `position` cursor; the event
stream has a third. Consumers must not merge these cursors.
Reports are sanitized display data; their original sealed artifacts remain
server-side. Missing artefacts are null/absent, not invented. Static QA/review
`pass` does not claim runtime code correctness.

## Event stream

`GET /v0/stream` is Server-Sent Events: one way, over the same bearer token and `read` permission as every other route.
Commands stay on HTTP. The stream says what committed and in what order. It does not replace the routes: a client that
misses it entirely, or never opens it, can still read everything by polling.

```
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/v0/stream
```

| Event | `id` | Data | Meaning |
|---|---|---|---|
| `ready` | yes | `cursor`, `replay` | First on every connection. `replay: false` means changes may have been missed: reload. It can come again later if the reader falls behind what is kept |
| `lifecycle` | yes | `project_id`, `task_id`, `event` | A contract envelope, the same one `/tasks/{id}/events` returns |
| `execution` | yes | `project_id`, `task_id`, `event` | A stage transition, the same row `/tasks/{id}/execution-events` returns |
| `changed` | yes | `project_id`, `task_id` or null, `parts` | Something without an event of its own changed. Reload it from its route |
| `heartbeat` | no | `run_id`, `task_id`, `project_id`, `heartbeat_at` | The worker is alive. Only when the value changes, about every 3 seconds per running run |
| `ping` | yes | `at` | Nothing else to say. Every 10 seconds, so a client can tell a quiet stream from a dead one |

`parts` of `changed`: `task` (`/tasks/{id}`: packages, design parts, branch, CI), `documents`, `messages` (with no task, the project's
intake), `reports` (with `/approvals`), `autonomy`, `drafts`, `project` (`/portfolio`, `/projects`, `/projects/{id}/intake`), `usage`
(`/tasks/{id}/usage` and `/projects/{id}/usage`; with no task, the intake or a draft). Ignore event types and parts you do not know.

**Resuming.** A browser's `EventSource` reconnects by itself and sends `Last-Event-ID`; other clients can send that header or
`?after=`, and the header wins. Everything after that position is replayed in commit order, with no gap and no repeat. With no
cursor, the stream starts at the newest change. A cursor the server cannot honor (older than what is kept, or ahead of the log)
gets `replay: false`, then follows from the newest change.

**Truthfulness.** SQL triggers append each change to `api_changes` inside the writer's own transaction, so a rolled-back write
is never announced, no call site can forget to announce one, and writes from the CLI are included. Task state and run phase have no
trigger of their own because they always commit with an event. The log keeps about the last 10,000 changes and prunes itself.

**Scope.** Each frame goes only to a principal allowed that project. A change whose project cannot be determined is withheld
from everyone, including a `*` principal. Frames pass through the same sanitizer as the routes.

**Limits.** At most 8 streams at once; beyond that, and during shutdown, the route answers 503 `stream_unavailable`.
A slow reader falls behind in the database, not in memory. Model tokens are not streamed. A change arrives within about 0.2 seconds of its
commit, plus a reader's own reload.


Task submission body (strict, extra fields refused):

```json
{"project_id":"prj_example","description":"Describe the engineering task",
 "request_id":"req_uniqueForThisSubmission","priority":"normal"}
```

Reuse `request_id` to retry the same submission; a duplicate returns the same
task and `duplicate=true`. A changed project/description with that ID is refused.
`source=api` is assigned by the server. Neither successful submission nor 202
run acceptance means a proposal has completed.

Approval body: `{"decision":"approve","reason":"Reviewed the evidence."}`
or `{"decision":"reject","reason":"Explain rejection."}`. Reject requires a
reason. There is no default decision or bypass. The server loads its own sealed
artefacts, reconstructs only allowlisted dataclasses, and invokes the existing
ApprovalService's revalidation, provenance checks, and static re-derivation.
It never accepts a client-supplied patch, QA pass, reviewer result, or seal.

Decision persistence and task transition share one transaction. Repeating the
same decision/body by the same authenticated identity is idempotent; a different
decision returns 409. Evidence and decision records have contextual HMACs using
the persistent server key; altered/cross-run records are refused. This does not
introduce a patch executor, expiry/consumption semantics for execution, or a claim
that producer identity and on-disk before-state were proven by Approval.

## Validation

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/smoke_http.py
```

The real HTTP smoke starts/stops its own loopback server, uses a temporary
database and generated test identity, and verifies health, unauthorized access,
durable submission, queued status, fail-closed model readiness, and OpenAPI.
API tests additionally exercise the real proposal chain with a scripted model,
stage observation, cancellation, quality gates, explicit approval/rejection,
restarts, tampering, project scope, bounded inputs, and idempotency. Mock model
responses are confined to tests; production never injects them.

Application startup/shutdown follows FastAPI's
[lifespan mechanism](https://fastapi.tiangolo.com/advanced/events/).
