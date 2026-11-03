"""Pure parsers for Git's -z / porcelain output: exact paths, no naive splitting."""

import pytest

from siamang.git import FileState, Relation, UnexpectedGitError, WorkspaceKind
from siamang.git.repository import (
    HeadInfo,
    parse_location,
    parse_numstat,
    parse_remotes,
    parse_status,
    parse_worktrees,
    workspace_id,
)

SHA = "d6f7150f855c87299ce7f9f16b5ad35eb2050ac1"
SHA2 = "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"
NASTY = ["with space.txt", 'quo"te.txt', "(paren).txt", "ünïcödé.txt", "semi;colon.txt", "$(touch PWNED).txt",
         "`touch PWNED`.txt", "tab\there.txt", "new\nline.txt", "a b c d e f g h i j.txt"]


def _ordinary(xy, path, sub="N..."):
    return f"1 {xy} {sub} 100644 100644 100644 {SHA} {SHA2} {path}"


def test_status_headers_and_entries():
    out = "\0".join([
        f"# branch.oid {SHA}", "# branch.head feature/x", "# branch.upstream main", "# branch.ab +2 -1",
        _ordinary(".M", "mod.txt"), _ordinary("A.", "new.txt"), _ordinary("AM", "new2.txt"),
        _ordinary(".D", "gone.txt"), _ordinary("D.", "staged-gone.txt"), _ordinary("M.", "staged.txt"),
        f"2 R. N... 100644 100644 100644 {SHA} {SHA} R100 new name.txt", "old name.txt",
        f"u UU N... 100644 100644 100644 100644 {SHA} {SHA} {SHA} conflict.txt",
        "? untracked dir/", "",
    ])
    st = parse_status(out)
    assert st.head == HeadInfo(SHA, "feature/x", False)
    assert (st.upstream, st.ahead, st.behind) == ("main", 2, 1)
    by = {e.path: e for e in st.entries}
    assert by["mod.txt"].state is FileState.MODIFIED and not by["mod.txt"].staged
    assert by["new.txt"].state is FileState.ADDED and by["new.txt"].staged
    assert by["new2.txt"].state is FileState.ADDED
    assert by["gone.txt"].state is FileState.DELETED
    assert by["staged-gone.txt"].state is FileState.DELETED and by["staged-gone.txt"].staged
    assert by["staged.txt"].state is FileState.MODIFIED and by["staged.txt"].index_status == "M"
    assert by["new name.txt"].state is FileState.RENAMED and by["new name.txt"].original_path == "old name.txt"
    assert by["conflict.txt"].state is FileState.CONFLICTED
    assert by["untracked dir/"].state is FileState.UNTRACKED
    assert not st.clean
    assert st.counts() == {"modified": 2, "added": 2, "deleted": 2, "renamed": 1, "untracked": 1, "conflicted": 1}


def test_status_paths_are_taken_verbatim():
    out = "\0".join([f"# branch.oid {SHA}", "# branch.head main"]
                    + [_ordinary(".M", p) for p in NASTY] + [f"? {p}" for p in NASTY]) + "\0"
    st = parse_status(out)
    assert [e.path for e in st.entries] == NASTY + NASTY


def test_status_rename_with_nasty_names():
    out = "\0".join([f"2 R. N... 100644 100644 100644 {SHA} {SHA} R90 {NASTY[5]}", NASTY[6], ""])
    (entry,) = parse_status(out).entries
    assert (entry.path, entry.original_path) == (NASTY[5], NASTY[6])


def test_status_empty_repository_and_detached():
    st = parse_status("# branch.oid (initial)\0# branch.head main\0")
    assert st.head.commit is None and st.head.unborn and st.head.branch == "main" and st.clean
    st = parse_status(f"# branch.oid {SHA}\0# branch.head (detached)\0")
    assert st.head.detached and st.head.branch is None and st.head.short_commit == SHA[:12]


def test_status_submodule_flag():
    (entry,) = parse_status(_ordinary(".M", "lib", sub="SC..") + "\0").entries
    assert entry.submodule


@pytest.mark.parametrize("out", ["X weird\0", "1 .M N...\0", "2 R. N... 1 2 3 a b R100 path\0", "# branch.ab x y\0",
                                 "u UU N... 1 2\0"])
def test_status_rejects_unrecognized_output(out):
    with pytest.raises(UnexpectedGitError):
        parse_status(out)


def test_numstat():
    out = "3\t1\tsrc/a b.py\0-\t-\timg.png\0" + "0\t0\t\0old;name\0$(new)\0" + "1\t0\tnew\nline\0"
    files = parse_numstat(out)
    assert [(f.path, f.original_path, f.added, f.deleted, f.binary) for f in files] == [
        ("src/a b.py", None, 3, 1, False),
        ("img.png", None, None, None, True),
        ("$(new)", "old;name", 0, 0, False),
        ("new\nline", None, 1, 0, False),
    ]
    assert parse_numstat("") == ()


@pytest.mark.parametrize("out", ["garbage\0", "x\ty\tz\0", "1\t1\t\0only-one\0"])
def test_numstat_rejects_unrecognized_output(out):
    with pytest.raises(UnexpectedGitError):
        parse_numstat(out)


def test_worktrees():
    out = "\0".join([
        "worktree /r", f"HEAD {SHA}", "branch refs/heads/main", "",
        "worktree /w/feat x", f"HEAD {SHA2}", "branch refs/heads/feature/x", "locked", "",
        "worktree /w/det", f"HEAD {SHA}", "detached", "prunable gitdir file points to non-existent location", "",
        "",
    ])
    main, feat, det = parse_worktrees(out, "/r/.git", current_root="/w/feat x")
    assert (main.kind, main.branch, main.current, main.repository_root) == (WorkspaceKind.MAIN, "main", False, "/r")
    assert (feat.kind, feat.branch, feat.locked, feat.current, feat.path) == (
        WorkspaceKind.LINKED, "feature/x", True, True, "/w/feat x")
    assert det.detached and det.branch is None and det.prunable
    assert len({main.workspace_id, feat.workspace_id, det.workspace_id}) == 3
    assert main.workspace_id == workspace_id("/r/.git", "/r") and main.workspace_id.startswith("wsp_")
    assert len(main.workspace_id) == 4 + 24


def test_worktrees_bare_main():
    main, linked = parse_worktrees("worktree /b.git\0bare\0\0worktree /w\0HEAD " + SHA + "\0detached\0\0", "/b.git")
    assert main.kind is WorkspaceKind.BARE and linked.kind is WorkspaceKind.LINKED


def test_worktree_unborn_head_is_none():
    (only,) = parse_worktrees("worktree /r\0HEAD " + "0" * 40 + "\0branch refs/heads/main\0\0", "/r/.git")
    assert only.commit is None and only.short_commit is None


@pytest.mark.parametrize("out", ["", "HEAD abc\0\0"])
def test_worktrees_rejects_unrecognized_output(out):
    with pytest.raises(UnexpectedGitError):
        parse_worktrees(out, "/r/.git")


def test_workspace_id_is_deterministic():
    assert workspace_id("/r/.git", "/w") == workspace_id("/r/.git", "/w") != workspace_id("/s/.git", "/w")


def test_remotes_scrub_credentials():
    out = ("origin\thttps://bob:ghp_SECRET@github.com/o/r.git (fetch)\n"
           "origin\thttps://bob:ghp_SECRET@github.com/o/r.git (push)\n"
           "mirror\tgit@host:o/r.git (fetch)\nmirror\t/local/path with space (push)\n")
    origin, mirror = sorted(parse_remotes(out), key=lambda r: r.name, reverse=True)
    assert origin.fetch_url == origin.push_url == "https://github.com/o/r.git"
    assert "ghp_SECRET" not in repr(parse_remotes(out))
    assert (mirror.fetch_url, mirror.push_url) == ("git@host:o/r.git", "/local/path with space")


@pytest.mark.parametrize("out", ["no-tab-here (fetch)\n", "o\turl (sideways)\n"])
def test_remotes_reject_unrecognized_output(out):
    with pytest.raises(UnexpectedGitError):
        parse_remotes(out)


def test_location():
    loc = parse_location("/w/p/sub", "/w/p\nsub/\n/w/p/.git\n/w/p/.git\n")
    assert loc.relation is Relation.NESTED and loc.prefix == "sub" and not loc.path_aliased
    assert not loc.is_linked_worktree
    loc = parse_location("/link", "/real\n\n/real/.git\n/real/.git\n")
    assert loc.relation is Relation.ROOT and loc.path_aliased
    loc = parse_location("/w/wt", "/w/wt\n\n/r/.git/worktrees/wt\n/r/.git\n")
    assert loc.is_linked_worktree and not loc.path_aliased


@pytest.mark.parametrize("out", ["", "/a\n", "relative\n\n/g\n/g\n"])
def test_location_rejects_unrecognized_output(out):
    with pytest.raises(UnexpectedGitError):
        parse_location("/a", out)
