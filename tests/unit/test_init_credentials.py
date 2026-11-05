import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "init-credentials.py"


def run(home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


def test_creates_private_file_whose_identity_matches_the_printed_token(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0

    token = result.stdout.strip().removeprefix("export SIAMANG_TOKEN=")
    env_file = tmp_path / ".config" / "siamang" / "local.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    lines = dict(line.removeprefix("export ").split("=", 1) for line in env_file.read_text().splitlines())
    identities = json.loads(shlex.split(lines["SIAMANG_API_IDENTITIES"])[0])
    assert identities["owner"]["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in env_file.read_text()
    assert len(lines["SIAMANG_API_SEAL_KEY"]) == 64


def test_refuses_to_replace_an_existing_seal_key(tmp_path):
    assert run(tmp_path).returncode == 0
    env_file = tmp_path / ".config" / "siamang" / "local.env"
    before = env_file.read_text()

    result = run(tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert env_file.read_text() == before
