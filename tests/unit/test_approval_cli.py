"""`siamang approval`: read-only policy view; nothing is approved, rejected, applied, or written."""

import json

import pytest

from siamang.approval import service as service_mod
from siamang.cli.main import main


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text(capsys):
    code, out, _ = run(capsys, "approval")
    lines = {line[:30].strip(): line[30:].strip() for line in out.splitlines()}
    assert code == 0 and lines["decisions"] == "approve, reject (explicit only)"
    assert lines["default approvers"].startswith("none") and lines["overrides"] == "none"
    assert lines["applies / executes / commits"] == "no / no / no"
    assert lines["authenticates approvers"].startswith("no")


def test_json(capsys):
    code, out, _ = run(capsys, "approval", "--json")
    d = json.loads(out)
    assert code == 0 and d["decisions"] == ["approve", "reject"] and d["statuses"] == ["approved", "rejected"]
    assert d["policy"]["authorized_approvers"] == [] and d["policy"]["overrides"] is False
    assert d["authentication"] is False and d["persistence"] is False and d["events"] is False
    assert "patch_application" in d["not_done"]


@pytest.mark.parametrize("argv", [["approve"], ["reject"], ["approve", "pch_01A"], ["grant"], ["apply"],
                                  ["--approver", "admin"], ["--force"], ["--override"]])
def test_no_decision_or_mutating_command(argv):
    with pytest.raises(SystemExit) as info:
        main(["approval", *argv])
    assert info.value.code == 2
    with pytest.raises(SystemExit):
        main(["approve"])


def test_never_decides_or_creates_a_database(capsys, monkeypatch, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("approval decided")

    monkeypatch.setattr(service_mod.ApprovalService, "decide", boom)
    monkeypatch.setattr(service_mod.ApprovalService, "verify", boom)
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert run(capsys, "--config", str(cfg), "approval")[0] == 0
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Approval ") and "OK" in line for line in out.splitlines())
    assert not (tmp_path / "s.db").exists()


def test_validate_check_fails_on_a_permissive_policy(monkeypatch):
    from siamang.approval import policy as policy_mod
    from siamang.cli.checks import check_approval

    assert check_approval().status == "OK"
    real = policy_mod.ApprovalPolicy.to_dict
    for override in ({"authorized_approvers": ["anyone"]}, {"overrides": True}, {"applies_patches": True}):
        monkeypatch.setattr(policy_mod.ApprovalPolicy, "to_dict", lambda self, o=override: {**real(self), **o})
        assert check_approval().failed
