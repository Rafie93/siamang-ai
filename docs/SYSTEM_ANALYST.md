# System Analyst (Phase 14)

**Status:** implemented (`src/siamang/analyst/`). Runtime is still
stdlib-only. No schema change (v3), no contract change (v0.2), no new
config, no events, no model provider.

## 1. Purpose

The System Analyst answers: **what does the project actually contain, and
what should implementation know before the planned work starts?** It
inspects a *registered* project read-only and returns a structured,
immutable, bounded `SystemAnalysis`.

```
Planner        = WHAT work is required
System Analyst = WHAT the system currently contains (and what is relevant, risky, unknown)
Programmer     = HOW to change it                    (future)
QA             = WHETHER the change works            (future)
Reviewer       = WHETHER the result is acceptable    (future)
```

## 2. Responsibilities

* Validate an `AnalysisRequest`, optionally carrying a validated Plan as data.
* Read the task through `TaskEngine.get`: it must exist, belong to the
  project, and not be final.
* Resolve the project through `ProjectRegistry`: it must exist and be
  active. Its registered `root_path` is the only filesystem authority.
* Inspect a bounded snapshot through `ProjectInspector`:
  - directory structure;
  - a fixed allowlist of manifest files, reading dependency *names* only;
  - secret-looking files, by name only.
* Read Git state through the Phase 08 `GitWorkspaceManager` (optional, read-only).
* Return findings and components that state their confidence
  (`observed`/`inferred`/`unknown`), plus risks, unknowns, and
  recommendations as data.

## 3. Non-responsibilities

The analyst does **not**:

* write, create, rename, or delete files, or change Git (commit, checkout,
  branch, worktree, stash, fetch, …);
* run commands, tests, shells, or servers;
* call agents, the orchestrator, the planner, tools, or models;
* change task or project state, persist analyses, or emit events;
* read source files for semantics: application-level understanding is a
  later phase.

## 4. Architecture

```
                    TASK
                      │
                      ▼
                   PLANNER
                      │
                      ▼
                    PLAN (optional context, data only)
                      │
                      ▼
              SYSTEM ANALYST  (analyst/service.py)
                      │
          ┌───────────┼───────────────┐
          ▼           ▼               ▼
   ProjectInspector  GitState     Task / Project
   (inspection.py)   (git_state.py: (TaskEngine.get,
   bounded, no        GitWorkspaceManager, ProjectRegistry)
   symlinks/secrets)  read-only)
          └───────────┼───────────────┘
                      ▼
               SYSTEM ANALYSIS  (validated, immutable, bounded)
                      │
                      ▼
              future PROGRAMMER
```

| Module | Role |
|---|---|
| `errors.py` | `AnalysisError` + 10 codes (`analysis_*`) |
| `request.py` | `AnalysisRequest` |
| `finding.py` | `Finding`, `FindingCategory`, `Severity`, `Confidence`, evidence rules |
| `component.py` | `Component`, `ComponentKind` |
| `analysis.py` | `SystemAnalysis`, `content` / `content_hash`, hard caps |
| `policy.py` | `AnalysisPolicy`, `DEFAULT_POLICY` |
| `inspection.py` | `ProjectInspector`: the only filesystem access |
| `git_state.py` | `GitState`, `read_git_state` via `GitWorkspaceManager` |
| `strategy.py` | `AnalysisStrategy` (ABC), `BasicStrategy`, `KNOWN_STRATEGIES` |
| `service.py` | `SystemAnalyst.analyze` |

Dependency direction (enforced by `tests/unit/test_analyst_architecture.py`):

```
cli ──► analyst ──► tasks (definition, errors, lifecycle; TaskEngine type, .get only)
                 ├─► projects, ids, log
                 ├─► planner.plan (Plan as data), planner.request (validation helpers)
                 └─► git.workspace, git.errors (never git.client)
analyst ─✗─► agents, orchestrator, tools, models, storage, bus, config, planner.service
planner, agents, orchestrator, tasks, projects, models, tools, git, storage, contracts ─✗─► analyst
```

## 5. AnalysisRequest

`AnalysisRequest(task_id, project_id, instruction, input={}, constraints=(),
metadata={}, plan=None)`, frozen.

* Validation reuses the Planner helpers, which reuse the Task Engine policy.
  Command, argv, cwd, path, shell, credential, agent, tool, model, provider,
  retry, workflow, callback, and executor keys are refused at any depth.
* The analyst also refuses, at any depth: `git_write`, `commit`, `branch`,
  `checkout`, `worktree`, `merge`, `reset`, `push`, `pull`, `fetch`,
  `stash`, `file`, `files`, `glob`, `pattern`, `include`, `exclude`, `root`,
  `write`, `delete`, `install`, and similar keys. **A request can never
  choose which files are read.**
* The instruction is opaque data. "cat ../../etc/passwd" is something to
  analyze around, not a path.
* `plan` must be a validated `Plan` for the same task and project. The
  analyst never calls the Planner.

## 6. Finding

`Finding(finding_id, category, severity, title, description, evidence,
confidence, metadata)`, frozen, with positional `finding_01` IDs.

* `category`: architecture, technology, dependency, structure,
  configuration, security, testing, integration, risk, constraint, or
  unknown.
* `severity`: info, low, medium, high, or critical. No other strings are
  accepted.
* `confidence`: observed, inferred, or unknown (§14).
* `evidence`: up to 20 single lines of ≤ 300 characters. They are redacted,
  project-relative, contain no `..` segment, and are never absolute
  (`/`, `~`, `C:\`).

## 7. Component

`Component(component_id, name, kind, location, description, technologies,
evidence, confidence, metadata)`, frozen.

* `kind`: application, library, frontend, backend, database, tests,
  configuration, documentation, deployment, build, or unknown.
* `location`: a project-relative directory (`.` is the root). It is never
  absolute and never contains `.`/`..` segments.
* `technologies`: names only, at most 10.

## 8. SystemAnalysis

`SystemAnalysis(analysis_id, task_id, project_id, objective, summary,
components, findings, constraints, risks, unknowns, recommendations,
metadata)`, frozen; every collection is a tuple.

* Validated on construction: identity, unique IDs, at most 200 findings and
  100 components (hard caps), at most 50 notes per list, serialized content
  ≤ 512 KiB.
* `analysis_id`: `ana_<ULID>` (new `analysis` ID prefix), different on
  every call.
* `content()` / `content_hash` (`sha256:`): everything except
  `analysis_id`. It is a change fingerprint, not a security mechanism.
* `metadata`: strategy and version, `files_read`, `bytes_read`,
  `entries_listed`, `truncated`, `git_available`, `match_terms`, and
  `plan_content_hash` when a plan was given.

## 9. Project inspection boundary

`ProjectInspector(root_path, policy)` is the **only** analyst code that
touches the filesystem, and the only analyst module that imports `os`
(security test). Public API: `list_dir(path)`, `read_text(path)`, `usage()`.
It has no write, path, environment, or execution API.

* The root is the registry's `root_path`, resolved once. A registered root
  that is itself a symlink is accepted, because the registry is the
  authority. It must be an existing directory.
* `list_dir` returns sorted entries (name, kind, size, sensitive). Kinds are
  file, directory, symlink, and other.
* `read_text` reads one regular file of at most `max_file_size` bytes.
  Larger files are reported (`too_large`), not read. Binary content
  (contains NUL) is not returned.

## 10. Filesystem security

* **Relative paths only.** No absolute paths, `..`/`.`/empty segments,
  backslashes, `~`, `%` (no encoded traversal), or control characters.
* **No symlink is ever followed**, whether it points inside or outside the
  root. Every component is checked with `lstat`. Symlinks are listed but
  never entered or read. The file is opened with
  `O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`, the descriptor is
  re-checked as a regular file, and the resolved path must lie inside the
  root.
* **Regular files only.** FIFOs, sockets, and devices are refused before
  being opened.
* **Budgets** (§13) make every listing and read finite. A spent budget is
  reported deterministically.
* **Hostile names.** Names with control or format characters (for example
  RTL overrides) or very long paths are omitted and only counted, so one
  file name cannot break or spoof an analysis.
* The analysis is a **snapshot**. The project may change while it is
  inspected; there are no locks or watchers. Entries that vanish are
  skipped.

## 11. Git read-only analysis

Git comes only from `GitWorkspaceManager.inspect` and `.worktrees`
(Phase 08). The analyst never calls `GitClient.run` itself. Tests record
that only `rev-parse`, `symbolic-ref`, `status`, `remote`, and `worktree`
reach Git, and that HEAD, branches, status, and stash are unchanged
afterwards.

* Reported: branch, short commit (or detached/unborn), clean/dirty with
  counts, repository-relative changed paths (≤ 50), ahead/behind against
  the *local* upstream ref, the number of worktrees, and remote **names**.
* Not reported: absolute repository, git-dir, and worktree paths, and
  remote URLs.
* A protected repository (ARCHITECTURE §6) refuses the
  analysis (`analysis_project_unavailable`, cause `protected_repository`).
  Any other Git error, or no manager, means Git state is an *unknown*, not a
  failure.

## 12. Secret handling

* Secret-looking files are **never opened**: `.env`, `.env.*`, `*.env`,
  `.envrc`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `id_rsa*` (and the
  other key types), `credential(s).*`, `secret(s).*`, `*.secret(s).*`,
  `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, and
  `service-account*.json`. They are reported by **name only** in a
  `security` finding, plus a risk note.
* Manifests yield dependency **names** only: no versions, scripts, URLs, or
  other values, and never raw file contents.
* All finding and component text, notes, and evidence pass through the
  shared redactor. Phase 14 also made that redactor linear-time (§19).
* Logs carry only task, project, and analysis IDs, counts, and the outcome.
  Never the instruction, input, file names, or contents.

## 13. Resource limits

`AnalysisPolicy` (defaults; hard cap in parentheses). No environment or
config override exists.

| Limit | Default (cap) | When exceeded |
|---|---|---|
| `max_files` (contents read) | 20 (200) | further reads stop; unknown "policy limit (file reads)" |
| `max_file_size` | 64 KiB (1 MiB) | file noted "too large", not read |
| `max_total_bytes` | 256 KiB (8 MiB) | further reads stop |
| `max_directory_entries` | 2000 (20 000) | listing stops; unknown "policy limit (directory listing)" |
| `max_depth` | 3 (6) | entries below are not listed |
| `max_findings` / `max_components` | 50 / 20 (200 / 100) | truncated in order; an unknown says how many were omitted |
| `max_output_size` | 128 KiB (512 KiB) | `analysis_policy_denied` |
| `max_instruction_length` | 4000 (10 000) | `analysis_policy_denied` |

`max_depth = 3` lists the root and two directory levels below it. Their
entries appear, but third-level directories are not entered. Vendored,
build, and VCS directories (`.git`, `node_modules`, `.venv`, `vendor`,
`dist`, `build`, …) are listed but not descended into. Truncation sets
`metadata.truncated`.

## 14. Observed vs inferred

| Confidence | Meaning | Example | Evidence |
|---|---|---|---|
| `observed` | seen directly in the snapshot | "Test directories present" (`tests/`) | required |
| `inferred` | concluded from observations | "Python indicators" (`backend/pyproject.toml exists`); "Frameworks declared as dependencies" | required |
| `unknown` | could not be established | "No test directory found"; Git unavailable | optional |

Technology and framework claims are always `inferred`: declaring a
dependency is not proof of use. Matching the request's words to file names
is `inferred` relevance. Every analysis lists unknowns, at minimum that
application semantics and runtime/deployment architecture were not
inspected.

## 15. Determinism

For the same request, policy, strategy, and project snapshot, `content()`
and `content_hash` are identical. Listings are sorted, budgets are charged
in a fixed order, and no timestamp, random value, absolute path, or
environment value enters the content. Two copies of the same tree in
different directories analyze identically (tested). If the project
changes, the content changes. `analysis_id` is identity only.

## 16. Planner relationship

The analyst accepts a validated `Plan` as optional context. It checks that
the plan is for the same task and project, records `plan_content_hash`, and
when the plan has an "Identify affected components" step, recommends using
the analysis for that step. The analyst never calls the Planner, and the
Planner does not import the analyst.

## 17. Programmer relationship

A caller hands a Plan plus a SystemAnalysis to the Programmer (Phase 15,
[PROGRAMMER.md](PROGRAMMER.md)). The analysis provides relevant components
and paths, observed technologies, risks (dirty tree, secret files), and
unknowns to resolve. The Programmer consumes the analysis as data: it
re-validates it, carries its unknowns forward, flags inferences, and reads
current files only through this phase's `ProjectInspector`. It never calls
`SystemAnalyst`, and the analyst never imports the Programmer. QA and
Reviewer are not implemented, and the analyst is not wrapped as an Agent.

## 18. Future model-backed analysis

`AnalysisStrategy.analyze(AnalysisContext) -> AnalysisDraft` is the seam.
Strategies are allowlisted by ID (`KNOWN_STRATEGIES`); only
`analysis.basic` exists. Future strategies (`analysis.python`,
`analysis.web`, `analysis.model`, …) would be added there. A model-backed
one would reach models only through the Model Router and return a draft
that passes exactly the same caps and validation. It would still use only
the `ProjectInspector` budget and never get raw filesystem or Git access.
Not implemented.

## 19. Limitations

* Structure and manifests only: no source-code reading, call graphs, or
  semantic understanding. "Relevant" paths are name matches.
* `pubspec.yaml` is recognised by a simple line check (no YAML parser in
  the stdlib). Other manifests are detected by name, and only
  `pyproject.toml`, `requirements.txt`, `package.json`, and `composer.json`
  are parsed for dependency names.
* Manifests are looked for only at the root and one level below.
* Symlinked directories are never analyzed, even ones that point inside the
  project.
* There are no locks: an analysis is a best-effort snapshot (§10).
* Git analysis needs Git ≥ 2.36 and a `GitWorkspaceManager`. Without one,
  Git state is unknown.
* Analyses live in memory only: no history or events, and `analysis_id` is
  not stored.
* The CLI shows only the policy; there is no command to analyze a project.
* **Lower-layer fix in this phase:** `siamang.log.redact` was quadratic in
  the length of long words (4000 characters took ~250 ms, 100k minutes).
  It is now linear with identical output (regression tests in
  `test_logging.py`).

## Phase 14 decisions

IDs use `SA` because `P`/`PL` already have meanings.

| # | Decision |
|---|---|
| SA1 | System Analyst is read-only: no writes, Git mutation, or execution |
| SA2 | Project Registry remains the project authority; the registered `root_path` is the only root |
| SA3 | Filesystem inspection goes through one controlled boundary (`ProjectInspector`, the only `os` user) |
| SA4 | The project-root boundary is mandatory: relative paths only, symlinks never followed, realpath containment |
| SA5 | Git access is read-only, through `GitWorkspaceManager` only; protected repositories refuse analysis |
| SA6 | Secrets are never returned: secret files are never opened, manifests yield names only, and output is redacted |
| SA7 | Findings and components state `observed` / `inferred` / `unknown`; observed and inferred need evidence |
| SA8 | Analysis is structured, validated data, not free-form text |
| SA9 | Analysis is bounded by `AnalysisPolicy` under fixed hard caps; truncation is deterministic and visible |
| SA10 | Analysis content is deterministic for the same snapshot; `analysis_id` is identity only |
| SA11 | The analyst does not execute agents, the orchestrator, the planner, or tools |
| SA12 | The analyst does not mutate tasks or projects; non-final tasks may be analyzed |
| SA13 | The analyst does not call external models; `AnalysisStrategy` is the allowlisted seam |
| SA14 | No persistence or events (schema v3, contract v0.2) |
| SA15 | The analyst is separate from the Agent abstraction (no wrapping in this phase) |
| SA16 | Requests can never select files; strategies read only a fixed manifest allowlist |
| SA17 | Hostile file names are omitted and counted, never echoed |
| SA18 | Read-only CLI `siamang analyst [--json]` plus a `validate` check; no run/execute/modify or path inspection |
| SA19 | `siamang.log.redact` made linear-time (it was quadratic); output unchanged |
