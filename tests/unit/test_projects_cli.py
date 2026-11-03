import json

import pytest

from siamang.cli.main import main


@pytest.fixture
def cfg(config_file, tmp_path):
    return config_file(f'[storage]\npath = "{tmp_path / "data" / "s.db"}"\n[logging]\nlevel = "WARNING"\n')


def run(capsys, cfg, *argv):
    code = main(["--config", str(cfg), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def _register(capsys, cfg, path, name="Toko", *extra):
    code, out, err = run(capsys, cfg, "projects", "register", "--name", name, "--path", str(path), "--json", *extra)
    assert code == 0, err
    return json.loads(out)


def test_list_without_database_does_not_create_it(capsys, cfg, tmp_path):
    code, out, _ = run(capsys, cfg, "projects")
    assert code == 0 and "No active projects." in out
    assert not (tmp_path / "data" / "s.db").exists()
    code, out, _ = run(capsys, cfg, "projects", "--json")
    assert json.loads(out) == []


def test_register_get_list(capsys, cfg, tmp_path):
    root = tmp_path / "toko"
    root.mkdir()
    p = _register(capsys, cfg, root, "Toko", "--type", "web", "--meta", "language=python", "--description", "SI")
    assert p["project_id"].startswith("prj_") and p["metadata"] == {"language": "python"}
    assert p["root_path"] == str(root)
    code, out, _ = run(capsys, cfg, "projects", "get", p["project_id"], "--json")
    assert code == 0 and json.loads(out) == p
    code, out, _ = run(capsys, cfg, "projects", "get", p["project_id"])
    assert "project_type  web" in out
    code, out, _ = run(capsys, cfg, "projects")
    assert p["project_id"] in out and "Toko" in out


def test_register_relative_path_and_missing_dir(capsys, cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = run(capsys, cfg, "projects", "register", "--name", "Nope", "--path", "missing")
    assert code == 1 and "does not exist" in err
    p = _register(capsys, cfg, "planned", "Planned", "--allow-missing")
    assert p["root_path"] == str(tmp_path / "planned")


def test_metadata_json_and_invalid(capsys, cfg, tmp_path):
    p = _register(capsys, cfg, tmp_path, "A", "--metadata-json", '{"tags": ["x"], "n": 2}')
    assert p["metadata"] == {"tags": ["x"], "n": 2}
    for bad in (["--metadata-json", "{nope"], ["--metadata-json", "[1]"], ["--meta", "novalue"], ["--meta", "api_key=sk-1"]):
        code, _, err = run(capsys, cfg, "projects", "register", "--name", "B", "--path", str(tmp_path / "b"),
                           "--allow-missing", *bad)
        assert code == 1 and err.startswith("error: metadata")


def test_duplicate_exit_code(capsys, cfg, tmp_path):
    _register(capsys, cfg, tmp_path, "Toko")
    code, _, err = run(capsys, cfg, "projects", "register", "--name", "TOKO", "--path", str(tmp_path / "x"), "--allow-missing")
    assert code == 1 and "name is already used" in err


def test_update_archive_restore(capsys, cfg, tmp_path):
    p = _register(capsys, cfg, tmp_path, "Toko")
    pid = p["project_id"]
    code, out, _ = run(capsys, cfg, "projects", "update", pid, "--name", "Toko 2", "--meta", "a=1", "--json")
    q = json.loads(out)
    assert code == 0 and (q["name"], q["metadata"], q["created_at"]) == ("Toko 2", {"a": "1"}, p["created_at"])
    code, out, _ = run(capsys, cfg, "projects", "archive", pid, "--json")
    assert json.loads(out)["status"] == "archived"
    code, out, _ = run(capsys, cfg, "projects")
    assert "No active projects." in out
    code, out, _ = run(capsys, cfg, "projects", "list", "--all", "--json")
    assert [x["status"] for x in json.loads(out)] == ["archived"]
    code, _, err = run(capsys, cfg, "projects", "update", pid, "--name", "x")
    assert code == 1 and "archived" in err
    code, out, _ = run(capsys, cfg, "projects", "restore", pid, "--json")
    assert json.loads(out)["status"] == "active"


def test_unknown_project(capsys, cfg, tmp_path):
    for cmd in (["get"], ["archive"], ["restore"], ["update"]):
        code, _, err = run(capsys, cfg, "projects", *cmd, "prj_ghost")
        assert code == 1 and "unknown project" in err
    _register(capsys, cfg, tmp_path)  # now the DB exists
    code, _, err = run(capsys, cfg, "projects", "get", "prj_ghost")
    assert code == 1 and "unknown project" in err


def test_filters_and_usage_errors(capsys, cfg, tmp_path):
    _register(capsys, cfg, tmp_path / "a", "A", "--type", "web", "--allow-missing")
    _register(capsys, cfg, tmp_path / "b", "B", "--type", "game", "--allow-missing")
    code, out, _ = run(capsys, cfg, "projects", "--type", "game", "--json")
    assert [x["name"] for x in json.loads(out)] == ["B"]
    with pytest.raises(SystemExit) as info:
        main(["--config", str(cfg), "projects", "register", "--name", "x"])  # --path required
    assert info.value.code == 2
    with pytest.raises(SystemExit):
        main(["--config", str(cfg), "projects", "--type", "spaceship"])


def test_health_and_validate_still_pass(capsys, cfg, tmp_path):
    _register(capsys, cfg, tmp_path)
    assert run(capsys, cfg, "health")[0] == 0
    code, out, _ = run(capsys, cfg, "validate")
    assert code == 0 and "Database schema (configured)" in out
