# Changelog

Notable changes to Siamang. No version has been tagged as a GitHub release yet.

## 0.3.0 (unreleased)

### Added

- Sandbox execution (opt-in): tests and builds run in a container, and that result decides whether a run passes. Not yet tested with a real container.
- Programmer tools (opt-in): the Programmer edits and runs commands in the sandbox before proposing a patch.
- Recovery Engine (opt-in): three tiers of bounded recovery when delivery fails. Not yet applied to parallel-package runs.
- Autonomy levels and autonomous Lead (opt-in): risk-based gates recorded in the policy's name. Not yet tested with a real model, container and GitHub together.
- Work division: design documents and work packages run in parallel within a task, with bounded retries, and the record shows what really ran at the same time.
- Automatic CI repair on an approved, open pull request, a limited number of times.
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

- A task with more than 100 execution events no longer stops at an old stage in the web interface: it now reads the whole history, a page at a time.
- A refresh asked for during another load, such as right after an action, is no longer dropped.
- `execution-events` now removes machine paths and credentials from its values, as the other routes do.
- A Programmer change labelled `create` on an existing file it had read no longer fails the task: it becomes a modification, with a warning. A file it never saw is still refused, and a failed task now names the rejected path.
- The **Bagi ulang** button always failed, because the gateway did not allow `POST /tasks/{id}/work-division`.

History before 0.3.0 is not published.
