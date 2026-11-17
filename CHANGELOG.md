# Changelog

Notable changes to Siamang. No version has been tagged as a GitHub release yet.

## 0.3.0 (unreleased)

### Added

- Tokens and time per project, task, and agent. Every model call is recorded with its agent, stage, and input and output tokens, failed calls included, and every stretch of an agent's work is timed, model or not. Input counts the prompt cache, which holds most of a Claude prompt. Parallel work counts once, a draft's conversation counts toward the task it became, and the project's intake shows apart as "outside tasks". `GET /v0/tasks/{id}/usage` and `GET /v0/projects/{id}/usage`; task lists and the portfolio carry a short total. API schema 8. The web interface shows a **Pemakaian** tab on each project, a token and duration column in the task list, a per-agent table in the task detail, and the agent's own share in the office dialog. Work from before this version has durations but no token record, and the page says so.
- Sandbox execution (opt-in): tests and builds run in a container, and that result decides whether a run passes. Not yet tested with a real container.
- Programmer tools (opt-in): the Programmer edits and runs commands in the sandbox before proposing a patch.
- Recovery Engine (opt-in): three tiers of bounded recovery when delivery fails. Not yet applied to parallel-package runs.
- Autonomy levels and autonomous Lead (opt-in): risk-based gates recorded in the policy's name. Not yet tested with a real model, container and GitHub together.
- Work division: design documents and work packages run in parallel within a task, with bounded retries, and the record shows what really ran at the same time.
- Automatic CI repair on an approved, open pull request, a limited number of times.
- Designer (UI/UX) agent between the Architect and the design gate: design tokens, static HTML wireframes, screen specification, user flow, sitemap, and accessibility, approved with the architecture and handed to the Programmer. Configure it as `[agents.designer]`; without a model its documents say `not_assigned`. A seventh desk in the 3D office.
- The Documents page is now **Artifacts** (same route): a list grouped by agent beside a reader, with search over titles and text, filters (diagram, UI design, text), a block count per document, and a table of contents that jumps to each diagram. Diagrams are drawn only as they near the screen. The task is picked beside the project.
- The sidebar collapses to its icons on wide screens and remembers it.
- Wireframes are drawn at desktop and phone width in a sandboxed frame that runs no script and loads nothing, with the design tokens as CSS variables; tokens show as swatches. The owner edits a wireframe's HTML or the token JSON beside a live preview, through the same gate, stopped-run, and revision paths as diagrams.
- Diagram editor: Mermaid code beside a live preview, with the earlier text kept. At the design gate, approving names what changed; a stopped run goes again on the changed design; on a published task, a change becomes a revision task and the shipped record stands.
- Local projects: **Proyek baru** starts a project from nothing, with no GitHub and no CI. Siamang creates its own Git repository in the project's folder; each task still works on its own branch, and approving the result merges it into `main`. Uncommitted changes in that folder are stashed, never discarded. `POST /v0/projects` without `repository` creates one; projects from GitHub work as before.
- Web interface in `web/`, in the same repository as the backend, with one version for both.
- Event stream: `GET /v0/stream` sends what changed as Server-Sent Events, resumable after a dropped connection and scoped to the projects a token may read. API schema 7 adds the `api_changes` log, filled by SQL triggers inside each write's own transaction.
- The web interface follows that stream instead of polling. An idle page now sends no requests, and changes appear in under a second. It falls back to polling when the stream is unavailable, and the top bar says which it is using.
- `scripts/init-credentials.py`, `siamang.claude.example.toml` and `siamang.ollama.example.toml`, which replace the inline setup in the quick start. The Ollama template replaces `siamang.api.example.toml` and now gives every agent a model.

### Changed

- The web interface was rebuilt without a commercial admin template: small components over native HTML elements, with no UI library.
- The web gateway creates a random backend token for each run instead of using a fixed one.
- Web data moved out of the repository, to `~/.siamang/web` (`WEB_DATA` overrides it), because projects must not live inside Siamang's own repository.
- README rewritten in English, with a Bahasa Indonesia translation. Details moved to `docs/REFERENCE.md`.

### Fixed

- A new file named in the project specification or the approved design no longer holds a task back as outside the task's scope, so that work reaches the approvals page. A file nobody named still raises the warning.
- A task with more than 100 execution events no longer stops at an old stage in the web interface: it now reads the whole history, a page at a time.
- A refresh asked for during another load, such as right after an action, is no longer dropped.
- `execution-events` now removes machine paths and credentials from its values, as the other routes do.
- A Programmer change labelled `create` on an existing file it had read no longer fails the task: it becomes a modification, with a warning. A file it never saw is still refused, and a failed task now names the rejected path.
- The **Bagi ulang** button always failed, because the gateway did not allow `POST /tasks/{id}/work-division`.

History before 0.3.0 is not published.
