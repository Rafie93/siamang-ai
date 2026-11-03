import os

import pytest

from siamang.projects import InvalidProjectError, ProjectStatus, ProjectType
from siamang.projects.definition import (
    METADATA_MAX_BYTES,
    canonical_metadata,
    normalize_name,
    normalize_root_path,
    parse_type,
    validate_description,
    validate_project_id,
)


def test_project_type_and_status_enums():
    assert {t.value for t in ProjectType} == {
        "software", "web", "mobile", "desktop", "game", "research", "data", "education", "other",
    }
    assert {s.value for s in ProjectStatus} == {"active", "archived"}
    assert parse_type("web") is ProjectType.WEB
    with pytest.raises(InvalidProjectError) as info:
        parse_type("spaceship")
    assert info.value.field == "project_type"


@pytest.mark.parametrize("good", ["prj_01M43APQC7SY9QWK3C5EWMHDDG", "prj_toko", "prj_a-b.c:d"])
def test_valid_project_ids(good):
    assert validate_project_id(good) == good


@pytest.mark.parametrize("bad", ["toko", "tsk_123", "prj_", "prj_ bad", "PRJ_x", "", None, 5])
def test_invalid_project_ids(bad):
    with pytest.raises(InvalidProjectError):
        validate_project_id(bad)


def test_name_normalization():
    assert normalize_name("  Toko   System  Informasi ") == ("Toko System Informasi", "toko system informasi")
    with pytest.raises(InvalidProjectError):
        normalize_name("tab\tseparated")  # tabs are control characters
    assert normalize_name("Straße")[1] == "strasse"  # casefold, not lower


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101, "bad\x00name", "line\nbreak", None, 3])
def test_invalid_names(bad):
    with pytest.raises(InvalidProjectError) as info:
        normalize_name(bad)
    assert info.value.field == "name"


def test_description():
    assert validate_description("  multi\nline\ttext  ") == "multi\nline\ttext"
    for bad in ("x" * 2001, "bell\x07", None):
        with pytest.raises(InvalidProjectError):
            validate_description(bad)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/Users/someone/web/proj", "/Users/someone/web/proj"),
        ("/Users/someone/web/proj/", "/Users/someone/web/proj"),
        ("/Users/someone/./web//proj", "/Users/someone/web/proj"),
        ("/Users/someone/web/x/../proj", "/Users/someone/web/proj"),
        ("//Users/someone/web/proj", "/Users/someone/web/proj"),
        ("/tmp/proj; rm -rf ~", "/tmp/proj; rm -rf ~"),  # stored as data, never executed
    ],
)
def test_root_path_normalization(raw, expected):
    assert normalize_root_path(raw) == expected


def test_root_path_accepts_pathlike(tmp_path):
    assert normalize_root_path(tmp_path) == os.path.normpath(str(tmp_path))


@pytest.mark.parametrize(
    "bad, match",
    [
        ("relative/path", "absolute"),
        ("~/proj", "absolute"),
        ("", "non-empty"),
        ("/", "filesystem root"),
        ("/../..", "filesystem root"),
        ("/tmp/a\x00b", "control"),
        ("/tmp/a\nb", "control"),
        ("/" + "a" * 5000, "at most"),
        (42, "path string"),
        (b"/tmp/bytes", "path string"),
    ],
)
def test_invalid_root_paths(bad, match):
    with pytest.raises(InvalidProjectError, match=match):
        normalize_root_path(bad)


def test_root_path_rejects_home_directory():
    with pytest.raises(InvalidProjectError, match="home directory"):
        normalize_root_path(os.path.expanduser("~"))
    normalize_root_path(os.path.join(os.path.expanduser("~"), "proj"))  # inside home is fine


def test_metadata_canonical_json():
    assert canonical_metadata(None) == "{}"
    assert canonical_metadata({"b": 1, "a": {"y": [1, 2.5, True, None], "x": "s"}}) == (
        '{"a":{"x":"s","y":[1,2.5,true,null]},"b":1}'
    )
    assert canonical_metadata({"domain": "pendidikan é"}) == '{"domain":"pendidikan é"}'


@pytest.mark.parametrize(
    "bad, match",
    [
        ([1, 2], "must be an object"),
        ("{}", "must be an object"),
        ({"1bad": 1}, "invalid key"),
        ({"has space": 1}, "invalid key"),
        ({"a": {"b c": 1}}, "invalid key"),
        ({"api_key": "sk-x"}, "credential"),
        ({"nested": {"auth_token": "x"}}, "credential"),
        ({"command": "make deploy"}, "executable"),
        ({"Scripts": ["x"]}, "executable"),
        ({"a": float("nan")}, "non-finite"),
        ({"a": float("inf")}, "non-finite"),
        ({"a": {1, 2}}, "unsupported value type"),
        ({"a": object()}, "unsupported value type"),
        ({"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}, "nesting"),
        ({f"k{i}": i for i in range(65)}, "top-level keys"),
        ({"big": "x" * METADATA_MAX_BYTES}, "bytes"),
    ],
)
def test_invalid_metadata(bad, match):
    with pytest.raises(InvalidProjectError, match=match):
        canonical_metadata(bad)
