"""Regression: identifier patterns must reject a trailing newline (`$` matches before a final "\\n")."""

import pytest

from siamang.contracts.envelope import new_event
from siamang.contracts import ContractError, EventType
from siamang.contracts.schema import F, ID_RE, check_object
from siamang.projects import InvalidProjectError, ProjectRegistry
from siamang.projects.definition import (
    METADATA_KEY_RE,
    PROJECT_ID_RE,
    canonical_metadata,
    validate_project_id,
)

SUFFIXES = ["\n", "\r\n", "\n\n", " \n"]


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_patterns_reject_trailing_newline(suffix):
    assert not ID_RE.match("tsk_1" + suffix)
    assert not PROJECT_ID_RE.match("prj_x" + suffix)
    assert not METADATA_KEY_RE.match("language" + suffix)


def test_patterns_still_accept_valid_ids():
    assert ID_RE.match("tsk_01JABCDEF") and ID_RE.match("a") and ID_RE.match("x" * 128)
    assert not ID_RE.match("x" * 129)
    assert PROJECT_ID_RE.match("prj_toko") and PROJECT_ID_RE.match("prj_a.b:c-d_e")
    assert METADATA_KEY_RE.match("language")


def test_validate_project_id_rejects_trailing_newline():
    with pytest.raises(InvalidProjectError):
        validate_project_id("prj_x\n")
    assert validate_project_id("prj_x") == "prj_x"


def test_registry_refuses_explicit_id_with_newline(db):
    with pytest.raises(InvalidProjectError):
        ProjectRegistry(db).register(name="N", root_path="/w/n", project_id="prj_x\n")


def test_metadata_key_with_newline_is_refused():
    with pytest.raises(InvalidProjectError):
        canonical_metadata({"language\n": "python"})


def test_contract_id_fields_reject_trailing_newline():
    assert check_object({"x": "tsk_1\n"}, {"x": F("id")})
    assert not check_object({"x": "tsk_1"}, {"x": F("id")})
    with pytest.raises(ContractError):
        new_event(EventType.TASK_STARTED, {"summary": "s"}, request_id="req_1", task_id="tsk_1\n", seq=1)
