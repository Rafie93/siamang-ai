"""`siamang planner`: read-only policy view; there is no planner run/execute/create."""

import json

import pytest

from siamang.cli.main import main
from siamang.planner import strategy as strategy_mod


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "planner")
    lines = {line[:26].strip(): line[26:].strip() for line in out.splitlines()}
    assert code == 0 and lines["strategy"] == "template.basic (deterministic)"
    assert lines["max steps"] == "12" and lines["empty plan allowed"] == "no"
    assert lines["executes / changes tasks"] == "no" and lines["persists / emits events"] == "no"


def test_json(capsys):
    code, out, _ = run(capsys, "planner", "--json")
    d = json.loads(out)
    assert code == 0 and d["strategies"] == {"template.basic": "1"} and d["default_strategy"] == "template.basic"
    assert d["policy"]["max_steps"] == 12 and d["persistence"] is False and d["events"] is False


@pytest.mark.parametrize("sub", ["run", "execute", "create", "plan", "policy-set"])
def test_no_execution_or_planning_command(sub):
    with pytest.raises(SystemExit) as info:
        main(["planner", sub, "tsk_01ABC"])
    assert info.value.code == 2


def test_never_plans_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("planned")

    monkeypatch.setattr(strategy_mod.TemplateStrategy, "propose", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "planner")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Planner") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()
