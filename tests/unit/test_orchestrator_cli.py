"""`siamang orchestrator`: read-only policy view; there is no orchestrator run/execute/invoke."""

import json

import pytest

from siamang.agents.reference import EchoAgent
from siamang.cli.main import main


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "orchestrator")
    assert code == 0
    lines = {line[:26].strip(): line[26:].strip() for line in out.splitlines()}
    assert lines["allowed task status"] == "running"
    assert lines["agents per orchestration"] == "1"
    assert lines["orchestratable agents"] == "agent.echo"
    assert lines["changes task state"] == "no" and lines["retries"] == "no" and lines["chains agents"] == "no"


def test_json(capsys):
    code, out, _ = run(capsys, "orchestrator", "--json")
    d = json.loads(out)
    assert code == 0 and d["agents"] == ["agent.echo"] and d["persistence"] is False and d["events"] is False
    assert d["policy"]["allowed_task_statuses"] == ["running"] and d["policy"]["agents_per_orchestration"] == 1


@pytest.mark.parametrize("sub", ["run", "execute", "invoke", "start", "plan"])
def test_no_execution_command(sub):
    with pytest.raises(SystemExit) as info:
        main(["orchestrator", sub, "tsk_01ABC"])
    assert info.value.code == 2


def test_never_runs_an_agent_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("agent executed")

    monkeypatch.setattr(EchoAgent, "run", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "orchestrator")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Orchestrator") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()
