import json
from datetime import timedelta

import pytest

from siamang.ids import ulid
from siamang.projects import (
    DuplicateProjectError,
    InvalidProjectError,
    Project,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectRegistry,
    ProjectStatus,
    ProjectType,
)
from siamang.storage import Database
from siamang.storage.projects import ProjectStore
from tests.support.projects import T0, Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def reg(db, clock):
    return ProjectRegistry(db, clock=clock)


def _register(reg, name="Toko", path="/work/toko", **kw):
    return reg.register(name=name, root_path=path, **kw)


# --- identity ----------------------------------------------------------------


def test_project_id_generated(reg):
    p = _register(reg)
    assert p.project_id.startswith("prj_") and len(p.project_id) == 4 + 26


def test_project_ids_unique(reg):
    ids = {_register(reg, name=f"p{i}", path=f"/work/p{i}").project_id for i in range(50)}
    assert len(ids) == 50


def test_explicit_project_id(reg):
    assert _register(reg, project_id="prj_toko").project_id == "prj_toko"


def test_project_id_stable_across_changes(reg, clock):
    p = _register(reg)
    clock.advance()
    q = reg.update(p.project_id, name="Toko v2", root_path="/elsewhere/toko", metadata={"x": 1})
    reg.archive(p.project_id)
    reg.restore(p.project_id)
    assert q.project_id == reg.get(p.project_id).project_id == p.project_id


# --- registration ---------------------------------------------------------------


def test_register_valid_project(reg):
    p = _register(reg, project_type="web", description="Sistem informasi", metadata={"language": "python"})
    assert isinstance(p, Project)
    assert (p.name, p.root_path, p.project_type, p.status) == ("Toko", "/work/toko", ProjectType.WEB, ProjectStatus.ACTIVE)
    assert p.description == "Sistem informasi"
    assert dict(p.metadata) == {"language": "python"}
    assert p.created_at == p.updated_at == T0
    assert p.is_active
    assert reg.get(p.project_id) == p


def test_defaults(reg):
    p = _register(reg)
    assert p.project_type is ProjectType.OTHER and p.description == "" and dict(p.metadata) == {}


def test_duplicate_id(reg):
    _register(reg, project_id="prj_x")
    with pytest.raises(DuplicateProjectError) as info:
        _register(reg, name="Other", path="/work/other", project_id="prj_x")
    assert (info.value.field, info.value.existing_project_id) == ("project_id", "prj_x")


def test_duplicate_active_name_case_insensitive(reg):
    first = _register(reg, name="Toko System")
    with pytest.raises(DuplicateProjectError) as info:
        _register(reg, name="  toko   SYSTEM ", path="/work/other")
    assert info.value.field == "name" and info.value.existing_project_id == first.project_id


def test_duplicate_active_root_path(reg):
    first = _register(reg, path="/work/toko")
    with pytest.raises(DuplicateProjectError) as info:
        _register(reg, name="Other", path="/work/./toko/")  # same path after normalization
    assert info.value.field == "root_path" and info.value.existing_project_id == first.project_id


def test_archived_name_and_path_can_be_reused(reg):
    old = _register(reg)
    reg.archive(old.project_id)
    new = _register(reg)  # same name + same path, new identity
    assert new.project_id != old.project_id
    assert reg.get(old.project_id).status is ProjectStatus.ARCHIVED


@pytest.mark.parametrize(
    "kw, field",
    [
        ({"name": ""}, "name"),
        ({"path": "relative"}, "root_path"),
        ({"project_type": "spaceship"}, "project_type"),
        ({"metadata": {"password": "x"}}, "metadata"),
        ({"metadata": ["not", "object"]}, "metadata"),
        ({"description": 5}, "description"),
        ({"project_id": "tsk_wrongprefix"}, "project_id"),
    ],
)
def test_invalid_project(reg, db, kw, field):
    with pytest.raises(InvalidProjectError) as info:
        _register(reg, **kw)
    assert info.value.field == field
    assert ProjectStore(db).count() == 0


def test_check_exists(reg, tmp_path):
    p = _register(reg, path=tmp_path, check_exists=True)
    assert p.root_path == str(tmp_path)
    with pytest.raises(InvalidProjectError, match="does not exist"):
        _register(reg, name="Missing", path=tmp_path / "missing", check_exists=True)
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(InvalidProjectError, match="not a directory"):
        _register(reg, name="File", path=tmp_path / "file.txt", check_exists=True)
    # existence is not required by default (a project may be planned before it exists)
    assert _register(reg, name="Planned", path=tmp_path / "later").root_path.endswith("later")


# --- retrieval -----------------------------------------------------------------


def test_get_unknown(reg):
    with pytest.raises(ProjectNotFoundError):
        reg.get("prj_ghost")
    with pytest.raises(LookupError):
        reg.get(None)
    assert reg.exists("prj_ghost") is False


def test_get_by_name(reg):
    p = _register(reg, name="Toko System Informasi")
    assert reg.get_by_name("toko  system informasi") == p
    reg.archive(p.project_id)
    with pytest.raises(ProjectNotFoundError):
        reg.get_by_name("Toko System Informasi")  # archived projects are not resolved by name


def test_find_by_root_path(reg):
    p = _register(reg, path="/work/toko")
    assert reg.find_by_root_path("/work/toko/") == p
    assert reg.find_by_root_path("/work/other") is None


def test_list_sorted_and_filtered(reg):
    b = _register(reg, name="beta", path="/w/b", project_type="game")
    a = _register(reg, name="Alpha", path="/w/a", project_type="web")
    c = _register(reg, name="charlie", path="/w/c", project_type="web")
    reg.archive(c.project_id)
    assert [p.name for p in reg.list()] == ["Alpha", "beta", "charlie"]
    assert [p.name for p in reg.list(status="active")] == ["Alpha", "beta"]
    assert [p.name for p in reg.list(status=ProjectStatus.ARCHIVED)] == ["charlie"]
    assert [p.project_id for p in reg.list(project_type="web")] == [a.project_id, c.project_id]
    assert reg.list(project_type="data") == []
    with pytest.raises(InvalidProjectError):
        reg.list(status="running")
    assert b in reg.list()


# --- update ----------------------------------------------------------------------


def test_update_fields(reg, clock):
    p = _register(reg, metadata={"language": "python", "framework": "fastapi"})
    clock.advance(60)
    q = reg.update(
        p.project_id,
        name="Toko 2",
        description="New",
        project_type="web",
        root_path="/work/toko2",
        metadata={"language": "php"},
    )
    assert (q.name, q.description, q.project_type, q.root_path) == ("Toko 2", "New", ProjectType.WEB, "/work/toko2")
    assert dict(q.metadata) == {"language": "php"}  # replaced, not merged
    assert q.created_at == T0
    assert q.updated_at == T0 + timedelta(seconds=60)
    assert reg.get(p.project_id) == q


@pytest.mark.parametrize(
    "kw",
    [{"name": "Renamed"}, {"description": "d"}, {"metadata": {"a": 1}}, {"root_path": "/work/moved"}, {"project_type": "data"}],
)
def test_each_update_changes_updated_at(reg, clock, kw):
    p = _register(reg)
    clock.advance(5)
    q = reg.update(p.project_id, **kw)
    assert q.updated_at > p.updated_at and q.created_at == p.created_at


def test_noop_update(reg, clock):
    p = _register(reg, name="Toko", metadata={"a": 1})
    clock.advance(10)
    assert reg.update(p.project_id) == p
    assert reg.update(p.project_id, name=" toko ".replace("t", "T", 1).strip(), metadata={"a": 1}) == p
    assert reg.get(p.project_id).updated_at == T0


def test_update_name_case_only_is_allowed(reg, clock):
    p = _register(reg, name="toko")
    clock.advance()
    assert reg.update(p.project_id, name="TOKO").name == "TOKO"  # same key, same project: no conflict


def test_update_conflicts(reg):
    _register(reg, name="A", path="/w/a")
    b = _register(reg, name="B", path="/w/b")
    with pytest.raises(DuplicateProjectError) as info:
        reg.update(b.project_id, name="a")
    assert info.value.field == "name"
    with pytest.raises(DuplicateProjectError) as info:
        reg.update(b.project_id, root_path="/w/a")
    assert info.value.field == "root_path"
    assert reg.get(b.project_id) == b


def test_update_invalid_and_unknown(reg):
    p = _register(reg)
    with pytest.raises(InvalidProjectError):
        reg.update(p.project_id, metadata={"exec": "x"})
    with pytest.raises(ProjectNotFoundError):
        reg.update("prj_ghost", name="x")
    assert reg.get(p.project_id) == p


def test_updated_at_never_goes_backwards(reg, clock):
    p = _register(reg)
    clock.now = T0 - timedelta(hours=1)  # clock skew
    q = reg.update(p.project_id, name="Later")
    assert q.updated_at == T0


# --- lifecycle --------------------------------------------------------------------


def test_archive_and_restore(reg, clock):
    p = _register(reg)
    clock.advance()
    a = reg.archive(p.project_id)
    assert a.status is ProjectStatus.ARCHIVED and a.updated_at > p.updated_at
    clock.advance()
    r = reg.restore(p.project_id)
    assert r.status is ProjectStatus.ACTIVE and r.updated_at > a.updated_at


def test_archive_and_restore_are_idempotent(reg, clock):
    p = _register(reg)
    clock.advance()
    a = reg.archive(p.project_id)
    clock.advance()
    assert reg.archive(p.project_id) == a
    assert reg.restore(reg.restore(p.project_id).project_id).status is ProjectStatus.ACTIVE


def test_archived_project_is_read_only(reg):
    p = _register(reg)
    reg.archive(p.project_id)
    with pytest.raises(ProjectArchivedError):
        reg.update(p.project_id, description="x")
    assert reg.get(p.project_id).description == ""


def test_restore_conflict(reg):
    old = _register(reg, name="Toko", path="/w/toko")
    reg.archive(old.project_id)
    new = _register(reg, name="Toko", path="/w/toko-new")
    with pytest.raises(DuplicateProjectError) as info:
        reg.restore(old.project_id)
    assert info.value.field == "name" and info.value.existing_project_id == new.project_id
    reg.update(new.project_id, name="Toko New")
    reg.update(new.project_id, root_path="/w/toko")
    with pytest.raises(DuplicateProjectError) as info:
        reg.restore(old.project_id)
    assert info.value.field == "root_path"
    assert reg.get(old.project_id).status is ProjectStatus.ARCHIVED


def test_archive_unknown(reg):
    with pytest.raises(ProjectNotFoundError):
        reg.archive("prj_ghost")
    with pytest.raises(ProjectNotFoundError):
        reg.restore("prj_ghost")


def test_no_delete_api(reg):
    assert not any(hasattr(reg, n) for n in ("delete", "remove", "purge", "unregister"))


# --- persistence --------------------------------------------------------------------


def test_survives_reopen(tmp_path):
    path = tmp_path / "siamang.db"
    with Database(path) as db:
        p = ProjectRegistry(db).register(name="Toko", root_path="/w/toko", metadata={"language": "python"})
    with Database(path) as db:
        again = ProjectRegistry(db).get(p.project_id)
    assert again == p
    assert again.to_dict() == p.to_dict()


def test_to_dict_is_json(reg):
    d = _register(reg, metadata={"tags": ["a", "b"], "n": 1.5}).to_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["created_at"].endswith("Z") and d["status"] == "active"


def test_metadata_is_read_only(reg):
    p = _register(reg, metadata={"a": 1})
    with pytest.raises(TypeError):
        p.metadata["a"] = 2


# --- transactions ---------------------------------------------------------------------


def test_failed_registration_leaves_nothing(reg, db, monkeypatch):
    def boom(row):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(reg.store, "insert", boom)
    with pytest.raises(RuntimeError):
        _register(reg)
    monkeypatch.undo()
    assert ProjectStore(db).count() == 0
    assert db.conn.in_transaction is False
    _register(reg)  # the registry is still usable


def test_failed_update_leaves_row_unchanged(reg, monkeypatch):
    p = _register(reg)

    def boom(row):
        raise RuntimeError("write failed")

    monkeypatch.setattr(reg.store, "update", boom)
    with pytest.raises(RuntimeError):
        reg.update(p.project_id, name="Changed")
    monkeypatch.undo()
    assert reg.get(p.project_id) == p


def test_database_backstops_duplicate_rules(reg, db, monkeypatch):
    first = _register(reg, name="Toko")
    monkeypatch.setattr(reg, "_check_active_conflicts", lambda row: None)  # bypass pre-checks
    with pytest.raises(DuplicateProjectError):
        _register(reg, name="TOKO", path="/w/other")  # unique index on name_key still rejects
    with pytest.raises(DuplicateProjectError):
        _register(reg, name="Other", path=first.root_path)  # unique index on root_path too
    assert ProjectStore(db).count() == 1


def test_ulid_ids_are_time_ordered():
    assert ulid(now_ms=1) < ulid(now_ms=2)
