"""ProjectInspector: confined to the registered root, no symlinks, no secret files, budgeted."""

import os

import pytest

from siamang.analyst import AnalysisPolicy, ProjectInspector, is_sensitive
from siamang.analyst.errors import AnalysisProjectUnavailableError, LimitReachedError, PathRejectedError
from siamang.analyst.inspection import EntryKind, validate_relative
from tests.support.analyst import SECRET, write


@pytest.fixture
def root(tmp_path):
    return write(tmp_path / "proj", {"README.md": "# x\n", "src/app.py": "print(1)\n", "src/deep/a/b.txt": "b\n",
                                     ".env": f"API_KEY={SECRET}\n", "bin.dat": "x"})


def inspector(root, **policy):
    return ProjectInspector(str(root), AnalysisPolicy(**policy))


def test_list_and_read(root):
    ins = inspector(root)
    entries = ins.list_dir(".")
    assert [e.name for e in entries] == sorted(e.name for e in entries)
    by = {e.name: e for e in entries}
    assert by["src"].kind is EntryKind.DIRECTORY and by["README.md"].kind is EntryKind.FILE
    assert by[".env"].sensitive and not by["README.md"].sensitive
    assert [e.path for e in ins.list_dir("src")] == ["src/app.py", "src/deep"]
    text = ins.read_text("src/app.py")
    assert text.text == "print(1)\n" and text.size == 9
    assert ins.usage() == {"files_read": 1, "bytes_read": 9, "entries_listed": len(entries) + 2}


@pytest.mark.parametrize("path", [
    "../", "..", "../../etc/passwd", "src/../../etc/passwd", "src/../README.md", "/etc/passwd", "/", "./README.md",
    "src//app.py", "src/./app.py", "~/x", "~root", "%2e%2e/etc/passwd", "src%2f..%2fREADME.md", "..%2f",
    "src\\..\\README.md", "C:\\Windows", "a\x00b", "a\nb", "", None, 42,
])
def test_traversal_and_malformed_paths_are_rejected(root, path):
    ins = inspector(root)
    with pytest.raises(PathRejectedError):
        ins.read_text(path)
    with pytest.raises(PathRejectedError):
        ins.list_dir(path if path not in ("",) else "")


def test_validate_relative():
    assert validate_relative(".") == ()
    assert validate_relative("a/b.c") == ("a", "b.c")
    assert validate_relative(".github/workflows") == (".github", "workflows")


def test_symlink_escape_is_never_followed(root, tmp_path):
    outside = write(tmp_path / "outside", {"passwd": f"root:{SECRET}\n"})
    os.symlink(outside / "passwd", root / "leak.txt")
    os.symlink(outside, root / "outdir")
    os.symlink(root / "README.md", root / "inside_link.md")  # even an internal link is not followed
    ins = inspector(root)
    kinds = {e.name: e.kind for e in ins.list_dir(".")}
    assert kinds["leak.txt"] is kinds["outdir"] is kinds["inside_link.md"] is EntryKind.SYMLINK
    for path in ("leak.txt", "outdir/passwd", "inside_link.md"):
        with pytest.raises(PathRejectedError):
            ins.read_text(path)
    with pytest.raises(PathRejectedError):
        ins.list_dir("outdir")
    assert ins.usage()["files_read"] == 0


def test_symlinked_intermediate_directory(root, tmp_path):
    write(tmp_path / "outside", {"x/secret.txt": SECRET})
    os.symlink(tmp_path / "outside" / "x", root / "src" / "linked")
    with pytest.raises(PathRejectedError):
        inspector(root).read_text("src/linked/secret.txt")


def test_registered_root_may_itself_be_a_symlink(root, tmp_path):
    link = tmp_path / "rootlink"
    os.symlink(root, link)
    assert inspector(link).read_text("README.md").text == "# x\n"


@pytest.mark.parametrize("name", [".env", ".env.production", "prod.env", ".envrc", "server.pem", "tls.key",
                                  "id_rsa", "id_ed25519.pub", "credentials.json", "credential", "secrets.yaml",
                                  "secret.txt", ".npmrc", ".netrc", ".pypirc", "keystore.jks", "app.secrets.json",
                                  "service-account-prod.json", ".git-credentials", "cert.p12"])
def test_sensitive_names(name):
    assert is_sensitive(name)


@pytest.mark.parametrize("name", ["README.md", "environment.py", "keyboard.py", "tokens.css", "monkey.py",
                                  "package.json", "pyproject.toml", "settings.py", "envelope.py"])
def test_ordinary_names(name):
    assert not is_sensitive(name)


def test_secret_files_are_never_opened(root, monkeypatch):
    ins = inspector(root)
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: opened.append(p) or real_open(p, *a, **k))
    with pytest.raises(PathRejectedError):
        ins.read_text(".env")
    write(root, {"config/credentials.json": f'{{"token": "{SECRET}"}}'})
    with pytest.raises(PathRejectedError):
        ins.read_text("config/credentials.json")
    assert opened == []


def test_oversized_file_is_noted_not_read(root):
    write(root, {"big.txt": "x" * 5000})
    ins = inspector(root, max_file_size=1000)
    big = ins.read_text("big.txt")
    assert big.too_large and big.text is None and big.size == 5000
    assert ins.usage()["bytes_read"] == 0


def test_binary_file(root):
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    out = inspector(root).read_text("bin.dat")
    assert out.binary and out.text is None


def test_file_budget(root):
    ins = inspector(root, max_files=1)
    ins.read_text("README.md")
    with pytest.raises(LimitReachedError):
        ins.read_text("src/app.py")


def test_byte_budget(root):
    ins = inspector(root, max_total_bytes=10)
    ins.read_text("src/app.py")  # 9 bytes
    with pytest.raises(LimitReachedError):
        ins.read_text("README.md")  # 4 more


def test_entry_budget(root):
    write(root, {f"many/f{i:03d}.txt": "x" for i in range(50)})
    with pytest.raises(LimitReachedError):
        inspector(root, max_directory_entries=10).list_dir("many")


def test_not_a_file_or_directory(root):
    ins = inspector(root)
    with pytest.raises(PathRejectedError):
        ins.read_text("src")
    with pytest.raises(PathRejectedError):
        ins.list_dir("README.md")
    with pytest.raises(PathRejectedError):
        ins.read_text("missing.txt")


def test_fifo_is_not_read(root):
    os.mkfifo(root / "pipe")
    ins = inspector(root)
    assert {e.name: e.kind for e in ins.list_dir(".")}["pipe"] is EntryKind.OTHER
    with pytest.raises(PathRejectedError):
        ins.read_text("pipe")  # would block forever if opened


def test_root_must_be_an_existing_directory(tmp_path):
    for bad in (str(tmp_path / "missing"), "relative/path", str(write(tmp_path, {"f.txt": "x"}) / "f.txt")):
        with pytest.raises(AnalysisProjectUnavailableError):
            ProjectInspector(bad, AnalysisPolicy())
    with pytest.raises(TypeError):
        ProjectInspector(str(tmp_path), {"max_files": 10**9})


def test_inspector_exposes_no_write_or_path_api():
    public = {n for n in dir(ProjectInspector) if not n.startswith("_")}
    assert public == {"list_dir", "read_text", "usage"}
