# Contributing to Siamang

Thanks for your interest. Siamang is early and maintained by one person, so please open an issue before starting a large change. Small fixes can go straight to a pull request.

## Backend

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,api]'
.venv/bin/pytest                         # offline, no model or network needed
.venv/bin/python scripts/smoke_http.py   # real HTTP against a temporary server and database
```

## Web interface

It needs the backend's `.venv` and a `siamang.toml` in the repository root.

```bash
cd web
npm install
npm run dev        # http://127.0.0.1:5180
npm test           # unit and component tests, no backend needed
```

End-to-end tests create tasks, so point them at a throwaway data folder:

```bash
WEB_SANDBOX=1 WEB_DATA=/tmp/siamang-e2e npm run dev   # in one terminal
npm run test:e2e                                      # in another; WEB_LIVE=1 adds a real Ollama pipeline
```

## Rules that keep the project honest

- A model never decides whether something passed. Pass status comes from deterministic code or from a real execution.
- Do not describe a capability as working unless it has been run. If it has only been tested with a fake model, say so.
- Keep the core free of dependencies outside the standard library. FastAPI and uvicorn stay in the optional `api` extra.
- The interface shows an agent working only when the backend reports fresh activity. Offline, stale or unknown is never animated as work, and a section with no recorded data stays absent or says so.
- Demo mode is always labelled as a simulation and stays out of Tasks, Reports and Approvals.
- Report security problems through the repository's **Security** tab, never in a public issue. See [SECURITY.md](SECURITY.md).

## Pull requests

- Add or update tests for behavior you change, and keep `pytest` and `npm test` passing.
- In `web/`, run `npm run format`, and use the components in `src/components/ui/` and the tokens in `src/assets/theme.css`. Please don't add a UI library or a template: the project must stay free of license restrictions.
- Keep the version in `pyproject.toml` and `web/package.json` the same. A test checks this.
- Update the docs and the [changelog](CHANGELOG.md) when behavior changes.
- When the README changes, update both languages (`README.md` and `README_ID.md`). If you can't translate it, say so in the pull request.
- To change the README banner, edit `scripts/generate_readme_images.py` and run it.
