"""Task domain values: strict requests, normalized failures/results, metadata policy."""

import dataclasses
import json

import pytest

from siamang.tasks import InvalidTaskError, TaskFailure, TaskRequest, TaskResult
from siamang.tasks.definition import (
    FORBIDDEN_KEYS,
    canonical_json_object,
    format_ts,
    normalize_title,
    parse_priority,
    parse_source,
    parse_status,
    validate_description,
    validate_max_retries,
    validate_project_id,
    validate_task_id,
)
from tests.support.projects import T0

PID = "prj_01JABCDEFGHJKMNPQRSTVWXYZ0"


@pytest.mark.parametrize("bad", ["", "tsk_", "x_1", "tsk_a b", "tsk_x\n", "tsk_x\r\n", "tsk_$(id)", "tsk_../x",
                                 "TSK_1", 5, None, "tsk_" + "a" * 130])
def test_bad_task_ids(bad):
    with pytest.raises(InvalidTaskError):
        validate_task_id(bad)


@pytest.mark.parametrize("bad", ["prj_x\n", "prj_", "x", "prj_a;b", "/w/p", "../prj_x", None, 1])
def test_bad_project_ids(bad):
    with pytest.raises(InvalidTaskError) as info:
        validate_project_id(bad)
    assert info.value.field == "project_id"


def test_enums():
    assert str(parse_status("waiting")) == "waiting"
    assert str(parse_priority("urgent")) == "urgent"
    assert str(parse_source("telegram")) == "telegram"
    for fn, bad in ((parse_status, "accepted"), (parse_status, "waiting_approval"), (parse_priority, "critical"),
                    (parse_source, "voice"), (parse_source, None)):
        with pytest.raises(InvalidTaskError):
            fn(bad)


@pytest.mark.parametrize("value", [-1, 11, True, 2.0, "3", None])
def test_max_retries_bounds(value):
    with pytest.raises(InvalidTaskError):
        validate_max_retries(value)


def test_description_and_title():
    assert validate_description("  do it \n detail ") == "do it \n detail"
    for bad in ("", "   ", "x\x00y", "a\x1b[31m", 3, "x" * 10_001):
        with pytest.raises(InvalidTaskError):
            validate_description(bad)
    assert normalize_title(None, "\n  First   line\nsecond") == "First line"
    assert len(normalize_title(None, "x" * 500)) == 200
    assert normalize_title("  A   title ", "d") == "A title"
    for bad in ("", "a\nb", "x" * 201, 7):
        with pytest.raises(InvalidTaskError):
            normalize_title(bad, "d")


def test_fixed_width_timestamps_sort_chronologically():
    a = format_ts(T0)
    b = format_ts(T0.replace(microsecond=500_000))
    assert a == "2026-10-04T10:00:00.000Z" and b == "2026-10-04T10:00:00.500Z" and a < b


# --- metadata (project policy + task key rules) ------------------------------------------


def test_metadata_valid():
    assert json.loads(canonical_json_object({"language": "python", "labels": ["a"]})) == {
        "labels": ["a"], "language": "python"}
    assert canonical_json_object(None) == "{}"


@pytest.mark.parametrize("bad", [
    {"api_key": "x"}, {"password": "x"}, {"auth_token": "x"}, {"client_secret": "x"},
    {"nested": {"private_key": "x"}}, {"credential": "x"}, {"x": [{"credentials": "y"}]},
    {"command": "rm -rf /"}, {"shell": "bash"}, {"argv": ["git", "push"]}, {"opts": {"argv": []}},
    {"cwd": "/"}, {"root_path": "/Users/x"}, {"deep": {"path": "/etc"}}, {"git_args": ["push"]},
    {"python": "import os"}, {"callable": "x"}, {"x": float("nan")}, {"x": float("inf")},
    {"x": b"bytes"}, {"x": object()}, {"k" * 65: 1}, {f"k{i}": i for i in range(65)},
    {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}, {"blob": "x" * 17_000}, ["not", "an", "object"], "str",
    {"bad key!": 1}, {"language\n": "py"},
])
def test_metadata_refused(bad):
    with pytest.raises(InvalidTaskError) as info:
        canonical_json_object(bad)
    assert info.value.field == "metadata"


def test_metadata_errors_hide_values():
    with pytest.raises(InvalidTaskError) as info:
        canonical_json_object({"api_key": "sk-SECRET-123"})
    assert "sk-SECRET-123" not in str(info.value)


def test_forbidden_keys_cover_the_brief():
    assert {"command", "shell", "argv", "private_key", "credential", "cwd", "root_path", "git_args", "python",
            "callable"} <= FORBIDDEN_KEYS


# --- TaskRequest ------------------------------------------------------------------------


def test_request_from_dict_is_strict():
    r = TaskRequest.from_dict({"project_id": PID, "description": "d", "source": "cli"})
    assert (r.project_id, r.priority, r.max_retries) == (PID, "normal", 3)
    with pytest.raises(InvalidTaskError):
        TaskRequest.from_dict({"project_id": PID, "description": "d"})  # source must be explicit
    with pytest.raises(InvalidTaskError):
        TaskRequest.from_dict({"project_id": PID, "description": "d", "source": "cli", "verbose": True})
    with pytest.raises(InvalidTaskError):
        TaskRequest.from_dict(["x"])


@pytest.mark.parametrize("field", ["shell", "argv", "command", "cwd", "root_path", "git_args", "python", "callable",
                                   "repository_path", "path", "agent_id", "model_id", "tool_id", "branch",
                                   "worktree"])
def test_request_refuses_execution_and_future_ownership_fields(field):
    with pytest.raises(InvalidTaskError) as info:
        TaskRequest.from_dict({"project_id": PID, "description": "d", "source": "cli", field: "x"})
    assert "not accepted" in str(info.value)


def test_request_is_immutable():
    r = TaskRequest(project_id=PID, description="d", source="cli")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.project_id = "prj_other"
    with pytest.raises(TypeError):
        TaskRequest(project_id=PID, description="d", source="cli", argv=["git", "push"])


# --- TaskFailure ------------------------------------------------------------------------


def test_failure_structure_and_sanitizing():
    f = TaskFailure("TOOL_FAILED", "token=ghp_ABC at https://bob:pw@host/r.git\x1b[0m", retryable=True, step="qa",
                    details={"attempt": 2})
    d = f.to_dict()
    assert d == {"code": "TOOL_FAILED", "message": d["message"], "retryable": True, "step": "qa",
                 "details": {"attempt": 2}}
    assert "ghp_ABC" not in d["message"] and "bob:pw" not in d["message"] and "\x1b" not in d["message"]
    assert TaskFailure.from_json(f.to_json()) == f
    assert len(TaskFailure("X", "m" * 5000).message) == 1000


@pytest.mark.parametrize("kwargs", [
    {"code": ""}, {"code": "has space"}, {"code": "x\n"}, {"code": 7}, {"code": "OK", "retryable": "yes"},
    {"code": "OK", "step": 1.5}, {"code": "OK", "step": True}, {"code": "OK", "details": {"password": "x"}},
    {"code": "OK", "details": {"argv": ["x"]}},
])
def test_failure_validation(kwargs):
    with pytest.raises(InvalidTaskError):
        TaskFailure(**kwargs)


def test_failure_from_siamang_style_error():
    class FakeGitError(Exception):
        code = "not_repository"
        retryable = False
        message = "not a Git repository"

    class Retryable(Exception):
        code = "command_timeout"
        retryable = True
        message = "git status timed out after 10s"

    f = TaskFailure.from_exception(FakeGitError(), step="inspect")
    assert (f.code, f.retryable, f.message, f.step) == ("not_repository", False, "not a Git repository", "inspect")
    assert dict(f.details) == {"cause": "FakeGitError"}
    assert TaskFailure.from_exception(Retryable()).retryable is True


def test_failure_from_arbitrary_exception_keeps_only_the_type():
    f = TaskFailure.from_exception(RuntimeError("secret ghp_LEAK in /Users/x/.ssh/id_rsa"))
    assert (f.code, f.message, f.retryable) == ("INTERNAL", "", False)
    assert dict(f.details) == {"cause": "RuntimeError"}
    assert "LEAK" not in f.to_json() and ".ssh" not in f.to_json() and "Traceback" not in f.to_json()

    class Liar(Exception):
        code = "BAD CODE WITH SPACES"
        retryable = True

    assert TaskFailure.from_exception(Liar()).code == "INTERNAL"
    assert TaskFailure.from_exception(Liar()).retryable is False


# --- TaskResult -------------------------------------------------------------------------


def test_result_follows_contract():
    r = TaskResult("done", "partial", result={"tests": {"passed": 3, "failed": 0}, "artifacts": ["a.txt"]},
                   details={"model": "echo-small"})
    assert TaskResult.from_json(r.to_json()) == r
    for kwargs in ({"summary": ""}, {"summary": "x" * 501}, {"summary": "ok", "outcome": "failed"},
                   {"summary": "ok", "result": {"tests": {"passed": -1}}},
                   {"summary": "ok", "result": {"tests": "all"}},
                   {"summary": "ok", "details": {"api_key": "x"}}, {"summary": "ok", "details": {"command": "x"}}):
        with pytest.raises(InvalidTaskError):
            TaskResult(**kwargs)
