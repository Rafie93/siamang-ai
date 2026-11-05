# Security Policy

## Scope and intended use

Siamang is early software (v0.x) for one operator on their own machine. The API binds
to `127.0.0.1` and has no login system or rate limiting. **Do not expose it to the
internet.**

Know these limits before running it:

- Agents read repository content and send it to the model provider you configure.
- Siamang pushes to GitHub with your `gh` login, only to `siamang/*` branches, without
  force, and without a merge unless the operator set `[autonomy] level = 3`. When CI fails on an open pull request, it pushes a repair to that branch
  without asking, up to `ci_repair` times. Set `ci_repair = 0` to turn this off.
- CI logs are project output and reach the model as data. Treat them as untrusted.
- Review generated pull requests before merging. By default QA is static and Siamang does not
  run project code, so a passing check does not prove the code works. `[sandbox]` runs code in a
  container when you turn it on, and has not yet been tested with a real container.
- Treat repository content and model output as untrusted. Report any path where they can
  act as instructions or reach your credentials.

## Web interface

The interface in `web/` binds to `127.0.0.1` and has no login system either. **Do not expose it to a network.**

- The browser never holds a backend token. A local gateway (`web/scripts/local.mjs`) creates a random token for each run and attaches it on the server side.
- The gateway requires a session cookie (`HttpOnly`, `SameSite=Strict`), a CSRF token and a matching origin and host for writes, and forwards only an allowlist of routes (`web/scripts/gateway.mjs`).
- A strict Content Security Policy blocks inline and third-party scripts.
- Model output is rendered as text. Mermaid diagrams written by a model are drawn in strict mode, which sanitizes the SVG and drops click handlers.
- Approval, evidence and permissions are enforced by the backend. A disabled button is a convenience, not access control.
- Local data, including the database and the evidence seal key, lives in `~/.siamang/web`, outside the repository. Keep it private.

## Supported versions

Only the latest commit on `main` receives security fixes.

## Reporting a vulnerability

Please do not open a public issue. Use **Report a vulnerability** on the repository's
**Security** tab, and include the affected version, steps to reproduce, and the impact.

Useful reports include:

- Bypassing an approval gate or forging approval evidence.
- Pushing outside `siamang/*`, or writing outside a managed clone.
- Leaking credentials, tokens, or the seal key.
- Prompt injection that changes what Siamang does rather than only what a model writes.
- Reaching a backend route the web gateway should refuse, or writing without a valid session and CSRF token.
- Running script through model output, a document, or a Mermaid diagram in the web interface.
- The web interface showing work, a pass or an approval that the backend did not report.
