"""A failed build goes back to the Programmer as evidence: bounded, and never by weakening that evidence.

A repair is a task of its own on the same branch. It runs the normal Programmer, QA, and Reviewer loop, and its
approval goes through the unchanged ApprovalService under ``CI_REPAIR_ACTOR``, so the evidence is re-derived and
the decision names who made it. The owner still decides the merge.

``silencing_reason`` is a heuristic for the common ways of turning a build green without fixing anything. It cannot
prove that a fix is honest; the build that follows is the evidence, and the owner reviews the pull request.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

CI_REPAIR_ACTOR = 'policy.ci-repair'
DEFAULT_ATTEMPTS = 2
METADATA_KEY = 'ci_repair'
LOG_CHARS = 3_000  # the Loop refuses input above 8 KiB
JOBS_LIMIT = 8

_VOLATILE = re.compile(r'\d{4}-\d\d-\d\dT[\d:.]+Z?|\b[0-9a-f]{7,64}\b|\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds?|minutes?)\b')
_CI_CONFIG = re.compile(r'(^|/)(\.github/workflows/|\.circleci/|\.gitlab-ci\.ya?ml$|\.travis\.ya?ml$|azure-pipelines[^/]*$|jenkinsfile$)', re.I)
_TEST_FILE = re.compile(r'(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$|(^|/)conftest\.py$', re.I)
_SKIP = re.compile(r'pytest\.skip|pytest\.mark\.(skip|xfail)|unittest\.skip|@Disabled\b|@Ignore\b|#\[ignore\]|\bt\.Skip\w*\(|'
                   r'\b(?:x|f)(?:it|describe|test)\(|\b(?:it|test|describe)\.(?:skip|only|todo)\b|markTestSkipped')
_ASSERTION = re.compile(r'\bassert|\bexpect\(|\bshould\b|\.toBe|\.toEqual|\bt\.(?:Error|Fatal)')


def fingerprint(log: str) -> str:
    """Equal for the same failure on two commits: timestamps, hashes, and durations do not count."""
    return hashlib.sha256(_VOLATILE.sub('', log or '').encode('utf-8', 'replace')).hexdigest()[:24]


def failed_jobs(runs) -> list[str]:
    jobs = [f"{r['name']} / {j['name']}" for r in runs for j in r.get('jobs', ())
            if j.get('conclusion') not in ('success', 'skipped', 'neutral', None)]
    return (jobs or [r['name'] for r in runs if r.get('conclusion') not in ('success', 'skipped', 'neutral')])[:JOBS_LIMIT]


def description(title: str, attempt: int, limit: int, jobs) -> str:
    named = ', '.join(jobs) or 'build'
    return (f'Perbaiki CI yang gagal ({attempt}/{limit}): {title}'[:200] + f'\n\nYang gagal: {named}.\n'
            'Cari penyebabnya dari log di ci_failure, lalu perbaiki kodenya. Jangan melewati, menghapus, atau '
            'melemahkan test, dan jangan mengubah konfigurasi CI.')


def failure_input(jobs, log: str) -> dict:
    """The end of a log holds the error. Project output, so it reaches the model as data."""
    return {'ci_failure': {'failed_jobs': list(jobs), 'failed_output': (log or '')[-LOG_CHARS:]}}


def info(task) -> dict | None:
    """The attempt this task is, or ``None`` for ordinary work."""
    value = (task.metadata or {}).get(METADATA_KEY)
    return dict(value) if value else None


def _current(root, path: str) -> str:
    target = Path(root) / path
    return target.read_text(encoding='utf-8', errors='replace') if target.is_file() and not target.is_symlink() else ''


def silencing_reason(patch, root) -> str | None:
    """Why this patch looks like it hides a failure instead of fixing it, or ``None``."""
    for change in patch.changes:
        path, deleted = change.path, str(change.operation) == 'delete'
        if _CI_CONFIG.search(path):
            return f'{path}: perbaikan CI tidak boleh mengubah konfigurasi CI'
        if not _TEST_FILE.search(path):
            continue
        if deleted:
            return f'{path}: perbaikan CI tidak boleh menghapus test'
        before, after = Counter(_current(root, path).splitlines()), Counter((change.after_content or '').splitlines())
        added, removed = after - before, before - after
        if any(_SKIP.search(line) for line in added):
            return f'{path}: perbaikan CI tidak boleh melewati test'
        if sum(n for line, n in removed.items() if _ASSERTION.search(line)) > sum(n for line, n in added.items() if _ASSERTION.search(line)):
            return f'{path}: perbaikan CI tidak boleh mengurangi assertion'
    return None
