"""Phase 25: untrusted code runs in a container that has no network, no secrets, and hard limits."""
import os
import stat
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from siamang.sandbox import CliContainerRuntime, RawRun, SandboxPolicy, SandboxService, SandboxStatus
from siamang.sandbox import workspace

POLICY = SandboxPolicy("python:3.12-slim", timeout_secs=5)


def change(path, content=None, operation="modify"):
    return SimpleNamespace(path=path, after_content=content, operation=operation)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote]")
    (root / "src" / "app.py").write_text("print('old')\n")
    (root / ".env").write_text("API_TOKEN=operator-secret-value")
    (root / "id_rsa").write_text("PRIVATE")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x")
    (tmp_path / "outside.txt").write_text("host file")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    return root


class Recording:
    def __init__(self, raw=None, up=True):
        self.raw, self.up, self.seen = raw or RawRun(0, "ok"), up, {}

    def available(self):
        return self.up

    def run(self, work, argv, policy):
        self.seen = {"argv": argv, "files": sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()),
                     "text": {p.relative_to(work).as_posix(): p.read_text() for p in work.rglob("*") if p.is_file()}}
        return self.raw


def test_workspace_is_a_copy_without_secrets_history_links_or_dependencies(project):
    runtime = Recording()
    result = SandboxService(runtime, POLICY).run(project, [], ["pytest"])
    assert result.passed and runtime.seen["files"] == ["src/app.py"]
    assert (project / ".env").exists() and (project / "link.txt").is_symlink()  # the project itself is untouched


def test_the_patch_is_applied_to_the_copy_only(project):
    runtime = Recording()
    changes = [change("src/app.py", "print('new')\n"), change("src/extra.py", "x = 1\n", "create")]
    SandboxService(runtime, POLICY).run(project, changes, ["pytest"])
    assert runtime.seen["text"]["src/app.py"] == "print('new')\n" and "src/extra.py" in runtime.seen["files"]
    assert (project / "src" / "app.py").read_text() == "print('old')\n"
    assert not (project / "src" / "extra.py").exists()


def test_a_delete_removes_the_file_from_the_copy(project):
    runtime = Recording()
    SandboxService(runtime, POLICY).run(project, [change("src/app.py", None, "delete")], ["pytest"])
    assert runtime.seen["files"] == []


@pytest.mark.parametrize("path", [".env", ".git/config", "../escape.py", "/etc/passwd", "a/../../b"])
def test_a_change_to_a_secret_or_outside_path_is_refused(project, path):
    runtime = Recording()
    result = SandboxService(runtime, POLICY).run(project, [change(path, "x", "create")], ["pytest"])
    assert result.status is SandboxStatus.ERROR and runtime.seen == {}


def test_the_workspace_size_limit_stops_the_run(project):
    small = SandboxPolicy("img", max_files=1)
    (project / "src" / "b.py").write_text("b")
    runtime = Recording()
    result = SandboxService(runtime, small).run(project, [], ["pytest"])
    assert result.status is SandboxStatus.ERROR and runtime.seen == {}


@pytest.mark.parametrize("raw,status", [(RawRun(0, "ok"), "passed"), (RawRun(1, "boom"), "failed"),
                                        (RawRun(None, "", timed_out=True), "timed_out"), (RawRun(None, "x"), "error")])
def test_the_status_comes_from_the_exit_code(project, raw, status):
    assert SandboxService(Recording(raw), POLICY).run(project, [], ["x"]).status == status


def test_no_engine_is_unavailable_and_never_a_pass(project):
    runtime = Recording(up=False)
    result = SandboxService(runtime, POLICY).run(project, [], ["pytest"])
    assert result.status is SandboxStatus.UNAVAILABLE and not result.passed and runtime.seen == {}


def test_output_is_redacted(project):
    raw = RawRun(1, "token=ghp_" + "a" * 36 + " failed")
    assert "ghp_" + "a" * 36 not in SandboxService(Recording(raw), POLICY).run(project, [], ["x"]).output


@pytest.mark.parametrize("argv", ["pytest -q", [], [""], ["a", 3], ["a\x00"], None])
def test_a_command_is_a_list_never_a_shell_line(project, argv):
    with pytest.raises(ValueError):
        SandboxService(Recording(), POLICY).run(project, [], argv)


@pytest.mark.parametrize("kwargs", [dict(image="-rm"), dict(image="a b"), dict(image=""), dict(image="x", timeout_secs=0),
                                    dict(image="x", timeout_secs=10**6), dict(image="x", memory_mb=True),
                                    dict(image="x", cpus=100)])
def test_the_policy_has_no_unlimited_value(kwargs):
    with pytest.raises(ValueError):
        SandboxPolicy(**kwargs)


def test_the_policy_cannot_enable_the_network():
    assert SandboxPolicy("x").to_dict()["network"] == "none"
    assert "network" not in SandboxPolicy.__dataclass_fields__


# -- the real CLI runtime, against a stand-in engine so no container is needed -----------------------------

def engine(tmp_path, body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "podman"
    script.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"

    def make(body):
        bin_dir = engine(tmp_path, f'echo "$@" >> {log}\nenv >> {log}.env\n' + body)
        monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
        return log
    return make


def test_the_container_is_started_with_every_isolation_flag(tmp_path, fake_engine):
    log = fake_engine('[ "$1" = info ] && exit 0\necho ran\n')
    (tmp_path / "w").mkdir()
    raw = CliContainerRuntime("podman").run(tmp_path / "w", ("pytest", "-q"), POLICY)
    line = [l for l in log.read_text().splitlines() if l.startswith("run ")][0]
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--user 65534:65534", "--pids-limit 256", "--memory 1024m", "--memory-swap 1024m", "--pull never"):
        assert flag in line
    assert line.endswith("python:3.12-slim pytest -q") and f"{tmp_path / 'w'}:/work:rw" in line
    assert raw.exit_code == 0 and "ran" in raw.output


def test_operator_secrets_never_reach_the_engine(tmp_path, fake_engine, monkeypatch):
    log = fake_engine('echo done\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-operator-secret")
    monkeypatch.setenv("SIAMANG_SEAL_KEY", "seal-secret")
    (tmp_path / "w").mkdir()
    CliContainerRuntime("podman").run(tmp_path / "w", ("x",), POLICY)
    env = Path(str(log) + ".env").read_text()
    assert "sk-operator-secret" not in env and "seal-secret" not in env


def test_a_command_that_runs_too_long_is_stopped(tmp_path, fake_engine):
    fake_engine('[ "$1" = rm ] && exit 0\nexec sleep 30\n')
    (tmp_path / "w").mkdir()
    raw = CliContainerRuntime("podman").run(tmp_path / "w", ("x",), SandboxPolicy("img", timeout_secs=1))
    assert raw.timed_out and raw.exit_code is None and raw.seconds < 10


def test_output_is_bounded_while_the_stream_is_drained(tmp_path, fake_engine):
    fake_engine('yes aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | head -c 3000000\nexit 3\n')
    (tmp_path / "w").mkdir()
    raw = CliContainerRuntime("podman").run(tmp_path / "w", ("x",), SandboxPolicy("img", output_bytes=1000))
    assert len(raw.output) == 1000 and raw.truncated and raw.exit_code == 3


def test_a_missing_engine_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert CliContainerRuntime("podman").available() is False
    (tmp_path / "w").mkdir()
    assert CliContainerRuntime("podman").run(tmp_path / "w", ("x",), POLICY).exit_code is None


def test_an_engine_that_cannot_run_is_unavailable(tmp_path, fake_engine):
    fake_engine("exit 125\n")
    assert CliContainerRuntime("podman").available() is False


def test_only_known_engines(tmp_path):
    with pytest.raises(ValueError):
        CliContainerRuntime("sh")


def test_workspace_error_never_carries_a_path(tmp_path):
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.build(tmp_path / "missing", [], tmp_path, POLICY)
    assert str(tmp_path) not in str(caught.value)
