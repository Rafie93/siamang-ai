"""ProposedChange: small vocabulary, safe project-relative paths, no forbidden files, no secrets, text only."""

import dataclasses

import pytest

from siamang.programmer import BeforeState, Operation, ProposedChange
from siamang.programmer.change import MAX_FILE_LIMIT
from siamang.programmer.errors import ChangeRejectedError, ProgrammingErrorCode
from siamang.programmer.safety import find_secret
from tests.support.programmer import sha

H = sha("old\n")


def make(**over):
    data = dict(change_id="change_01", path="src/app.py", operation="modify", step_id="step_03",
                reason="Implement step_03.", after_content="new\n", before="observed", before_hash=H)
    data.update(over)
    return ProposedChange(**data)


def rejected(**over):
    with pytest.raises(ChangeRejectedError) as info:
        make(**over)
    assert info.value.code is ProgrammingErrorCode.CHANGE_REJECTED
    return info.value


def test_create_modify_delete():
    c = make(operation="create", before="absent", before_hash=None, path="src/new.py", after_content="x = 1\n")
    assert c.operation is Operation.CREATE and c.before is BeforeState.ABSENT and c.after_hash == sha("x = 1\n")
    m = make()
    assert m.operation is Operation.MODIFY and m.before_hash == H and m.after_hash == sha("new\n")
    d = make(operation="delete", after_content=None)
    assert d.operation is Operation.DELETE and d.after_hash is None and d.size == 0
    u = make(before="unverified", before_hash=None)  # current state not established: no invented hash
    assert u.before_hash is None
    assert make(operation="create", before="unverified", before_hash=None).before is BeforeState.UNVERIFIED


def test_change_is_immutable_and_after_hash_is_computed():
    c = make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.after_content = "evil"
    with pytest.raises(TypeError):
        ProposedChange(change_id="change_01", path="a.py", operation="create", step_id="step_01", reason="r",
                       after_content="x", before="absent", after_hash=sha("other"))
    with pytest.raises(TypeError):
        c.metadata["x"] = 1
    assert c.to_dict()["after_hash"] == sha("new\n")


@pytest.mark.parametrize("op", ["rename", "move", "chmod", "symlink", "binary_patch", "submodule", "git", "append",
                                "", None, "CREATE"])
def test_only_create_modify_delete(op):
    rejected(operation=op)


@pytest.mark.parametrize("path", [
    "../x.py", "../../etc/passwd", "src/../../x", "/etc/passwd", "/Users/someone/web/x.py", "~/x.py", "~", "a/~/b",
    "C:/x.py", "C:\\x.py", "c:x.py", "\\\\server\\share\\x", "//server/share/x", "src\\app.py", "%2e%2e/x",
    "src/%2e%2e/x", "a//b", "./a", "a/./b", "a/", "", ".", "..", "src/a\x00.py", "src/a\nb.py", "src/a\x1b.py",
    "src/\u202egnp.exe", "src/cafe\u0301.py", "a/" + "b" * 300, "x/" * 600 + "y", 42, None, b"src/a.py",
])
def test_unsafe_paths_rejected(path):
    error = rejected(path=path)
    assert "path rejected" in error.message
    if isinstance(path, str) and path.startswith("/Users"):
        assert path not in str(error.to_dict())


@pytest.mark.parametrize("path", [
    ".git/config", ".git/hooks/pre-commit", ".git", "sub/.git/HEAD", ".GIT/config", ".hg/hgrc", ".svn/entries",
    ".env", ".env.local", ".env.production", "config/.env", "backend/.envrc", "prod.env", "certs/server.pem",
    "keys/deploy.key", "id_rsa", "id_rsa.pub", "home/.ssh/id_ed25519", ".ssh/config", "credentials.json",
    "config/credentials.yml", "secrets.yaml", "app/secret.json", "x.p12", "service-account-prod.json", ".netrc",
    ".npmrc", ".aws/config", "secrets/README.md",
])
def test_forbidden_files_rejected(path):
    for op, kw in (("modify", {}), ("delete", {"after_content": None}),
                   ("create", {"before": "absent", "before_hash": None})):
        assert "path rejected" in rejected(path=path, operation=op, **kw).message


@pytest.mark.parametrize("content", [
    "API_KEY=abcd1234efgh5678\n", "SECRET=s3cr3tvalue99\n", "export PASSWORD=hunter2hunter2\n", "TOKEN=zx81zx81zx81\n",
    'api_key = "a1b2c3d4e5f6g7h8"\n', "client_secret: 'qwertyuiop123'\n", '{"password": "P@ssw0rd!2024"}',
    "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n",
    "-----BEGIN RSA PRIVATE KEY-----\nabc\n", "-----BEGIN OPENSSH PRIVATE KEY-----\n",
    "KEY = 'sk-SECRET-DO-NOT-LEAK-31337'\n", "aws = 'AKIAABCDEFGHIJKLMNOP'\n",
    "t = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n", "slack = 'xoxb-1234567890-abcdefghij'\n",
    "DB_PASSWORD := \"Zx9Qw8Er7Ty6\"\n", "$config = [\"secret_key\" => 'abcdEFGH1234'];",
])
def test_secret_material_rejected(content):
    error = rejected(after_content=content)
    assert "credential material" in error.message
    assert content.strip() not in str(error.to_dict())


@pytest.mark.parametrize("content", [
    "API_KEY = os.environ['API_KEY']\n", 'password = request.form["password"]\n', "TOKEN=${TOKEN}\n",
    "SECRET=changeme\n", 'api_key = "your-api-key-here"\n', "PASSWORD=\n", 'tokenizer = "bert-base-uncased"\n',
    'token_type = "bearer-token-v1"\n', "password_field = 'password_input_box'\n", "secret = None\n",
    "API_KEY=<set-me>\n", 'const key = process.env.API_KEY;\n', "def check_password(pw):\n    return len(pw) > 8\n",
    "TEST_PASSWORD = 'test-password-123'\n",
])
def test_ordinary_code_is_not_a_secret(content):
    assert find_secret(content) is None
    assert make(after_content=content).after_content == content


@pytest.mark.parametrize("content", ["a\x00b", "\x00\x01\x02\x03", "x\x07y", "PK\x03\x04binary", "\x7fELF\x02\x01",
                                     "ok\ud800bad", 12, b"bytes"])
def test_binary_or_non_text_content_rejected(content):
    assert "after_content rejected" in rejected(after_content=content).message


def test_text_controls_allowed_and_size_capped():
    assert make(after_content="a\tb\r\nc\x0cd\n").after_content
    big = "x" * (MAX_FILE_LIMIT + 1)
    assert "exceeds" in rejected(after_content=big).message
    assert make(after_content="é" * (MAX_FILE_LIMIT // 2)).size == MAX_FILE_LIMIT


@pytest.mark.parametrize("over, words", [
    ({"operation": "create", "before": "observed"}, "already exists"),
    ({"operation": "modify", "before": "absent", "before_hash": None}, "does not exist"),
    ({"operation": "delete", "before": "absent", "before_hash": None, "after_content": None}, "does not exist"),
    ({"before": "observed", "before_hash": None}, "before_hash"),
    ({"before": "observed", "before_hash": "md5:abc"}, "before_hash"),
    ({"before": "unverified", "before_hash": H}, "only set"),
    ({"operation": "create", "before": "absent", "before_hash": H}, "only set"),
    ({"operation": "delete", "after_content": "x"}, "no after_content"),
    ({"after_content": None}, "after_content rejected"),
    ({"after_content": "old\n"}, "must change"),
    ({"before": "known"}, "absent, observed, or unverified"),
])
def test_before_state_invariants(over, words):
    assert words in rejected(**over).message


@pytest.mark.parametrize("over", [{"change_id": "c1"}, {"change_id": "change_1"}, {"step_id": "3"},
                                  {"step_id": "step_x"}, {"step_id": None}, {"reason": ""}, {"reason": "a\x00"},
                                  {"reason": "x" * 501}, {"reason": "API_KEY=abcd1234efgh5678 is used"},
                                  {"evidence": "one line"}, {"evidence": ["/Users/someone/x"]},
                                  {"evidence": ["../../etc/passwd"]}, {"evidence": ["~/x"]},
                                  {"evidence": ["token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"]},
                                  {"evidence": ["x"] * 11}, {"metadata": {"command": "rm"}},
                                  {"metadata": {"path": "/etc"}}])
def test_field_validation(over):
    rejected(**over)


def test_evidence_and_reason_are_kept_as_data():
    c = make(evidence=["System analysis observed backend/routes/auth.py", "plan step_03"],
             reason="Add validation; see analysis finding_05.")
    assert c.evidence == ("System analysis observed backend/routes/auth.py", "plan step_03")
