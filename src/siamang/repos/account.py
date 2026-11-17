"""The GitHub token the owner connects from Settings, kept in Siamang's own file.

It reaches `gh` and `git` only as ``GH_TOKEN`` in their child environment. The owner's own `gh` login
(its config folder and the system keychain) is never read, written or logged out by Siamang.
"""
import os
import re
from pathlib import Path

# Classic (ghp_), OAuth (gho_) and fine-grained (github_pat_) tokens. Nothing else is stored.
TOKEN_RE = re.compile(r'(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,250}')


def token_path():
    override = os.environ.get('SIAMANG_GITHUB_TOKEN_FILE')
    return Path(override) if override else Path.home() / '.config' / 'siamang' / 'github-token'


def read_token(path=None):
    """The stored token, or None when there is none or the file is not one Siamang wrote."""
    path = Path(path) if path else token_path()
    try:
        value = path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return None
    return value if TOKEN_RE.fullmatch(value) else None


def write_token(token, path=None):
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise ValueError('invalid token')
    path = Path(path) if path else token_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp')
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (token + '\n').encode('utf-8'))
    finally:
        os.close(fd)
    os.replace(temp, path)


def forget_token(path=None):
    (Path(path) if path else token_path()).unlink(missing_ok=True)
