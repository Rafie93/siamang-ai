"""The managed clone: applying an approved patch, and reading a bounded view of the repository."""
from __future__ import annotations

import re
from pathlib import Path

from siamang.log import redact
from siamang.programmer.change import BeforeState, Operation
from siamang.programmer.safety import content_hash, forbidden_reason
from siamang.repos.client import RepoError

CONTEXT_FILES = ('README.md', 'README', 'package.json', 'composer.json', 'pyproject.toml', 'requirements.txt',
                 'index.html', 'index.php', 'go.mod', 'Gemfile')
CONTEXT_FILE_BYTES = 12_000
NOT_SOURCE = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.pdf', '.zip', '.gz', '.tar', '.mp3', '.mp4',
              '.woff', '.woff2', '.ttf', '.eot', '.otf', '.lock', '.min.js', '.min.css', '.map')
GENERATED = ('package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'composer.lock', 'poetry.lock')
SYMLINK_MODE = '120000'


def _target(root: Path, path: str) -> Path:
    """Refuse anything that could leave the clone or touch Git's own files."""
    parts = Path(path).parts
    if forbidden_reason(path) or Path(path).is_absolute() or '..' in parts or not parts:
        raise RepoError('patch_path_refused')
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise RepoError('patch_path_refused')
    return current


def _check_before(target: Path, change):
    if change.before is BeforeState.ABSENT:
        if target.exists():
            raise RepoError('patch_stale')
    elif change.before is BeforeState.OBSERVED:
        if not target.is_file() or content_hash(target.read_bytes()) != change.before_hash:
            raise RepoError('patch_stale')
    elif target.exists() and change.operation is not Operation.CREATE:
        raise RepoError('patch_stale')  # never overwrite a file the model did not see


def write_changes(root: Path, patch) -> tuple[str, ...]:
    """All-or-nothing against staleness: every file is checked before the first write."""
    root = Path(root)
    targets = []
    for change in patch.changes:
        target = _target(root, change.path)
        _check_before(target, change)
        targets.append((target, change))
    for target, change in targets:
        if change.operation is Operation.DELETE:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.after_content or '', encoding='utf-8', newline='')
    return tuple(c.path for c in patch.changes)


def read_context(client, root) -> dict:
    """What the design agents may see: tracked names plus a few well-known files. Data for a model, redacted."""
    root = Path(root)
    names = client.files(root)
    samples = {}
    for name in CONTEXT_FILES:
        path = root / name
        if name in names and path.is_file() and not path.is_symlink():
            samples[name] = redact(path.read_bytes()[:CONTEXT_FILE_BYTES].decode('utf-8', 'replace'))
    return {'tracked_files': names, 'file_samples': samples}


def read_latest(client, root, default_branch, branch=None, *, local=False) -> dict:
    """What the Lead sees before a task runs: the newest state, never whichever branch the clone is on.

    On GitHub that is the remote's state, and a failed fetch still answers from the last known one, and says so.
    A local project has nothing to fetch: its own branches are the newest state.
    """
    root = Path(root)
    fetched = True if local else client.refresh(root)
    revision = client.local_revision if local else client.revision
    commit = (revision(root, branch) if branch else None) or revision(root, default_branch)
    if commit is None:
        return read_context(client, root) | {'fetched_latest': fetched, 'revision': None}
    files = client.tree(root, commit)
    samples = {name: redact(client.blob(root, commit, name).encode('utf-8', 'replace')[:CONTEXT_FILE_BYTES].decode('utf-8', 'replace'))
               for name in CONTEXT_FILES if name in files and files[name] != SYMLINK_MODE}
    return {'tracked_files': list(files), 'file_samples': samples, 'fetched_latest': fetched, 'revision': commit[:12]}


def source_files(names) -> list[str]:
    """Tracked files worth showing a model: no binaries, lock files, or minified output."""
    return [n for n in names if not n.lower().endswith(NOT_SOURCE) and n.rsplit('/', 1)[-1] not in GENERATED]


def named_in(names, texts) -> list[str]:
    """Files that the design documents mention by path, then the well-known ones."""
    text = '\n'.join(texts)
    return [n for n in names if n in text] + [n for n in CONTEXT_FILES if n in names and n not in text]


def branch_name(task_id: str, text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40].strip('-') or 'task'
    return f'siamang/{slug}-{task_id[-6:].lower()}'
