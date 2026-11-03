"""`siamang analyst`: read-only policy view; no analysis, no arbitrary path inspection."""

import json

import pytest

from siamang.analyst import inspection as inspection_mod
from siamang.analyst import strategy as strategy_mod
from siamang.cli.main import main


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "analyst")
    lines = {line[:27].strip(): line[27:].strip() for line in out.splitlines()}
    assert code == 0 and lines["strategy"] == "analysis.basic (deterministic)"
    assert lines["max files read"] == "20" and lines["max depth"] == "3"
    assert lines["writes / follows symlinks"] == "no / no" and lines["reads secret files"] == "no (names only)"


def test_json(capsys):
    code, out, _ = run(capsys, "analyst", "--json")
    d = json.loads(out)
    assert code == 0 and d["strategies"] == {"analysis.basic": "1"}
    assert d["policy"]["writes"] is False and d["persistence"] is False and d["events"] is False


@pytest.mark.parametrize("argv", [["run", "prj_01ABC"], ["execute"], ["modify"], ["inspect", "/etc"],
                                  ["read", "../../etc/passwd"], ["analyze", "tsk_01ABC"]])
def test_no_execution_or_path_command(argv):
    with pytest.raises(SystemExit) as info:
        main(["analyst", *argv])
    assert info.value.code == 2


def test_never_inspects_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("project inspected")

    monkeypatch.setattr(inspection_mod.ProjectInspector, "list_dir", boom)
    monkeypatch.setattr(inspection_mod.ProjectInspector, "read_text", boom)
    monkeypatch.setattr(strategy_mod.BasicStrategy, "analyze", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "analyst")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Analyst") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()
