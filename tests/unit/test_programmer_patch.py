"""Patch: one operation per path, no conflicts, bounded, deterministic hash without patch_id."""

import dataclasses

import pytest

from siamang.programmer import Patch, ProposedChange
from siamang.programmer.errors import InvalidPatchError, ProgrammingErrorCode
from siamang.programmer.patch import MAX_CHANGES_LIMIT, MAX_TOTAL_LIMIT
from tests.support.programmer import sha

T, P = "tsk_01ABC", "prj_01ABC"


def ch(n, path, op="modify", content="new\n"):
    kw = {"create": dict(before="absent"), "modify": dict(before="observed", before_hash=sha("old\n")),
          "delete": dict(before="unverified")}[op]
    return ProposedChange(f"change_{n:02d}", path, op, "step_01", "reason",
                          after_content=None if op == "delete" else content, **kw)


def patch(*changes, pid="pch_A1", summary="Summary"):
    return Patch(pid, T, P, summary, tuple(changes))


def refused(*changes):
    with pytest.raises(InvalidPatchError) as info:
        patch(*changes)
    assert info.value.code is ProgrammingErrorCode.PATCH_INVALID
    return info.value


def test_valid_patch():
    p = patch(ch(1, "src/a.py", "create"), ch(2, "src/b.py"), ch(3, "old.py", "delete"))
    assert p.paths == ("src/a.py", "src/b.py", "old.py") and p.size == 8
    assert p.to_dict()["patch_id"] == "pch_A1" and p.to_dict()["content_hash"] == p.content_hash


@pytest.mark.parametrize("first, second", [("create", "create"), ("modify", "modify"), ("delete", "modify"),
                                           ("create", "delete"), ("modify", "delete"), ("delete", "delete")])
def test_one_operation_per_path(first, second):
    error = refused(ch(1, "src/a.py", first), ch(2, "src/a.py", second))
    assert error.details == ("change_01", "change_02")


def test_case_only_duplicates_conflict():
    refused(ch(1, "README.md"), ch(2, "readme.md"))


@pytest.mark.parametrize("a, b", [("src", "src/app.py"), ("src/app.py", "src/app.py/x"), ("a/b", "a/b/c/d")])
def test_file_directory_conflicts(a, b):
    refused(ch(1, a, "create"), ch(2, b, "create"))
    refused(ch(1, b, "create"), ch(2, a, "delete"))


def test_similar_but_distinct_paths_are_fine():
    assert patch(ch(1, "src/app.py"), ch(2, "src/app.py.bak", "create"), ch(3, "src/app", "create")).changes


def test_duplicate_change_ids_and_types():
    refused(ch(1, "a.py"), ch(1, "b.py"))
    with pytest.raises(InvalidPatchError):
        patch({"path": "a.py"})
    with pytest.raises(InvalidPatchError):
        Patch("pch_A1", T, P, "s", "not a list")

    class Sneaky(ProposedChange):
        pass

    good = ch(1, "a.py")
    sneaky = Sneaky(**{f.name: getattr(good, f.name) for f in dataclasses.fields(ProposedChange) if f.init})
    refused(sneaky)


def test_bounds():
    many = [ch(i, f"f{i}.py", "create") for i in range(1, MAX_CHANGES_LIMIT + 2)]
    assert "at most" in refused(*many).message
    big = "x" * (1024 * 1024)
    chunks = [ch(i, f"big{i}.py", "create", big) for i in range(1, MAX_TOTAL_LIMIT // len(big) + 2)]
    assert "exceeds" in refused(*chunks).message


@pytest.mark.parametrize("over", [{"pid": "patch_1"}, {"pid": ""}, {"summary": ""}, {"summary": "x" * 4001}])
def test_identity_and_summary(over):
    with pytest.raises(InvalidPatchError):
        patch(ch(1, "a.py"), **over)
    with pytest.raises(InvalidPatchError):
        Patch("pch_A1", "tsk_../x", P, "s", ())


def test_hash_is_deterministic_and_excludes_patch_id():
    a = patch(ch(1, "a.py"), pid="pch_ONE")
    b = patch(ch(1, "a.py"), pid="pch_TWO")
    assert a.patch_id != b.patch_id and a.content_hash == b.content_hash and a.content() == b.content()
    assert a.content_hash != patch(ch(1, "a.py", content="other\n")).content_hash
    assert a.content_hash != patch(ch(1, "b.py")).content_hash
    assert a.content_hash.startswith("sha256:") and "pch_ONE" not in str(a.content())


def test_patch_is_immutable():
    p = patch(ch(1, "a.py"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.changes = ()
    assert isinstance(p.changes, tuple)
    with pytest.raises(AttributeError):
        p.changes.append(ch(2, "b.py"))
