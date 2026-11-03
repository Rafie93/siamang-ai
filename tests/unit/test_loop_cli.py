"""`siamang loop`: read-only policy view; no round is run, nothing is applied, approved, or written."""

import json

import pytest

from siamang.cli.main import main
from siamang.loop import service as service_mod


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "loop")
    lines = {line[:28].strip(): line[28:].strip() for line in out.splitlines()}
    assert code == 0 and lines["strategy"] == "loop.bounded (bounded, deterministic)"
    assert lines["max iterations"] == "3" and lines["max calls (prog/qa/rev)"] == "3 / 3 / 3"
    assert lines["applies / approves patches"] == "no / no" and lines["pass means approved"] == "no"


def test_json(capsys):
    code, out, _ = run(capsys, "loop", "--json")
    d = json.loads(out)
    assert code == 0 and d["round"] == ["programmer", "qa", "reviewer"] and d["policy"]["max_iterations"] == 3
    assert d["statuses"] == ["pass", "concern", "fail", "inconclusive"] and "no_progress" in d["stop_reasons"]
    assert d["policy"]["applies_patches"] is False and d["policy"]["approves"] is False
    assert "approval" in d["not_done"] and d["persistence"] is False and d["events"] is False
    assert len(out) < 4096


@pytest.mark.parametrize("argv", [["run"], ["run", "tsk_01A"], ["apply"], ["approve"], ["commit"], ["retry"],
                                  ["--max-iterations", "100"], ["--unlimited"]])
def test_no_run_or_mutating_command(argv):
    with pytest.raises(SystemExit) as info:
        main(["loop", *argv])
    assert info.value.code == 2


def test_never_runs_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("loop ran")

    monkeypatch.setattr(service_mod.LoopService, "run", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "loop")[0] == 0
    assert run(capsys, "--config", str(cfg), "loop", "--json")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Loop ") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()


def test_validate_check_fails_on_an_unbounded_or_approving_policy(monkeypatch):
    from siamang.cli.checks import check_loop
    from siamang.loop import policy as policy_mod

    assert check_loop().status == "OK"
    real = policy_mod.LoopPolicy.to_dict
    for override in ({"approves": True}, {"applies_patches": True}, {"max_iterations": 50}):
        monkeypatch.setattr(policy_mod.LoopPolicy, "to_dict", lambda self, o=override: {**real(self), **o})
        assert check_loop().failed
