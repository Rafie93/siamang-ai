"""ToolDefinition: immutable, data-only, strict contracts; validate_input is strict."""

import dataclasses
from types import MappingProxyType

import pytest

from siamang.contracts.schema import F
from siamang.tools import ToolDefinition, ToolErrorCode, ToolOperation
from siamang.tools.definition import FORBIDDEN_INPUT_FIELDS, check_read_only, validate_input
from siamang.tools.errors import ToolDefinitionError, ToolInputError, ToolProjectRequiredError
from siamang.tools.git_tools import GIT_TOOLS
from siamang.tools.permissions import Permission

PID = "prj_01JABCDEFGHJKMNPQRSTVWXYZ0"


def make(**overrides):
    base = dict(
        tool_id="git.status", name="Git Status", description="d", version="1", category="git",
        operation=ToolOperation.READ, implementation="git.status",
        capabilities={Permission.GIT_READ, Permission.PROJECT_READ},
        input_schema={"project_id": F("id", required=True)},
        output_schema={"clean": F("bool", required=True)},
    )
    base.update(overrides)
    return ToolDefinition(**base)


def test_valid_definition_is_immutable():
    d = make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.tool_id = "git.push"
    assert isinstance(d.input_schema, MappingProxyType) and isinstance(d.metadata, MappingProxyType)
    with pytest.raises(TypeError):
        d.input_schema["command"] = F("str")
    assert isinstance(d.capabilities, frozenset)
    assert d.required_permissions == {Permission.GIT_READ, Permission.PROJECT_READ, Permission.TOOL_INVOKE}


def test_to_dict_hides_implementation():
    d = make().to_dict()
    assert "implementation" not in d
    assert {"tool_id", "name", "description", "version", "category", "operation", "mutating", "requires_project",
            "capabilities", "enabled"} <= set(d)


@pytest.mark.parametrize("tool_id", ["", "git", "Git.status", "git..status", "git.status!", "git status", "a.b.c.d.e",
                                     "git/status", 7, None])
def test_bad_tool_ids(tool_id):
    with pytest.raises(ToolDefinitionError):
        make(tool_id=tool_id, category="git")


@pytest.mark.parametrize("tool_id", ["git.run", "git.exec", "git.command", "git.shell", "shell.run", "git.raw",
                                     "process.spawn", "os.system", "git.status.exec"])
def test_raw_execution_tool_ids_are_refused(tool_id):
    with pytest.raises(ToolDefinitionError) as info:
        make(tool_id=tool_id, category=tool_id.split(".")[0])
    assert info.value.code is ToolErrorCode.TOOL_DEFINITION_INVALID


@pytest.mark.parametrize("overrides", [
    {"name": ""}, {"name": "x" * 81}, {"description": "   "}, {"version": "v1"}, {"version": "1.2.3.4"},
    {"category": "fs"}, {"implementation": lambda p, a: {}}, {"implementation": "Not An Id"},
    {"operation": ToolOperation.WRITE}, {"mutating": True}, {"enabled": "yes"},
    {"requires_project": False, "requires_workspace": True, "input_schema": {}},
    {"requires_project": False},  # declares project_id anyway
    {"input_schema": {}}, {"input_schema": {"project_id": F("id")}}, {"input_schema": {"project_id": F("str", required=True)}},
    {"input_schema": {"project_id": F("id", required=True), "opts": F("object")}},
    {"input_schema": {"project_id": F("id", required=True), "items": F("list", item=F("str"))}},
    {"input_schema": {"project_id": F("id", required=True), "blob": F("any")}},
    {"input_schema": {"project_id": F("id", required=True), "x": "not a field"}},
    {"output_schema": {}}, {"output_schema": {"x": F("nope")}},
    {"metadata": {"handler": print}}, {"metadata": {"n": float("nan")}},
])
def test_invalid_definitions(overrides):
    with pytest.raises((ToolDefinitionError, ValueError)):
        make(**overrides)


@pytest.mark.parametrize("name", sorted(FORBIDDEN_INPUT_FIELDS))
def test_execution_and_path_inputs_cannot_be_declared(name):
    with pytest.raises(ToolDefinitionError):
        make(input_schema={"project_id": F("id", required=True), name: F("str")})


def test_unknown_permission_in_capabilities():
    with pytest.raises(ValueError):
        make(capabilities={"root"})


def test_check_read_only():
    check_read_only(make())
    with pytest.raises(ToolDefinitionError):
        check_read_only(make(operation=ToolOperation.WRITE, mutating=True))
    for cap in (Permission.GIT_WRITE, Permission.NETWORK, Permission.PROCESS_EXECUTE, Permission.FILESYSTEM_WRITE,
                Permission.WORKSPACE_WRITE):
        with pytest.raises(ToolDefinitionError):
            check_read_only(make(capabilities={Permission.GIT_READ, cap}))


# --- validate_input --------------------------------------------------------------------

DIFF = next(t for t in GIT_TOOLS if t.tool_id == "git.diff")
STATUS = next(t for t in GIT_TOOLS if t.tool_id == "git.status")


def test_valid_inputs():
    assert validate_input(STATUS, {"project_id": PID}) == {"project_id": PID}
    assert validate_input(DIFF, {"project_id": PID, "cached": False}) == {"project_id": PID, "cached": False}
    assert validate_input(DIFF, {"project_id": PID}) == {"project_id": PID}


def test_input_is_copied():
    data = {"project_id": PID}
    out = validate_input(STATUS, data)
    data["project_id"] = "prj_other"
    assert out["project_id"] == PID


def test_missing_project_is_project_required():
    with pytest.raises(ToolProjectRequiredError) as info:
        validate_input(STATUS, {})
    assert info.value.code is ToolErrorCode.TOOL_PROJECT_REQUIRED


@pytest.mark.parametrize("data", [
    None, [], "project_id=prj_x", ("project_id",), {1: PID}, {"project_id": None}, {"project_id": 42},
    {"project_id": ["prj_x"]}, {"project_id": PID, "cached": "false"}, {"project_id": PID, "cached": 0},
    {"project_id": PID, "cached": None},
])
def test_wrong_shapes_and_types(data):
    tool = DIFF
    with pytest.raises((ToolInputError, ToolProjectRequiredError)):
        validate_input(tool, data)


@pytest.mark.parametrize("pid", ["x_123", "prj_", "prj_x; rm -rf ~", "prj_$(touch x)", "prj_`id`", "prj_../../etc",
                                 "prj_/etc/passwd", "prj_a b", "prj_x\n", "PRJ_x", " prj_x", "prj_" + "a" * 200])
def test_malformed_project_ids(pid):
    with pytest.raises(ToolInputError):
        validate_input(STATUS, {"project_id": pid})


@pytest.mark.parametrize("field", ["command", "argv", "shell_command", "git_args", "script", "python", "callable",
                                   "path", "root_path", "cwd", "args", "env"])
def test_execution_style_inputs_are_refused(field):
    with pytest.raises(ToolInputError) as info:
        validate_input(STATUS, {"project_id": PID, field: ["push", "origin"]})
    assert info.value.message == "execution-style or path input is not accepted"
    assert info.value.details == (field,)


def test_unknown_field_is_refused():
    with pytest.raises(ToolInputError) as info:
        validate_input(STATUS, {"project_id": PID, "verbose": True})
    assert info.value.message == "unknown input field(s)"


def test_input_errors_are_serializable_and_value_free():
    with pytest.raises(ToolInputError) as info:
        validate_input(STATUS, {"project_id": PID, "token": "ghp_SECRET", "command": "x"})
    d = info.value.to_dict()
    assert d["code"] == "tool_input_invalid" and d["tool_id"] == "git.status"
    assert "ghp_SECRET" not in repr(d)
