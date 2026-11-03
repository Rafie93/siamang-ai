"""`siamang programmer`: read-only policy view; nothing is generated, read, or written."""

import json

import pytest

from siamang.cli.main import main
from siamang.programmer import access as access_mod
from siamang.programmer import service as service_mod
from siamang.programmer import snapshot as snapshot_mod


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "programmer")
    lines = {line[:26].strip(): line[26:].strip() for line in out.splitlines()}
    assert code == 0 and lines["strategy"] == "programming.model (via ModelRouter)"
    assert lines["model calls per request"] == "1 (no retry)" and lines["operations"] == "create, modify, delete"
    assert lines["max changes"] == "20" and lines["writes / applies patches"] == "no / no"
    assert lines["executes generated code"] == "no" and lines["persists / emits events"] == "no"


def test_json(capsys):
    code, out, _ = run(capsys, "programmer", "--json")
    d = json.loads(out)
    assert code == 0 and d["strategies"] == {"programming.model": "1"}
    assert d["model_capabilities"] == {"required": ["text_generation"], "preferred": ["structured_output"]}
    assert d["policy"]["writes"] is False and d["policy"]["applies_patches"] is False
    assert d["persistence"] is False and d["events"] is False
    assert not any(k in out.lower() for k in ("api_key", "secret", "token", "password"))


@pytest.mark.parametrize("argv", [["run", "tsk_01ABC"], ["apply"], ["write", "src/a.py"], ["execute"],
                                  ["commit"], ["patch", "/etc/passwd"], ["program", "tsk_01ABC"]])
def test_no_run_or_write_command(argv):
    with pytest.raises(SystemExit) as info:
        main(["programmer", *argv])
    assert info.value.code == 2


def test_never_programs_reads_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("programmer ran")

    monkeypatch.setattr(service_mod.Programmer, "program", boom)
    monkeypatch.setattr(access_mod.ProgrammerModelAccess, "generate", boom)
    monkeypatch.setattr(snapshot_mod.ProjectSnapshot, "before", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "programmer")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Programmer") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()
