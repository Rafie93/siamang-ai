"""`siamang tools list|get`: read-only catalog; there is no `tools run`."""

import json

import pytest

from siamang.cli.main import main

FIELDS = {"tool_id", "name", "description", "version", "category", "operation", "mutating", "requires_project",
          "capabilities", "enabled"}


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_list_text(capsys):
    code, out, _ = run(capsys, "tools")
    lines = out.splitlines()
    assert code == 0 and lines[0].split()[0] == "ID" and len(lines) == 6
    assert [line.split()[0] for line in lines[1:]] == ["git.diff", "git.diff_cached", "git.inspect", "git.status",
                                                       "git.worktrees"]
    assert all(" no " in line for line in lines[1:])  # mutating = no
    assert run(capsys, "tools", "list")[1] == out


def test_list_json(capsys):
    for argv in (("tools", "--json"), ("tools", "list", "--json")):
        code, out, _ = run(capsys, *argv)
        data = json.loads(out)
        assert code == 0 and len(data) == 5
        for t in data:
            assert FIELDS <= set(t)
            assert "implementation" not in t
            assert t["mutating"] is False and t["enabled"] is True and t["operation"] == "read"
            assert set(t["capabilities"]) == {"project.read", "workspace.read", "git.read"}


def test_get(capsys):
    code, out, _ = run(capsys, "tools", "get", "git.diff", "--json")
    d = json.loads(out)
    assert code == 0 and d["tool_id"] == "git.diff"
    assert d["input_schema"] == {"project_id": {"kind": "id", "required": True},
                                 "cached": {"kind": "bool", "required": False}}
    code, out, _ = run(capsys, "tools", "get", "git.status")
    assert code == 0 and "tool_id" in out and "git.status" in out and "mutating" in out


def test_get_unknown(capsys):
    code, out, err = run(capsys, "tools", "get", "git.push")
    assert code == 1 and out == "" and "tool_not_found" in err


@pytest.mark.parametrize("sub", ["run", "invoke", "exec", "call"])
def test_no_invocation_command(sub):
    with pytest.raises(SystemExit) as info:
        main(["tools", sub, "git.status"])
    assert info.value.code == 2


def test_validate_reports_tools(capsys, config_file, tmp_path):
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    code, out, _ = run(capsys, "--config", str(cfg), "validate")
    assert code == 0 and "Status: VALID" in out
    assert any(line.startswith("Tools") and "OK" in line for line in out.splitlines())
