import json
import subprocess
import sys
from pathlib import Path

import pytest

from siamang.cli.main import main
from siamang.contracts import new_event
from siamang.storage import SCHEMA_VERSION, Database, EventStore

EXAMPLES = Path(__file__).parents[2] / "src" / "siamang" / "contracts" / "examples"
INVALID = Path(__file__).parents[1] / "fixtures" / "invalid"


@pytest.fixture
def cfg(config_file, tmp_path):
    return config_file(f'[storage]\npath = "{tmp_path / "data" / "s.db"}"\n[logging]\nlevel = "WARNING"\n')


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_health_success(capsys, cfg, tmp_path):
    code, out, _ = run(capsys, "--config", str(cfg), "health")
    assert code == 0
    for name in ("Configuration", "Storage", "Database", "Event Bus", "Contract"):
        assert any(line.startswith(name) and " OK" in line for line in out.splitlines()), name
    assert out.strip().endswith("Status: HEALTHY")
    assert (tmp_path / "data" / "s.db").exists()


def test_health_failure_storage(capsys, config_file, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    path = config_file(f'[storage]\npath = "{blocker / "sub" / "s.db"}"\n')
    code, out, _ = run(capsys, "--config", str(path), "health")
    assert code == 1
    assert "Storage" in out and "FAIL" in out
    assert "Status: UNHEALTHY" in out


def test_health_failure_config(capsys, config_file):
    path = config_file('[logging]\nlevel = "LOUD"\n')
    code, out, _ = run(capsys, "--config", str(path), "health")
    assert code == 1
    assert "Configuration" in out and "logging.level" in out
    assert "Status: UNHEALTHY" in out


def test_health_failure_database(capsys, cfg, tmp_path):
    db_path = tmp_path / "data" / "s.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"this is not a sqlite database at all" * 100)
    code, out, _ = run(capsys, "--config", str(cfg), "health")
    assert code == 1
    assert "Database" in out and "FAIL" in out


def test_validate_success(capsys, cfg):
    files = sorted(str(p) for p in EXAMPLES.glob("*.json"))
    code, out, _ = run(capsys, "--config", str(cfg), "validate", *files)
    assert code == 0
    assert any(line.startswith("Event fixtures") and " OK" in line for line in out.splitlines())
    assert out.count(" OK") >= 4 + len(files)
    assert out.strip().endswith("Status: VALID")


def test_validate_existing_database(capsys, cfg):
    run(capsys, "--config", str(cfg), "health")  # creates the DB
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0
    assert "Database schema (configured)" in out


@pytest.mark.parametrize("name", sorted(p.name for p in INVALID.glob("*.json")))
def test_validate_failure_message(capsys, cfg, name):
    code, out, _ = run(capsys, "--config", str(cfg), "validate", str(INVALID / name))
    assert code == 1
    assert "FAIL" in out and "Status: INVALID" in out


def test_validate_failure_config(capsys, config_file):
    code, out, _ = run(capsys, "--config", str(config_file("[nope]\n")), "validate")
    assert code == 1
    assert "Status: INVALID" in out


def test_validate_missing_file(capsys, cfg, tmp_path):
    code, out, _ = run(capsys, "--config", str(cfg), "validate", str(tmp_path / "missing.json"))
    assert code == 1


def test_version(capsys):
    code, out, _ = run(capsys, "version")
    assert code == 0
    assert "contract 0.2" in out and f"schema v{SCHEMA_VERSION}" in out


def test_events_and_replay(capsys, cfg, tmp_path):
    with Database(tmp_path / "data" / "s.db") as db:
        store = EventStore(db)
        for seq in (1, 2, 3):
            store.append(new_event("task.progress", {"summary": f"s{seq}"}, request_id="req_1", task_id="tsk_1", seq=seq))
    code, out, _ = run(capsys, "--config", str(cfg), "replay", "tsk_1", "--after-seq", "1")
    assert code == 0
    assert [json.loads(line)["seq"] for line in out.splitlines()] == [2, 3]
    code, out, _ = run(capsys, "--config", str(cfg), "events", "--task", "tsk_1")
    assert code == 0 and len(out.splitlines()) == 3


def test_replay_without_database(capsys, cfg):
    code, _, err = run(capsys, "--config", str(cfg), "replay", "tsk_1")
    assert code == 1 and "does not exist" in err


def test_usage_error():
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2


def test_module_entry_point(cfg):
    proc = subprocess.run(
        [sys.executable, "-m", "siamang", "--config", str(cfg), "health"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Status: HEALTHY" in proc.stdout
