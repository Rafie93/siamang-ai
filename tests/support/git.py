"""Test helpers: isolated temporary Git repositories.

Tests drive Git directly to *build* scenarios (commits, branches, merges);
Siamang itself only ever reads them. User and system Git config are ignored,
so the developer's ~/.gitconfig cannot change results.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(cwd: Path | str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=30)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


class Repo:
    def __init__(self, path: Path):
        self.path = path

    @property
    def root(self) -> str:
        return str(self.path)

    def git(self, *args: str, check: bool = True) -> str:
        return git(self.path, *args, check=check)

    def write(self, rel: str, text: str = "x\n") -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def commit(self, message: str = "commit", *paths: str) -> str:
        self.git("add", "-A", *paths)
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()


def make_repo(path: Path, *, commit: bool = True, branch: str = "main") -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    repo = Repo(path.resolve())
    if commit:
        repo.write("README.md", "hello\n")
        repo.commit("initial")
    return repo
