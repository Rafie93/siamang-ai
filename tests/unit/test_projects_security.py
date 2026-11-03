"""The registry is metadata-only: no processes, network, env/credential reads, or directory scans."""

import builtins
import io
import os
import pathlib
import subprocess

import pytest

from siamang.cli.main import main
from siamang.config import LoggingConfig
from siamang.log import configure_logging
from siamang.projects import ProjectRegistry
from siamang.storage import Database

SECRET = "sk-project-env-SECRET-424242"


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / ".env").write_text(f"OPENAI_API_KEY={SECRET}\n")
    (root / "credentials.json").write_text(f'{{"key": "{SECRET}"}}')
    (root / "src" / "main.py").write_text("raise SystemExit('must never run')\n")
    return root


@pytest.fixture
def guarded(monkeypatch, project_dir):
    """Fail on any process spawn or directory listing anywhere, and on any file
    access inside the project directory. Returns the list of such accesses."""
    touched: list[str] = []

    def forbidden(*a, **k):
        raise AssertionError("forbidden call during project registry operation")

    for target, name in [
        (subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (subprocess, "check_output"),
        (os, "system"), (os, "popen"), (os, "fork"), (os, "execv"), (os, "execvp"), (os, "spawnv"),
        (os, "scandir"), (os, "listdir"), (os, "walk"),
        (pathlib.Path, "iterdir"), (pathlib.Path, "glob"), (pathlib.Path, "rglob"),
    ]:
        monkeypatch.setattr(target, name, forbidden)

    def inside(path) -> bool:
        try:
            return os.fspath(path).startswith(str(project_dir))
        except TypeError:
            return False

    def guard(real):
        def wrapper(path, *a, **k):
            if inside(path):
                touched.append(os.fspath(path))
                raise AssertionError(f"read inside project: {path}")
            return real(path, *a, **k)

        return wrapper

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(os, "open", guard(os.open))
    real_read_text, real_read_bytes = pathlib.Path.read_text, pathlib.Path.read_bytes
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: guard(real_read_text)(self, *a, **k))
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self, *a, **k: guard(real_read_bytes)(self, *a, **k))
    return touched


def test_full_lifecycle_touches_nothing_inside_project(db, project_dir, guarded, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="DEBUG"), stream=stream)
    reg = ProjectRegistry(db)
    p = reg.register(name="Proj", root_path=project_dir, check_exists=True, metadata={"language": "python"})
    reg.update(p.project_id, description="d", root_path=project_dir, check_exists=True)
    reg.get(p.project_id)
    reg.list()
    reg.find_by_root_path(project_dir)
    reg.archive(p.project_id)
    reg.restore(p.project_id)
    assert guarded == []
    logged = stream.getvalue()
    assert SECRET not in logged
    assert str(project_dir) not in logged  # paths are not logged
    assert SECRET.encode() not in db.path.read_bytes()


def test_cli_register_touches_nothing_inside_project(capsys, config_file, tmp_path, project_dir, guarded):
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert main(["--config", str(cfg), "projects", "register", "--name", "P", "--path", str(project_dir)]) == 0
    assert main(["--config", str(cfg), "projects", "--all"]) == 0
    out = capsys.readouterr()
    assert guarded == []
    assert SECRET not in out.out + out.err


def test_shell_metacharacters_are_inert(db, guarded, tmp_path):
    marker = tmp_path / "pwned"
    evil = f"/tmp/x; touch {marker} && $(touch {marker}) `touch {marker}`"
    p = ProjectRegistry(db).register(name="$(rm -rf ~)", root_path=evil, description="`whoami`")
    assert ProjectRegistry(db).get(p.project_id).root_path == evil
    assert not marker.exists()


def test_errors_do_not_leak_paths_or_metadata(db, project_dir):
    reg = ProjectRegistry(db)
    reg.register(name="Proj", root_path=project_dir)
    from siamang.projects import DuplicateProjectError, InvalidProjectError

    with pytest.raises(DuplicateProjectError) as dup:
        reg.register(name="Other", root_path=project_dir)
    assert str(project_dir) not in str(dup.value)
    with pytest.raises(InvalidProjectError) as bad:
        reg.register(name="X", root_path="/w/x", metadata={"auth_token": SECRET})
    assert SECRET not in str(bad.value)


def test_network_blocked_and_unused(db):
    # The autouse fixture makes any connect() raise; the registry must not need it.
    reg = ProjectRegistry(db)
    reg.register(name="Net", root_path="/w/net")
    assert reg.get_by_name("net").name == "Net"
