"""Test helpers for the System Analyst: fixture projects on disk."""

from __future__ import annotations

from pathlib import Path

SECRET = "sk-SECRET-DO-NOT-LEAK-31337"


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def sample_project(root: Path) -> Path:
    """A small two-part project with a secret file, a test dir, and config."""
    return write(root, {
        "README.md": "# Banuva\n",
        "backend/pyproject.toml": '[project]\nname = "banuva"\ndependencies = ["fastapi>=0.110", "sqlalchemy"]\n',
        "backend/routes/auth.py": "def login():\n    pass\n",
        "backend/models/user.py": "class User: ...\n",
        "frontend/package.json": '{"name": "web", "dependencies": {"vue": "^3.4"}, "devDependencies": {"vite": "5"}}',
        "frontend/src/pages/Login.vue": "<template></template>\n",
        "tests/test_auth.py": "def test_login(): pass\n",
        "Dockerfile": "FROM python:3.13\n",
        ".env": f"API_KEY={SECRET}\nTOKEN={SECRET}\nPASSWORD={SECRET}\n",
        "config/secrets.yaml": f"private_key: {SECRET}\n",
        "certs/server.pem": f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n",
        "node_modules/vue/package.json": '{"name": "vue"}',
    })
