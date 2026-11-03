"""`siamang git ...` commands and the optional Git health capability."""

import json
import subprocess

import pytest

from siamang.cli.main import main
from siamang.git import client as client_mod
from tests.support.git import make_repo


@pytest.fixture
def cfg(config_file, tmp_path):
    return str(config_file(f'[storage]\npath = "{tmp_path / "data" / "s.db"}"\n'))


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def project(capsys, cfg, tmp_path):
    repo = make_repo(tmp_path / "repo")
    code, out, _ = run(capsys, "--config", cfg, "projects", "register", "--name", "Repo", "--path", repo.root,
                       "--json")
    assert code == 0
    return json.loads(out)["project_id"], repo


def test_inspect(capsys, cfg, project):
    pid, repo = project
    code, out, _ = run(capsys, "--config", cfg, "git", "inspect", pid)
    assert code == 0
    assert f"project_id       {pid}" in out
    assert "branch           main" in out and "clean            yes" in out and "relation         root" in out
    code, out, _ = run(capsys, "--config", cfg, "git", "inspect", pid, "--json")
    data = json.loads(out)
    assert data["project_id"] == pid and data["head"]["commit"] == repo.head()
    assert data["location"]["repository_root"] == repo.root


def test_status(capsys, cfg, project):
    pid, repo = project
    code, out, _ = run(capsys, "--config", cfg, "git", "status", pid)
    assert code == 0 and "On branch main" in out and "Working tree clean." in out
    repo.write("README.md", "changed\n")
    repo.write("new file.txt")
    repo.git("mv", "README.md", "READ ME.md")
    code, out, _ = run(capsys, "--config", cfg, "git", "status", pid)
    assert "renamed" in out and "README.md -> READ ME.md" in out and "untracked" in out
    code, out, _ = run(capsys, "--config", cfg, "git", "inspect", pid)
    assert "clean            no (" in out
    data = json.loads(run(capsys, "--config", cfg, "git", "status", pid, "--json")[1])
    assert not data["clean"] and data["counts"]["untracked"] == 1


def test_diff(capsys, cfg, project):
    pid, repo = project
    assert run(capsys, "--config", cfg, "git", "diff", pid)[1] == "No changes.\n"
    repo.write("README.md", "changed\n")
    code, out, _ = run(capsys, "--config", cfg, "git", "diff", pid)
    assert code == 0 and "+changed" in out
    assert run(capsys, "--config", cfg, "git", "diff", pid, "--cached")[1] == "No changes.\n"
    repo.git("add", "README.md")
    data = json.loads(run(capsys, "--config", cfg, "git", "diff", pid, "--cached", "--json")[1])
    assert data["cached"] and data["files"][0]["path"] == "README.md"
    assert (repo.path / "README.md").read_text() == "changed\n"  # nothing reverted or applied


def test_worktrees(capsys, cfg, project, tmp_path):
    pid, repo = project
    repo.git("worktree", "add", "-q", "-b", "feature", str(tmp_path / "wt"))
    code, out, _ = run(capsys, "--config", cfg, "git", "worktrees", pid)
    lines = out.splitlines()
    assert code == 0 and len(lines) == 3
    assert lines[1].startswith("*") and "main" in lines[1] and "feature" in lines[2]
    data = json.loads(run(capsys, "--config", cfg, "git", "worktrees", pid, "--json")[1])
    assert [w["kind"] for w in data] == ["main", "linked"]


def test_unknown_project(capsys, cfg, project):
    code, _, err = run(capsys, "--config", cfg, "git", "status", "prj_missing")
    assert code == 1 and "unknown project" in err


def test_without_database(capsys, cfg):
    code, _, err = run(capsys, "--config", cfg, "git", "inspect", "prj_x")
    assert code == 1 and "unknown project" in err


def test_not_a_repository(capsys, cfg, tmp_path):
    (tmp_path / "plain").mkdir()
    pid = json.loads(run(capsys, "--config", cfg, "projects", "register", "--name", "Plain", "--path",
                         str(tmp_path / "plain"), "--json")[1])["project_id"]
    code, out, err = run(capsys, "--config", cfg, "git", "inspect", pid)
    assert code == 1 and "not_repository" in err and out == ""
    assert str(tmp_path) not in err  # error text carries no path


def test_git_requires_subcommand():
    with pytest.raises(SystemExit) as info:
        main(["git"])
    assert info.value.code == 2


def test_no_mutating_subcommands():
    for cmd in ("commit", "push", "pull", "fetch", "checkout", "branch", "merge", "reset", "worktree-add"):
        with pytest.raises(SystemExit) as info:
            main(["git", cmd, "prj_x"])
        assert info.value.code == 2


# --- health ---------------------------------------------------------------------------


def test_health_reports_git(capsys, cfg):
    code, out, _ = run(capsys, "--config", cfg, "health")
    assert code == 0 and "Status: HEALTHY" in out
    assert any(line.startswith("Git") and "OK" in line for line in out.splitlines())


def test_health_without_git_is_still_healthy(capsys, cfg, monkeypatch):
    monkeypatch.setattr(client_mod.shutil, "which", lambda name: None)
    code, out, _ = run(capsys, "--config", cfg, "health")
    assert code == 0 and "Status: HEALTHY" in out
    assert "SKIP  (capability unavailable: git_not_installed)" in out


def test_health_with_old_git_is_still_healthy(capsys, cfg, monkeypatch):
    def fake(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, b"git version 2.20.0\n", b"")

    monkeypatch.setattr(client_mod.subprocess, "run", fake)
    code, out, _ = run(capsys, "--config", cfg, "health")
    assert code == 0 and "unsupported_git_version" in out and "Status: HEALTHY" in out


def test_validate_does_not_run_git(capsys, cfg, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("validate must not run git")

    monkeypatch.setattr(client_mod.subprocess, "run", forbidden)
    code, out, _ = run(capsys, "--config", cfg, "validate")
    assert code == 0 and "Status: VALID" in out
