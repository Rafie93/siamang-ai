"""`siamang qa`: read-only policy view; nothing is verified, run, or written."""

import json

import pytest

from siamang.cli.main import main
from siamang.programmer import snapshot as snapshot_mod
from siamang.qa import service as service_mod


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "qa")
    lines = {line[:29].strip(): line[29:].strip() for line in out.splitlines()}
    assert code == 0 and lines["strategy"] == "qa.static (deterministic, static)"
    assert lines["statuses"] == "pass, fail, inconclusive" and lines["max changes"] == "20"
    assert lines["executes code / uses models"] == "no / no" and lines["writes / retries"] == "no / no"
    assert lines["pass means approved"] == "no"


def test_json(capsys):
    code, out, _ = run(capsys, "qa", "--json")
    d = json.loads(out)
    assert code == 0 and d["strategy"] == "qa.static" and d["statuses"] == ["pass", "fail", "inconclusive"]
    assert d["policy"]["executes_code"] is False and d["policy"]["uses_models"] is False
    assert d["persistence"] is False and d["events"] is False and "patch_integrity" in d["checks"]


@pytest.mark.parametrize("argv", [["run"], ["verify", "pch_01ABC"], ["test"], ["apply"], ["fix"], ["review"],
                                  ["approve"], ["pytest", "/etc"]])
def test_no_run_or_mutating_command(argv):
    with pytest.raises(SystemExit) as info:
        main(["qa", *argv])
    assert info.value.code == 2


def test_never_verifies_reads_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("qa ran")

    monkeypatch.setattr(service_mod.QAService, "verify", boom)
    monkeypatch.setattr(snapshot_mod.ProjectSnapshot, "before", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "qa")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("QA ") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()
