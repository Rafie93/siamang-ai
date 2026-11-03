"""`siamang reviewer`: read-only policy view; nothing is reviewed, approved, applied, or written."""

import json

import pytest

from siamang.cli.main import main
from siamang.reviewer import service as service_mod


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "reviewer")
    lines = {line[:30].strip(): line[30:].strip() for line in out.splitlines()}
    assert code == 0 and lines["strategy"] == "review.static (deterministic, static)"
    assert lines["statuses"] == "pass, concern, fail, inconclusive" and lines["max changes"] == "20"
    assert lines["reads project / uses models"] == "no / no" and lines["writes / executes code"] == "no / no"
    assert lines["approves / applies / repairs"] == "no / no / no" and lines["pass means approved"] == "no"


def test_json(capsys):
    code, out, _ = run(capsys, "reviewer", "--json")
    d = json.loads(out)
    assert code == 0 and d["strategy"] == "review.static"
    assert d["statuses"] == ["pass", "concern", "fail", "inconclusive"] and "alignment" in d["dimensions"]
    assert d["policy"]["approves"] is False and d["policy"]["uses_models"] is False and d["policy"]["writes"] is False
    assert "approval" in d["not_verified"] and d["persistence"] is False and d["events"] is False


@pytest.mark.parametrize("argv", [["run"], ["review", "pch_01ABC"], ["approve"], ["reject"], ["apply"], ["merge"],
                                  ["fix"], ["repair"], ["retry"], ["commit"], ["--path", "/etc"]])
def test_no_run_or_mutating_command(argv):
    with pytest.raises(SystemExit) as info:
        main(["reviewer", *argv])
    assert info.value.code == 2


def test_never_reviews_reads_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("reviewer ran")

    monkeypatch.setattr(service_mod.ReviewerService, "review", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "reviewer")[0] == 0
    assert run(capsys, "--config", str(cfg), "reviewer", "--json")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Reviewer ") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()


def test_validate_check_fails_if_the_policy_could_approve(monkeypatch):
    from siamang.cli.checks import check_reviewer
    from siamang.reviewer import policy as policy_mod

    assert check_reviewer().status == "OK"
    real = policy_mod.ReviewerPolicy.to_dict
    monkeypatch.setattr(policy_mod.ReviewerPolicy, "to_dict", lambda self: {**real(self), "approves": True})
    assert check_reviewer().failed
