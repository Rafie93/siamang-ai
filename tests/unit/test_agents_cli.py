"""`siamang agents list|get`: read-only catalog; there is no `agents run`."""

import json

import pytest

from siamang.agents.reference import EchoAgent
from siamang.cli.main import main

FIELDS = {"agent_id", "name", "description", "version", "role", "capabilities", "model_requirements",
          "tool_permissions", "input_schema", "output_schema", "enabled", "metadata"}


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_list_text(capsys):
    code, out, _ = run(capsys, "agents")
    lines = out.splitlines()
    assert code == 0 and lines[0].split()[0] == "ID" and len(lines) == 2
    assert lines[1].split()[:4] == ["agent.echo", "1", "echo", "yes"]
    assert run(capsys, "agents", "list")[1] == out


def test_list_json(capsys):
    for argv in (("agents", "--json"), ("agents", "list", "--json")):
        code, out, _ = run(capsys, *argv)
        data = json.loads(out)
        assert code == 0 and [a["agent_id"] for a in data] == ["agent.echo"]
        assert set(data[0]) == FIELDS and "implementation" not in data[0]
        assert data[0]["tool_permissions"] == [] and data[0]["model_requirements"] == []


def test_get(capsys):
    code, out, _ = run(capsys, "agents", "get", "agent.echo", "--json")
    d = json.loads(out)
    assert code == 0 and d["agent_id"] == "agent.echo" and d["metadata"] == {"reference": True}
    assert d["input_schema"] == {"message": {"kind": "text", "required": True}}
    code, out, _ = run(capsys, "agents", "get", "agent.echo")
    assert code == 0 and "agent_id" in out and "agent.echo" in out and "capabilities" in out


@pytest.mark.parametrize("agent_id", ["agent.programmer", "agent.echo\n", "echo"])
def test_get_unknown(capsys, agent_id):
    code, out, err = run(capsys, "agents", "get", agent_id)
    assert code == 1 and out == "" and "agent_not_found" in err


@pytest.mark.parametrize("sub", ["run", "invoke", "exec", "execute", "call", "register"])
def test_no_execution_command(sub):
    with pytest.raises(SystemExit) as info:
        main(["agents", sub, "agent.echo"])
    assert info.value.code == 2


def test_catalog_never_runs_an_agent(capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("agent executed")

    monkeypatch.setattr(EchoAgent, "run", boom)
    assert run(capsys, "agents")[0] == 0
    assert run(capsys, "agents", "get", "agent.echo")[0] == 0


def test_validate_reports_agents_without_running_them(capsys, config_file, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("agent executed")

    monkeypatch.setattr(EchoAgent, "run", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Agents") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()  # validate creates nothing
