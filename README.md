<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/images/readme/hero-en-dark.svg">
    <img src="./docs/images/readme/hero-en-light.svg" alt="Siamang: autonomous engineering, every claim backed by evidence. An AI engineering team that plans, writes code and opens pull requests, and shows where every result came from." width="100%">
  </picture>
</p>

<p align="center">
  <a href="./docs/README.md"><img alt="docs" src="https://img.shields.io/badge/docs-read-1b7f52?style=flat-square&labelColor=12261d"></a>
  <a href="./CHANGELOG.md"><img alt="version" src="https://img.shields.io/badge/version-v0.3.0_early-1b7f52?style=flat-square&labelColor=12261d"></a>
  <a href="./LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-1b7f52?style=flat-square&labelColor=12261d"></a>
  <a href="./pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.11+-1b7f52?style=flat-square&labelColor=12261d"></a>
  <a href="https://github.com/Rafie93/siamang-ai/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Rafie93/siamang-ai?style=flat-square&labelColor=12261d&color=1b7f52"></a>
  <br/>
  <a href="https://github.com/Rafie93/siamang-ai/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Rafie93/siamang-ai/ci.yml?branch=main&style=flat-square&label=CI&labelColor=12261d"></a>
  <a href="#web-interface"><img alt="web UI" src="https://img.shields.io/badge/web_UI-3D_office-4285a8?style=flat-square&labelColor=12261d"></a>
  <a href="https://code.claude.com/docs"><img alt="Claude CLI" src="https://img.shields.io/badge/model-Claude_CLI-c15f3c?style=flat-square&labelColor=12261d"></a>
  <a href="https://ollama.com/"><img alt="Ollama" src="https://img.shields.io/badge/model-Ollama-5b6b63?style=flat-square&labelColor=12261d"></a>
</p>

<p align="center">
  <b>English</b> · <a href="./README_ID.md">Bahasa Indonesia</a>
</p>

<p align="center">
  <a href="#overview">Overview</a> ·
  <a href="#the-team">The Team</a> ·
  <a href="#demo">Demo</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#web-interface">Web Interface</a> ·
  <a href="#cli">CLI</a> ·
  <a href="#http-api">HTTP API</a> ·
  <a href="#whats-new">What's New</a> ·
  <a href="#features">Features</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#development">Development</a>
</p>

## Overview

Siamang is an AI engineering team that takes a software task, plans it, writes code for a real repository, checks it, and opens a pull request once you agree. You are not asked to trust what an agent says, because every result shows where it came from.

It stands alone: no external control plane, and the core uses nothing beyond the Python standard library.

> [!IMPORTANT]
> **v0.3.0 is early.** Siamang runs locally for one operator and is not ready for the internet. Full autonomy is still in progress. In the default configuration two gates need your approval and Siamang does not run project code, so test results come only from GitHub CI. See [Features](#features).

## The Team

Seven agents, each with its own desk in the 3D office and its own set of documents. The order between them is decided by code, not by a model.

| # | Agent | Role | What it produces |
|---|---|---|---|
| 1 | **Amang** | Lead | Reads the repository, asks you questions, writes the brief and task specification, assigns work, reports status |
| 2 | **Planner** | Planning | PRD, roadmap, WBS, task list, priorities, complexity, dependencies, acceptance criteria |
| 3 | **Architect** | System analyst | SRS, architecture, use case, class, ERD and sequence diagrams, API specification, technical design, work packages |
| 4 | **Designer** | UI/UX | Design tokens, HTML wireframes per screen, screen specification, user flow, sitemap, accessibility |
| 5 | **Programmer** | Coding | Source code, file structure, migrations, first unit tests, the patch |
| 6 | **QA** | Quality | Test plan and cases, test results, coverage, bug report, the QA verdict |
| 7 | **Reviewer** | Code review | Review report, architecture compliance, security, performance and maintainability findings, the verdict |

Together that is 53 kinds of document. You can turn off the ones you don't need in [Settings](#settings-in-the-interface) to save tokens.

<a href="./docs/images/readme/office-7-agents.png">
  <img src="./docs/images/readme/office-7-agents.png" alt="The Office page connected to a live backend: seven agents at their desks in a 3D room, beside the team list with each agent's status. Amang is waiting, Planner, Architect and Designer are finished, and Programmer, QA and Reviewer are on standby while the design waits for approval." width="100%">
</a>

<p align="center"><sub>A real run waiting at the design gate: the design agents are finished, and coding waits for your approval.</sub></p>

## Demo

These screenshots come from real runs with Claude models. The interface text is in Indonesian.

### How it works: the team divides the work

<a href="./docs/images/readme/cara-kerja-membagi-part.png">
  <img src="./docs/images/readme/cara-kerja-membagi-part.png" alt="The Architect panel during a real run: design documents split into three parts running at the same time. Part 2 is finished after 2 minutes 17 seconds, and parts 1 and 3 are still being written after 2 minutes 28 seconds." width="100%">
</a>

Instead of one long model call, the design documents are split into parts that run at the same time. Click any agent to see what it is doing now: which documents each part is writing, how long it has been running, and what is finished. The line "3 parts running at the same time" is computed from the times the backend records, not assumed.

### Artifacts: diagrams drawn on the page

<a href="./docs/images/readme/artifacts-diagram.png">
  <img src="./docs/images/readme/artifacts-diagram.png" alt="The Artifacts page: documents grouped by agent on the left, with a count of diagrams per document. The Architect's Class Diagram is open and drawn on the page, with an Edit diagram button and a table of contents on the right." width="100%">
</a>

Every agent's output sits in one list, grouped by agent, beside a reader. Search covers titles and text, and filters pick diagrams, UI designs or text. Mermaid diagrams are drawn on the page, and **Edit diagram** opens the code beside a live preview. Each document says where it came from: *Tulisan model · claude-opus* means it was written by a model, so it is a draft, not evidence.

### Artifacts: the Designer's wireframes and tokens

<table>
  <tr>
    <td width="50%"><a href="./docs/images/readme/artifacts-wireframe.png"><img src="./docs/images/readme/artifacts-wireframe.png" alt="The Designer's Wireframe per Layar document: 14 screens, the home page drawn at desktop width with a switch for phone width, and an Edit wireframe button."></a></td>
    <td width="50%"><a href="./docs/images/readme/artifacts-tokens.png"><img src="./docs/images/readme/artifacts-tokens.png" alt="The Designer's Design System / Tokens document: colour tokens shown as swatches with their hex values, and an Edit token button."></a></td>
  </tr>
  <tr>
    <td align="center"><sub>HTML wireframes at desktop and phone width</sub></td>
    <td align="center"><sub>Design tokens as swatches</sub></td>
  </tr>
</table>

The Designer draws each screen as static HTML using the design tokens. Wireframes render in a sandboxed frame that runs no script and loads nothing. You can edit a wireframe or the tokens beside a live preview; at the design gate, approving names what you changed, and the Programmer builds from the approved version.

## Why Siamang

Coding agents often report "all tests pass". In Siamang, a model cannot make that claim.

| Principle | Today | Planned |
|---|---|---|
| **Every result shows its origin** | Documents are labelled `authored` (written by a model), `derived` (computed by the system) or `measured` (a real measurement) | Also for every agent action |
| **A model cannot claim a pass** | Pass status comes from deterministic checks, and test results from CI, or from the sandbox when it is on | Each acceptance criterion matched to a check |
| **Decisions are bound to evidence** | Approval recomputes the evidence chain and refuses anything that changed | Automatic decisions recorded the same way |
| **Failure means repair, within limits** | A failed design stage retries up to three rounds, a timed-out package splits in two, and failed CI returns to the Programmer; all bounded and recorded | Replanning that really changes the steps |
| **Autonomy can be audited** | Two gates need a human by default; risk-based gates can be turned on, and their decisions are recorded in the policy's name | Levels per project |

### How a task runs

```mermaid
flowchart LR
  T[Instruction] --> L[Amang / Lead]
  L --> P[Planner] --> A[Architect] --> D[UI/UX Designer]
  D --> G1{Design approval}
  G1 --> C[Programmer]
  C --> Q[Static QA] --> R[Reviewer]
  R -->|held| C
  R -->|passed| G2{Final approval}
  G2 --> PR["siamang/* branch and PR"]
  PR --> CI{GitHub CI}
  CI -->|failed, bounded| C
```

## Quick Start

**You need:** macOS or Linux, Python 3.11+ and Git 2.36+. The web interface also needs Node.js 20.19+. To work on GitHub repositories, log in with [`gh`](https://cli.github.com/) using an account that can write to them.

### 1. Install

```bash
git clone https://github.com/Rafie93/siamang-ai.git
cd siamang-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[api]'
```

### 2. Choose a model

Copy one template to `siamang.toml`, which stays out of Git:

| Model | Command | Before you start |
|---|---|---|
| Claude | `cp siamang.claude.example.toml siamang.toml` | Log in with the [`claude`](https://code.claude.com/docs) CLI. For personal use. |
| Ollama | `cp siamang.ollama.example.toml siamang.toml` | Run [Ollama](https://ollama.com/), and set `provider_model` to a tag from `ollama list`. |

Then check it:

```bash
export SIAMANG_CONFIG="$PWD/siamang.toml"
siamang health            # configuration, storage and database
siamang models --check    # exits with an error if the model provider is not ready
```

### 3. Choose how to work

| Way | Good for | Runs the agents? |
|---|---|---|
| [Web interface](#web-interface) | Everyday use: start here | Yes |
| [HTTP API](#http-api) | Scripts and integrations | Yes |
| [CLI](#cli) | Inspecting and managing what is stored | No |

## Web Interface

```bash
cd web
npm install
npm run dev               # http://127.0.0.1:5180
```

This one command also starts the backend, so you don't need to start the API server. Stop everything with `Ctrl+C`. For a production build, run `npm run build && npm start` instead.

The Office page is shown in [The Team](#the-team) above. To look around without a model or a repository, choose **▶ Mode demo** there; demo mode is always labelled as a simulation.

### Your first task

The interface text is currently in Indonesian, so the button names below are shown as they appear.

1. Check that the top bar says **Backend tersambung** (backend connected). In **Settings**, check your GitHub and Claude accounts and pick a model for each agent.
2. Open **Projects**, choose **Proyek baru** (new project), paste a GitHub URL such as `https://github.com/OWNER/REPO`, and choose **Buat proyek** (create project).
3. Amang reads the repository and asks questions. Answer them on the project page, then choose **Setujui spesifikasi** (approve the specification).
4. Open **Command**, write what you want, and choose **Kirim ke Amang** (send to Amang). Check the summary, then choose **Setuju, buat task dan mulai** (agree, create the task and start).
5. Follow the work in **Office** or **Tasks**. When the design is ready, read it in **Artifacts** and choose **Setujui desain** (approve the design) on the task.
6. When the result is ready, read the evidence in **Reports**, then choose **Approve** in **Approvals**. Siamang creates a commit on a `siamang/*` branch and opens a pull request. Merging stays with you.

### What's in it

| Area | What it does |
|---|---|
| **3D office** | Seven agents, each with a desk and an animated monitor. Orbit, zoom, follow the activity, or click an agent to see its work. |
| **Handoffs** | Each recorded phase change plays as a handoff between agents. An agent is shown working only when the backend reports fresh activity, never when offline or stale. |
| **Two-way command** | Amang checks your instruction before a task exists, and a stopped task explains why and can continue with your answer. |
| **Ask any agent** | Click an agent to ask it about its own work in the current task. It answers from its documents; it does not change them. |
| **Artifacts** | Each agent's output (documents, diagrams, wireframes, tokens) with its origin: written by a model, computed by the system, or measured. Search, filters, a table of contents, and editors for diagrams, wireframes and tokens. |
| **Tokens and time** | Input and output tokens and working time per project, task and agent, including failed calls. |
| **Evidence and approvals** | Checks with exit codes, recovery attempts and technical debt when the sandbox is on. Without it, the page says the code was not run. |
| **Demo mode** | A simulated run for presentations, always labelled and kept out of Tasks, Reports and Approvals. |
| **Accessibility** | A text team list and log, reduced motion, native keyboard-friendly controls, and a 2D fallback without WebGL. |

### Settings in the interface

The **Settings** page has four sections.

| Section | What you set |
|---|---|
| **Akun** (accounts) | The GitHub account used to clone, push and open pull requests: your `gh` CLI login, or a token pasted here. The Claude login the models use, with its plan. Tokens stay on the local server and are never sent back to the browser. |
| **Dashboard** | 3D office on or off (off shows the team as a conversation, lighter without a GPU), 3D quality, work animations, and demo mode. Saved in this browser. |
| **Model per agen** (model per agent) | Which model each of the seven agents uses, Claude or Ollama. Saved to `siamang.toml` and used from the next model call, without a restart. |
| **Dokumen per agen** (documents per agent) | Which of the 53 documents each agent writes. A document turned off is not requested from the model and not recorded, which saves tokens. Applies to all projects from the next run. |

<table>
  <tr>
    <td width="50%"><a href="./docs/images/readme/settings-models.png"><img src="./docs/images/readme/settings-models.png" alt="Settings, Model per agen: a model picker for each of the seven agents, here set to local Ollama models."></a></td>
    <td width="50%"><a href="./docs/images/readme/settings-documents.png"><img src="./docs/images/readme/settings-documents.png" alt="Settings, Dokumen per agen: one tab per agent with a count of active documents, and a switch for each document. 38 of 53 are on."></a><br/><br/><a href="./docs/images/readme/settings-accounts.png"><img src="./docs/images/readme/settings-accounts.png" alt="Settings, Akun: GitHub connected through the gh CLI with an optional token field, and the Claude login with its plan and a Login ulang button."></a></td>
  </tr>
</table>

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `5180` | Port of the interface |
| `WEB_DATA` | `~/.siamang/web` | Its database, seal key and project clones. Keep it outside this repository. |
| `SIAMANG_ROOT` | the parent folder | Where the backend is |
| `WEB_SANDBOX` | off | `1` adds a sample project, which the end-to-end tests need |

## CLI

The CLI inspects and manages what Siamang has stored. **It does not run the agents:** to start work, use the web interface or the HTTP API. It reads the configuration from `SIAMANG_CONFIG` or `--config`, so run this first in each new terminal:

```bash
source .venv/bin/activate
export SIAMANG_CONFIG="$PWD/siamang.toml"
```

| Command | What it does |
|---|---|
| `siamang health`, `siamang version` | Check configuration, storage and database; show versions |
| `siamang validate` | Validate configuration, database schema and contract |
| `siamang models --check` | List models and check that their provider is ready |
| `siamang projects` | List projects; add `--all` for archived ones and `--json` for scripts |
| `siamang projects register --name NAME --path PATH --type web` | Register a local repository |
| `siamang projects get\|update\|archive\|restore` | Manage a project |
| `siamang git inspect\|status\|diff\|worktrees PROJECT_ID` | Read-only Git views of a project |
| `siamang tasks --project PROJECT_ID` | List tasks; filter with `--status` |
| `siamang tasks get TASK_ID` | Read one task |
| `siamang tasks cancel\|retry TASK_ID --yes` | Cancel a task or queue it again; nothing is executed |
| `siamang tasks recovery` | List active tasks, to see what may need recovery |
| `siamang replay TASK_ID`, `siamang events` | Read stored events |
| `siamang tools`, `siamang orchestrator`, `siamang planner`, `siamang qa`, `siamang approval`, ... | Show catalogs and policies; nothing is executed |

Run `siamang --help` for everything, or `siamang COMMAND --help` for one command.

> [!NOTE]
> The web interface keeps its own database, separate from the one in `siamang.toml`. To read its projects and tasks from the CLI, point the CLI at it. This works while the interface is running:
>
> ```bash
> SIAMANG_STORAGE_PATH="$HOME/.siamang/web/office.db" siamang projects
> ```

## HTTP API

Use this for scripts. Unlike the web interface, you start the server yourself, and it needs local credentials. It binds to `127.0.0.1` only.

```bash
python3 scripts/init-credentials.py    # creates ~/.config/siamang/local.env and prints SIAMANG_TOKEN once: keep it
./scripts/run-local.sh --port 8765
```

In another terminal, run the `export SIAMANG_TOKEN=...` line printed above, then:

```bash
api() { curl -s -H "Authorization: Bearer $SIAMANG_TOKEN" -H 'Content-Type: application/json' "http://127.0.0.1:8765/v0$1" "${@:2}"; }

api /capabilities    # confirms the server and model are ready
api /projects -d '{"name": "demo", "repository": "OWNER/REPO"}'    # Siamang clones it into its own directory
```

A task then moves through these calls:

| # | Step | API call |
|---|---|---|
| 1 | Amang reads the repository and asks; answer, then approve the specification | `POST /projects/{id}/intake/messages`, `POST /projects/{id}/intake/approve` |
| 2 | Describe the work; Amang writes a brief and you confirm it | `POST /projects/{id}/drafts`, `POST /projects/{id}/drafts/{draft}/confirm` |
| 3 | Planner, Architect, and Designer write design documents; the run stops at the first gate | `GET /tasks/{id}/documents`, `POST /tasks/{id}/design-decision` |
| 4 | Programmer, QA and Reviewer work in a bounded loop; the run stops at the second gate | `GET /tasks/{id}/reports`, `GET /approvals?project_id={id}` |
| 5 | Once you agree, Siamang recomputes the evidence and creates the commit and PR | `POST /approvals/{run_id}` |
| 6 | Siamang follows CI; a failure returns to the Programmer, within limits | `GET /tasks/{id}` (`task.ci`) |

Both decisions use the same body. In the default configuration, merging stays with you.

```bash
api /tasks/TASK_ID/design-decision -d '{"decision": "approve", "reason": "The design fits."}'
api /approvals/RUN_ID -d '{"decision": "approve", "reason": "I checked the evidence."}'
```

The full reference is in [docs/HTTP_API.md](docs/HTTP_API.md), and the OpenAPI contract is at `GET /v0/openapi.json`.

## What's New

### v0.3.0

Not tagged as a GitHub release yet. Details are in the [changelog](./CHANGELOG.md).

- **Seven agents**: a UI/UX **Designer** joins between the Architect and the design gate, with design tokens, HTML wireframes, user flow, sitemap and accessibility notes.
- **Artifacts page**: the Documents page rebuilt as a list grouped by agent beside a reader, with search, filters, a table of contents, and editors for diagrams, wireframes and tokens.
- **Settings**: GitHub and Claude accounts, a model per agent, and documents per agent that can be turned off to save tokens.
- **Tokens and time** per project, task and agent, and **questions to any agent** about its own work.
- **Local projects**: start from nothing, without GitHub or CI; Siamang keeps its own Git repository and merges into `main` on approval.
- **Sandbox execution** (opt-in): tests and builds run in a container, and that result decides whether a run passes. Not yet tested with a real container.
- **Programmer tools** (opt-in): the Programmer edits and runs commands inside the sandbox before proposing a patch.
- **Recovery Engine** (opt-in): three tiers of bounded recovery when delivery fails. Not yet applied to parallel-package runs.
- **Autonomy levels and autonomous Lead** (opt-in): risk-based gates, recorded in the policy's name. Not yet tested with a real model, container and GitHub together.
- **Work division**: design documents and work packages run in parallel within a task, with bounded retries, and the record shows what really ran at the same time.
- **Automatic CI repair**: a failed build on an approved, open pull request returns to the Programmer, a limited number of times.
- **Web interface in one repository**: rebuilt without a commercial template, and now lives in `web/` with the same version as the backend.

## Features

| Capability | Status | Notes |
|---|---|---|
| Lead, Planner, Architect, Designer, Programmer, QA, Reviewer | Available | Stage order is decided by code |
| `siamang/*` branches, PRs and reading GitHub CI | Available | No force push; no merge except at autonomy level 3 |
| Automatic repair when CI fails | Available | Only on an approved, open PR; `ci_repair = 0` turns it off |
| Parallel work packages | Available | So far tested only with a fake model |
| Tests and builds in a sandbox | Opt-in, off by default | Not yet tested with a real container |
| Programmer with tools | Opt-in, off by default | Not yet tested with a real container |
| Three-tier Recovery Engine | Opt-in, off by default | Not yet applied to parallel-package runs |
| Autonomy levels and autonomous Lead | Opt-in, off by default | Not yet tested with a real model, container and GitHub; [docs/AUTONOMY.md](docs/AUTONOMY.md) |
| Matching acceptance criteria to checks | Not yet | Today every criterion is `not_verified` |
| Several tasks at once | Not yet | One task runs at a time |
| Multi-user login, TLS, GitHub App | Not yet | Do not expose the API to the internet |

**Benchmarks:** no publishable results exist yet, so this README states no figures for quality, cost or time. The plan is in [Phase 32.5](docs/PHASE_ROADMAP.md#phase-325-benchmark-kesiapan-rilis). The tests here run offline with a fake model: they prove how the system behaves, not the quality of the code a model writes.

## Security and Limits

- Pushes go only to `siamang/*` branches, without force. A merge happens only if the operator chooses autonomy level 3.
- In the default configuration the first change always waits for your approval. Automatic CI repair only enters a PR you approved that is still open.
- A CI repair is held if it appears to hide the failure, for example by changing CI configuration, deleting tests or adding skips. This check is a heuristic, not proof of an honest repair.
- Agents read repository content and send it to the model provider you choose.
- Siamang pushes to GitHub with your `gh` login.
- A pass from static QA does not prove the code runs. Review every PR before merging.

Read [SECURITY.md](SECURITY.md) before running Siamang. Report vulnerabilities through the repository's **Security** tab, not in a public issue.

## Documentation

Documents under `docs/` are currently written mostly in Indonesian.

| Document | Contents |
|---|---|
| [docs/README.md](docs/README.md) | Index of all documentation |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Configuration, CLI, HTTP API summary, the agent team and large-task behavior |
| [docs/VISION.md](docs/VISION.md) | Goals, principles, autonomy levels, component status |
| [docs/PHASE_ROADMAP.md](docs/PHASE_ROADMAP.md) | Order of work |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Structure and design decisions |
| [docs/HTTP_API.md](docs/HTTP_API.md) | API reference |
| [docs/SANDBOX.md](docs/SANDBOX.md) | Tests and builds in a container (opt-in) |
| [docs/AUTONOMY.md](docs/AUTONOMY.md) | Autonomy levels and autonomous Lead (opt-in) |
| [docs/SKILL.md](docs/SKILL.md) | Guide for AI agents |
| [docs/WEB.md](docs/WEB.md) | Web interface: layers, data flow and the rules of the 3D scene |
| [docs/WEB_CONTRACT.md](docs/WEB_CONTRACT.md) | The HTTP contract the web interface uses |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Dependencies and their licenses |

## Development

```bash
pip install -e '.[dev,api]'
.venv/bin/pytest                               # offline tests, no LLM
.venv/bin/python scripts/smoke_http.py         # HTTP against a temporary server and database
cd web && npm ci && npm test                             # web unit and component tests
```

See [CONTRIBUTING.md](./CONTRIBUTING.md). Maintained by one person, so please open an issue before a large change.

## License

[MIT](./LICENSE)
