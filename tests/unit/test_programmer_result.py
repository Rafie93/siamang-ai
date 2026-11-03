"""ProgrammingResult: fixed statuses with invariants, immutable, bounded, deterministic hash, safe metadata."""

import dataclasses

import pytest

from siamang.programmer import Patch, ProgrammingResult, ProgrammingStatus, ProposedChange
from siamang.programmer.errors import ProgrammerModelOutputError, ProgrammingResultError
from siamang.programmer.result import NOTES_LIMIT

T, P = "tsk_01ABC", "prj_01ABC"


def make_patch(content="x = 1\n", pid="pch_A1", task=T):
    c = ProposedChange("change_01", "src/a.py", "create", "step_01", "reason", after_content=content, before="absent")
    return Patch(pid, task, P, "Add a.", (c,))


def result(**over):
    data = dict(programming_id="prg_A1", task_id=T, project_id=P, status="completed", summary="Add a.",
                patch=make_patch(), metadata={"strategy": "programming.model", "usage": {"files_read": 1}})
    data.update(over)
    return ProgrammingResult(**data)


ERROR = ProgrammerModelOutputError("model output is not valid JSON", cause="JSONDecodeError").to_dict()


def test_three_statuses():
    assert result().status is ProgrammingStatus.COMPLETED and len(result().changes) == 1
    w = result(status="waiting", patch=None, unknowns=["Authentication architecture not established."])
    assert w.changes == () and w.unknowns
    f = result(status="failed", patch=None, error=ERROR)
    assert f.error["code"] == "programmer_model_invalid_output" and f.changes == ()


@pytest.mark.parametrize("over", [
    {"status": "done"}, {"status": "running"}, {"status": None},
    {"patch": None},  # completed needs a patch
    {"patch": Patch("pch_A1", T, P, "s", ())},  # ... with at least one change
    {"error": ERROR},  # completed has no error
    {"status": "waiting"}, {"status": "waiting", "patch": None},  # waiting: no patch, at least one unknown
    {"status": "failed", "patch": None},  # failed needs an error
    {"status": "failed", "error": ERROR},  # failed has no patch
    {"patch": make_patch(task="tsk_OTHER")},
    {"patch": make_patch().to_dict()},
    {"status": "failed", "patch": None, "error": {"code": "boom", "message": "x", "cause": None, "details": []}},
    {"status": "failed", "patch": None, "error": {"code": "programmer_internal", "message": "x"}},
    {"status": "failed", "patch": None, "error": "programmer_internal"},
])
def test_invariants(over):
    with pytest.raises(ProgrammingResultError):
        result(**over)


@pytest.mark.parametrize("over", [{"programming_id": "prog_1"}, {"task_id": "x y"}, {"summary": ""},
                                  {"summary": "use token ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
                                  {"warnings": ["x"] * (NOTES_LIMIT + 1)}, {"warnings": "one"},
                                  {"unknowns": ["API_KEY=abcd1234efgh5678"]}, {"unknowns": ["x" * 501]},
                                  {"metadata": {"command": "rm -rf /"}}, {"metadata": {"model_id": "m"}},
                                  {"metadata": {"path": "/Users/someone"}}, {"metadata": "x"}])
def test_bounded_and_safe(over):
    with pytest.raises(ProgrammingResultError):
        result(**over)


def test_result_is_deeply_immutable():
    r = result(warnings=["w"], metadata={"usage": {"files_read": 1}, "context_files": ["a.py"]})
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = "failed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.patch = None
    for mutate in (lambda: r.metadata.__setitem__("x", 1), lambda: r.metadata["usage"].__setitem__("files_read", 9),
                   lambda: r.metadata["context_files"].append("b.py"), lambda: r.warnings.append("x"),
                   lambda: r.changes[0].metadata.__setitem__("x", 1)):
        with pytest.raises((TypeError, AttributeError)):
            mutate()
    f = result(status="failed", patch=None, error=ERROR)
    with pytest.raises(TypeError):
        f.error["code"] = "programmer_internal"


def test_hash_excludes_identity():
    a = result(programming_id="prg_ONE", patch=make_patch(pid="pch_ONE"))
    b = result(programming_id="prg_TWO", patch=make_patch(pid="pch_TWO"))
    assert a.content_hash == b.content_hash and a.content() == b.content()
    assert result(patch=make_patch(content="x = 2\n")).content_hash != a.content_hash
    text = str(a.content())
    assert "prg_ONE" not in text and "pch_ONE" not in text
    d = a.to_dict()
    assert d["programming_id"] == "prg_ONE" and d["patch"]["patch_id"] == "pch_ONE" and d["content_hash"] == a.content_hash


def test_error_is_normalized():
    raw = {"code": "programmer_model_unavailable", "message": "failed: api_key=sk-zzz", "cause": "timeout",
           "details": ["a"] * 30}
    f = result(status="failed", patch=None, error=raw)
    assert "sk-zzz" not in f.error["message"] and len(f.error["details"]) == 20
