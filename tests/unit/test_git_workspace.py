"""GitWorkspaceManager against real, isolated temporary repositories."""

import hashlib
import os
from pathlib import Path

import pytest

from siamang.git import (
    PROTECTED_REPOSITORIES,
    FileState,
    GitWorkspaceManager,
    InvalidGitArgumentError,
    InvalidRepositoryError,
    NotRepositoryError,
    ProtectedRepositoryError,
    Relation,
    WorkspaceKind,
)
from siamang.projects import ProjectNotFoundError, ProjectRegistry
from tests.support.git import git, make_repo

NASTY = ["with space.txt", 'quo"te.txt', "it's.txt", "(paren).txt", "ünïcödé.txt", "semi;colon.txt",
         "$(touch PWNED).txt", "`touch PWNED`.txt", "new\nline.txt", "tab\there.txt"]


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


@pytest.fixture
def registry(db):
    return ProjectRegistry(db)


@pytest.fixture
def manager(registry):
    return GitWorkspaceManager(registry)


def register(registry, path, name="P"):
    return registry.register(name=name, root_path=str(path))


def snapshot(git_dir: Path) -> dict[str, str]:
    """Hash of every file under .git (the test may scan; Siamang may not)."""
    return {
        str(p.relative_to(git_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(git_dir.rglob("*")) if p.is_file()
    }


# --- detection --------------------------------------------------------------------


def test_is_repository(manager, repo, tmp_path):
    (tmp_path / "plain").mkdir()
    assert manager.is_repository(repo.root)
    assert not manager.is_repository(str(tmp_path / "plain"))
    assert not manager.is_repository(str(tmp_path / "missing"))


def test_repository_root_from_nested_path(manager, repo):
    (repo.path / "a" / "b").mkdir(parents=True)
    assert manager.repository_root(str(repo.path / "a" / "b")) == repo.root


def test_non_repository_raises(manager, tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(NotRepositoryError) as info:
        manager.repository_root(str(tmp_path / "plain"))
    assert info.value.__context__ is None


def test_bare_repository_is_not_a_work_tree(manager, tmp_path):
    git(tmp_path, "init", "-q", "--bare", "b.git")
    assert not manager.is_repository(str(tmp_path / "b.git"))
    with pytest.raises(InvalidRepositoryError):
        manager.locate_path(str(tmp_path / "b.git"))


def test_relative_path_rejected(manager):
    with pytest.raises(InvalidGitArgumentError):
        manager.is_repository("relative/dir")


# --- project -> repository ------------------------------------------------------------


def test_project_at_repository_root(manager, registry, repo):
    p = register(registry, repo.path)
    loc = manager.locate(p.project_id)
    assert loc.project_root == p.root_path == repo.root
    assert loc.repository_root == repo.root
    assert loc.relation is Relation.ROOT and loc.prefix == ""
    assert loc.git_dir == loc.common_git_dir == str(repo.path / ".git")
    assert not loc.path_aliased and not loc.is_linked_worktree


def test_nested_project_keeps_its_own_root(manager, registry, repo):
    sub = repo.path / "apps" / "web ui"
    sub.mkdir(parents=True)
    p = register(registry, sub)
    loc = manager.locate(p)
    assert loc.project_root == str(sub)  # registry value untouched
    assert loc.repository_root == repo.root
    assert loc.relation is Relation.NESTED and loc.prefix == "apps/web ui"
    assert registry.get(p.project_id).root_path == str(sub)  # registry not redefined


def test_symlinked_project_root(manager, registry, repo, tmp_path):
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo.path)
    p = register(registry, link)
    loc = manager.locate(p)
    assert loc.project_root == str(link)  # Siamang does not resolve it
    assert loc.repository_root == repo.root  # Git does
    assert loc.relation is Relation.ROOT and loc.path_aliased


def test_symlink_into_subdirectory(manager, registry, repo, tmp_path):
    (repo.path / "sub").mkdir()
    link = tmp_path / "sublink"
    link.symlink_to(repo.path / "sub")
    loc = manager.locate(register(registry, link))
    assert loc.relation is Relation.NESTED and loc.prefix == "sub" and loc.path_aliased


def test_project_in_linked_worktree(manager, registry, repo, tmp_path):
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "-b", "feature", str(wt))
    p = register(registry, wt)
    loc = manager.locate(p)
    assert loc.repository_root == str(wt)
    assert loc.is_linked_worktree
    assert loc.common_git_dir == str(repo.path / ".git")
    assert manager.status(p).head.branch == "feature"
    assert manager.head(register(registry, repo.path, name="Main")).branch == "main"


def test_project_lookup(manager, registry, repo):
    p = register(registry, repo.path)
    assert manager.project(p.project_id) == p
    assert manager.project(p) is p
    with pytest.raises(ProjectNotFoundError):
        manager.locate("prj_missing")
    with pytest.raises(InvalidGitArgumentError):
        GitWorkspaceManager().locate(p.project_id)  # an ID needs a registry
    assert GitWorkspaceManager().locate(p).repository_root == repo.root  # a Project does not


def test_project_root_not_a_repository(manager, registry, tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(NotRepositoryError):
        manager.inspect(register(registry, tmp_path / "plain").project_id)


def test_missing_project_root(manager, registry, tmp_path):
    with pytest.raises(NotRepositoryError):
        manager.status(register(registry, tmp_path / "not-created-yet"))


def test_archived_project_can_still_be_inspected(manager, registry, repo):
    p = register(registry, repo.path)
    registry.archive(p.project_id)
    assert manager.inspect(p.project_id).status.clean


# --- HEAD / branch ------------------------------------------------------------------


def test_head_on_branch(manager, registry, repo):
    head = manager.head(register(registry, repo.path))
    assert head.commit == repo.head() and head.short_commit == repo.head()[:12]
    assert head.branch == "main" and not head.detached and not head.unborn


def test_branch_with_slash(manager, registry, repo):
    repo.git("checkout", "-q", "-b", "feature/example")
    assert manager.head(register(registry, repo.path)).branch == "feature/example"


def test_detached_head(manager, registry, repo):
    first = repo.head()
    repo.write("b.txt")
    repo.commit("second")
    repo.git("checkout", "-q", "--detach", first)
    p = register(registry, repo.path)
    head = manager.head(p)
    assert head.detached and head.branch is None and head.commit == first
    st = manager.status(p)
    assert st.head.detached and st.head.branch is None and st.head.commit == first


def test_empty_repository(manager, registry, tmp_path):
    empty = make_repo(tmp_path / "empty", commit=False)
    p = register(registry, empty.path)
    head = manager.head(p)
    assert head.commit is None and head.unborn and head.branch == "main" and not head.detached
    st = manager.status(p)
    assert st.clean and st.head.unborn
    empty.write("first.txt")
    empty.git("add", "first.txt")
    assert [(e.path, e.state) for e in manager.status(p).entries] == [("first.txt", FileState.ADDED)]
    cached = manager.diff(p, cached=True)
    assert [f.path for f in cached.files] == ["first.txt"]
    assert manager.worktrees(p)[0].commit is None


# --- working tree status ----------------------------------------------------------------


def test_clean(manager, registry, repo):
    st = manager.status(register(registry, repo.path))
    assert st.clean and st.entries == () and st.upstream is None and st.ahead is None


def test_all_change_kinds(manager, registry, repo):
    repo.write("mod.txt", "1\n")
    repo.write("del.txt", "1\n")
    repo.write("old.txt", "rename me\n" * 5)
    repo.write("staged.txt", "1\n")
    repo.commit("base")
    repo.write("mod.txt", "2\n")
    (repo.path / "del.txt").unlink()
    repo.git("mv", "old.txt", "new.txt")
    repo.write("staged.txt", "2\n")
    repo.git("add", "staged.txt")
    repo.write("added.txt")
    repo.git("add", "added.txt")
    repo.write("untracked.txt")
    st = manager.status(register(registry, repo.path))
    by = {e.path: e for e in st.entries}
    assert by["mod.txt"].state is FileState.MODIFIED and not by["mod.txt"].staged
    assert by["del.txt"].state is FileState.DELETED
    assert by["new.txt"].state is FileState.RENAMED and by["new.txt"].original_path == "old.txt"
    assert by["staged.txt"].state is FileState.MODIFIED and by["staged.txt"].staged
    assert by["added.txt"].state is FileState.ADDED
    assert by["untracked.txt"].state is FileState.UNTRACKED
    assert not st.clean
    assert st.counts()["conflicted"] == 0


def test_conflict(manager, registry, repo):
    repo.write("c.txt", "base\n")
    repo.commit("base")
    repo.git("checkout", "-q", "-b", "other")
    repo.write("c.txt", "other\n")
    repo.commit("other")
    repo.git("checkout", "-q", "main")
    repo.write("c.txt", "main\n")
    repo.commit("main")
    repo.git("merge", "other", check=False)
    st = manager.status(register(registry, repo.path))
    (entry,) = [e for e in st.entries if e.path == "c.txt"]
    assert entry.state is FileState.CONFLICTED and entry.index_status == "U"


def test_status_paths_are_relative_to_repository_root(manager, registry, repo):
    (repo.path / "sub").mkdir()
    repo.write("sub/inner.txt")
    repo.write("outer.txt")
    st = manager.status(register(registry, repo.path / "sub"))
    assert {e.path for e in st.entries} == {"sub/", "outer.txt"}


def test_ahead_behind_against_local_upstream(manager, registry, repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.git("branch", "--set-upstream-to=main")
    repo.write("f.txt")
    repo.commit("ahead 1")
    repo.git("checkout", "-q", "main")
    repo.write("m.txt")
    repo.commit("behind 1")
    repo.write("m2.txt")
    repo.commit("behind 2")
    repo.git("checkout", "-q", "feature")
    st = manager.status(register(registry, repo.path))
    assert (st.upstream, st.ahead, st.behind) == ("main", 1, 2)


def test_unusual_paths_are_data(manager, registry, repo):
    for name in NASTY:
        repo.write(f"tracked/{name}", "1\n")
    repo.commit("nasty")
    for name in NASTY:
        repo.write(f"tracked/{name}", "2\n")
        repo.write(f"untracked/{name}")
    repo.git("mv", f"tracked/{NASTY[6]}", f"tracked/renamed {NASTY[7]}")
    p = register(registry, repo.path)
    st = manager.status(p)
    paths = {e.path for e in st.entries}
    assert {f"tracked/{n}" for n in NASTY if n != NASTY[6]} <= paths
    assert "untracked/" in paths
    renamed = [e for e in st.entries if e.state is FileState.RENAMED]
    assert [(e.original_path, e.path) for e in renamed] == [(f"tracked/{NASTY[6]}", f"tracked/renamed {NASTY[7]}")]
    numstat = {f.path for f in manager.diff(p).files}
    assert {f"tracked/{n}" for n in NASTY if n != NASTY[6]} <= numstat
    assert not (repo.path / "PWNED").exists() and not Path("PWNED").exists()


def test_untracked_files_individually_listed_inside_tracked_dir(manager, registry, repo):
    for name in NASTY:
        repo.write(name)
    st = manager.status(register(registry, repo.path))
    assert sorted(e.path for e in st.entries) == sorted(NASTY)
    assert all(e.state is FileState.UNTRACKED for e in st.entries)


# --- diff ---------------------------------------------------------------------------------


def test_diff_and_cached(manager, registry, repo):
    repo.write("a.txt", "one\n")
    repo.write("b.txt", "one\n")
    repo.commit("base")
    repo.write("a.txt", "one\ntwo\n")
    repo.write("b.txt", "changed\n")
    repo.git("add", "b.txt")
    (repo.path / "bin.dat").write_bytes(b"\x00\x01\x02")
    repo.git("add", "bin.dat")
    p = register(registry, repo.path)
    unstaged = manager.diff(p)
    assert not unstaged.cached
    assert [(f.path, f.added, f.deleted) for f in unstaged.files] == [("a.txt", 1, 0)]
    assert "+two" in unstaged.patch and "b.txt" not in unstaged.patch
    staged = manager.diff(p, cached=True)
    assert staged.cached
    files = {f.path: f for f in staged.files}
    assert (files["b.txt"].added, files["b.txt"].deleted) == (1, 1)
    assert files["bin.dat"].binary
    assert "+changed" in staged.patch and "two" not in staged.patch


def test_diff_clean(manager, registry, repo):
    d = manager.diff(register(registry, repo.path))
    assert d.files == () and d.patch == "" and not d.truncated


def test_diff_staged_rename(manager, registry, repo):
    repo.write("old.txt", "content\n" * 10)
    repo.commit("base")
    repo.git("mv", "old.txt", "new name.txt")
    (f,) = manager.diff(register(registry, repo.path), cached=True).files
    assert (f.original_path, f.path) == ("old.txt", "new name.txt")


def test_diff_ignores_external_diff_and_textconv(manager, registry, repo, tmp_path):
    marker = tmp_path / "EXECUTED"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o755)
    repo.git("config", "diff.external", str(script))
    repo.git("config", "diff.evil.textconv", str(script))
    repo.write(".gitattributes", "*.txt diff=evil\n")
    repo.write("x.txt", "1\n")
    repo.commit("base")
    repo.write("x.txt", "2\n")
    p = register(registry, repo.path)
    manager.diff(p)
    manager.diff(p, cached=True)
    manager.status(p)
    assert not marker.exists()


def test_repository_hooks_and_fsmonitor_never_run(manager, registry, repo, tmp_path):
    marker = tmp_path / "EXECUTED"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o755)
    repo.git("config", "core.fsmonitor", str(script))
    hooks = repo.path / ".git" / "hooks"
    for hook in ("post-checkout", "pre-commit", "reference-transaction", "post-index-change"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        (hooks / hook).chmod(0o755)
    p = register(registry, repo.path)
    manager.inspect(p)
    manager.worktrees(p)
    manager.diff(p)
    manager.head(p)
    assert not marker.exists()


# --- worktrees ----------------------------------------------------------------------------


def test_worktree_discovery(manager, registry, repo, tmp_path):
    feature = tmp_path / "wt feature"
    detached = tmp_path / "wt-detached"
    repo.git("worktree", "add", "-q", "-b", "feature/x", str(feature))
    repo.git("worktree", "add", "-q", "--detach", str(detached))
    repo.git("worktree", "lock", str(detached))
    p = register(registry, repo.path)
    main, feat, det = manager.worktrees(p)
    assert (main.kind, main.path, main.branch, main.current) == (WorkspaceKind.MAIN, repo.root, "main", True)
    assert (feat.kind, feat.path, feat.branch, feat.current) == (WorkspaceKind.LINKED, str(feature), "feature/x", False)
    assert det.detached and det.branch is None and det.locked and det.commit == repo.head()
    assert {w.repository_root for w in (main, feat, det)} == {repo.root}
    # From inside a linked worktree, the same set is seen, with `current` moved.
    q = register(registry, feature, name="Feature")
    seen = manager.worktrees(q)
    assert [w.workspace_id for w in seen] == [main.workspace_id, feat.workspace_id, det.workspace_id]
    assert [w.current for w in seen] == [False, True, False]


def test_prunable_worktree(manager, registry, repo, tmp_path):
    gone = tmp_path / "gone"
    repo.git("worktree", "add", "-q", "--detach", str(gone))
    import shutil

    shutil.rmtree(gone)
    _, stale = manager.worktrees(register(registry, repo.path))
    assert stale.prunable
    assert (repo.path / ".git" / "worktrees" / "gone").exists()  # discovery never prunes


def test_single_worktree(manager, registry, repo):
    (only,) = manager.worktrees(register(registry, repo.path))
    assert only.kind is WorkspaceKind.MAIN and only.current


# --- remotes ------------------------------------------------------------------------------


def test_remotes_are_local_metadata_with_credentials_removed(manager, registry, repo):
    repo.git("remote", "add", "origin", "https://bob:ghp_SECRET123@github.com/o/r.git")
    repo.git("remote", "add", "upstream", "git@example.invalid:o/r.git")
    info = manager.inspect(register(registry, repo.path))
    assert [(r.name, r.fetch_url) for r in info.remotes] == [
        ("origin", "https://github.com/o/r.git"), ("upstream", "git@example.invalid:o/r.git")]
    assert "ghp_SECRET123" not in repr(info.to_dict())


# --- protected repositories (ARCHITECTURE §6) --------------------------------------------


def test_default_protected_markers():
    assert PROTECTED_REPOSITORIES == {
        "siamang": "0b35527a17fadde1dac59d7bb152a8948f752218",
    }


def test_protected_repository_is_refused(registry, repo, tmp_path):
    manager = GitWorkspaceManager(registry, protected={"other": repo.head()})
    (repo.path / "inner").mkdir()
    for path, name in [(repo.path, "Root"), (repo.path / "inner", "Inner")]:
        p = register(registry, path, name=name)
        with pytest.raises(ProtectedRepositoryError) as info:
            manager.inspect(p)
        assert "other" in info.value.message
        for op in ("status", "diff", "worktrees", "head", "remotes", "locate"):
            with pytest.raises(ProtectedRepositoryError):
                getattr(manager, op)(p)


def test_clone_and_worktree_of_protected_repository_are_refused(registry, repo, tmp_path):
    manager = GitWorkspaceManager(registry, protected={"siamang": repo.head()})
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "--detach", str(wt))
    git(tmp_path, "clone", "-q", "--no-local", str(repo.path), "clone")
    for path, name in [(wt, "WT"), (tmp_path / "clone", "Clone")]:
        with pytest.raises(ProtectedRepositoryError):
            manager.status(register(registry, path, name=name))


def test_separate_repository_inside_protected_directory_is_allowed(registry, repo):
    # A project with its own .git, inside the directory of a protected repository.
    manager = GitWorkspaceManager(registry, protected={"other": repo.head()})
    inner = make_repo(repo.path / "toko", commit=False)
    inner.write("toko.txt", "a different history\n")
    inner.commit("toko initial")
    loc = manager.locate(register(registry, inner.path))
    assert loc.repository_root == inner.root


def test_path_level_detection_is_not_guarded(repo):
    manager = GitWorkspaceManager(protected={"other": repo.head()})
    assert manager.is_repository(repo.root)


# --- read-only ----------------------------------------------------------------------------


def test_operations_never_modify_the_repository(manager, registry, repo, tmp_path):
    repo.write("m.txt", "1\n")
    repo.commit("base")
    repo.write("m.txt", "2\n")
    repo.write("u.txt")
    repo.git("worktree", "add", "-q", "--detach", str(tmp_path / "wt"))
    before = snapshot(repo.path / ".git")
    files_before = sorted(os.listdir(repo.path))
    p = register(registry, repo.path)
    for _ in range(2):
        manager.inspect(p)
        manager.head(p)
        manager.status(p)
        manager.diff(p)
        manager.diff(p, cached=True)
        manager.worktrees(p)
        manager.remotes(p)
    assert snapshot(repo.path / ".git") == before
    assert sorted(os.listdir(repo.path)) == files_before
    assert (repo.path / "m.txt").read_text() == "2\n"


def test_caller_git_environment_is_not_inherited(manager, registry, repo, tmp_path, monkeypatch):
    other = make_repo(tmp_path / "other")
    monkeypatch.setenv("GIT_DIR", str(other.path / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", other.root)
    monkeypatch.setenv("GIT_INDEX_FILE", "/nonexistent/index")
    repo.write("only-in-repo.txt")
    st = manager.status(register(registry, repo.path))
    assert [e.path for e in st.entries] == ["only-in-repo.txt"]
    assert manager.repository_root(repo.root) == repo.root


def test_inspection_to_dict_is_json_ready(manager, registry, repo):
    import json

    d = manager.inspect(register(registry, repo.path)).to_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["head"]["branch"] == "main" and d["status"]["clean"] is True
    assert d["location"]["relation"] == "root"
