"""Deterministic QA checks. Each one only *reads* its inputs and appends findings.

| Check | What it establishes |
|---|---|
| patch integrity | the recorded ``patch_hash`` matches; every change re-validates under the Phase 15 rules and is unaltered (``after_hash`` recomputed); one operation per path, no case or file/directory conflicts |
| security | the Phase 15 path, forbidden-file, secret, and text rules hold for every change (re-validation); snapshot refusals (symlinks, case clashes, non-files) |
| policy | change count, file sizes, total size, path length within ``QAPolicy`` |
| plan consistency | every change names a step of the supplied Plan |
| analysis consistency | the analysis was made against this plan (when it recorded one) |
| project consistency | each recorded before-state still matches the project (read-only snapshot) |
| scope | each changed path lies where the analysis pointed (component locations, cited paths) |

Nothing here writes, executes, or calls another service. Findings carry
fixed messages and safe paths only: no file contents, secrets, or machine
paths.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from types import MappingProxyType
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.planner.plan import Plan
from siamang.programmer.change import BeforeState, Operation, ProposedChange
from siamang.programmer.errors import ChangeRejectedError, ProgrammingError
from siamang.programmer.patch import Patch
from siamang.programmer.snapshot import UNLISTED, ProjectSnapshot
from siamang.programmer.safety import validate_path
from siamang.qa.finding import Category, QAFinding, Severity, safe_path
from siamang.qa.policy import QAPolicy
from siamang.qa.errors import QAResultError

E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO
WITHHELD = "Finding details withheld (unsafe to display)."


class Findings:
    """Collects findings; a message that cannot be shown safely is replaced, never leaked."""

    def __init__(self) -> None:
        self.items: list[QAFinding] = []

    def add(self, code: str, severity: Severity, category: Category, message: str, path: Any = None, *,
            inconclusive: bool = False, **metadata: Any) -> None:
        path = safe_path(path) if path is not None else None
        try:
            finding = QAFinding(code, severity, category, message, path, inconclusive, metadata)
        except QAResultError:
            finding = QAFinding(code, severity, category, WITHHELD, path, inconclusive)
        self.items.append(finding)


def deep_frozen(value: Any) -> bool:
    """Is ``value`` still in its validated read-only form (read-only mappings and tuples all the way down)?"""
    if isinstance(value, MappingProxyType):
        return all(deep_frozen(v) for v in value.values())
    if isinstance(value, (dict, list, set, bytearray)):
        return False
    if isinstance(value, tuple):
        return all(deep_frozen(v) for v in value)
    return True


def _category(exc: ProgrammingError) -> Category:
    text = exc.message
    if text.startswith("path rejected") or "credential" in text:
        return Category.SECURITY
    return Category.PATCH


# --- patch integrity, security, policy ----------------------------------------------------


def check_patch(patch: Patch, patch_hash: str, policy: QAPolicy, out: Findings) -> tuple[str | None,
                                                                                         tuple[ProposedChange, ...]]:
    """Returns the supplied patch's hash (or ``None``) and the changes that re-validated unaltered."""
    supplied: str | None
    try:
        supplied = patch.content_hash
    except Exception:  # a forced, malformed object: its hash cannot be computed
        supplied = None
        out.add("patch.unhashable", E, Category.PATCH, "The patch content cannot be serialized or hashed.")
    if supplied is not None and supplied != patch_hash:
        out.add("patch.hash_mismatch", E, Category.PATCH,
                "The patch does not match the recorded patch hash (altered or forged).")

    changes = patch.changes if isinstance(patch.changes, tuple) else ()
    if not isinstance(patch.changes, tuple):
        out.add("patch.changes_invalid", E, Category.PATCH, "The patch changes are not a list of changes.")
    if not changes:
        out.add("patch.empty", W, Category.PATCH, "The patch proposes no changes; there is nothing to verify.",
                inconclusive=True)
    if len(changes) > policy.max_changes:
        out.add("policy.too_many_changes", E, Category.POLICY,
                f"The patch has {len(changes)} changes; the QA limit is {policy.max_changes}.")
        changes = changes[: policy.max_changes]

    valid: list[ProposedChange] = []
    total = 0
    for change in changes:
        if type(change) is not ProposedChange:
            out.add("patch.change_invalid", E, Category.PATCH, "A patch entry is not a proposed change.")
            continue
        path = change.path
        try:
            fresh = dataclasses.replace(change)  # every Phase 15 rule runs again
        except ProgrammingError as exc:
            out.add("patch.change_invalid", E, _category(exc), f"Change does not validate: {exc.message}", path)
            continue
        except Exception as exc:
            out.add("patch.change_invalid", E, Category.PATCH,
                    f"Change does not validate ({type(exc).__name__}).", path)
            continue
        try:  # same content, and still the validated read-only object (no mutable state forced in)
            unaltered = fresh.to_dict() == change.to_dict() and deep_frozen(change.metadata) and deep_frozen(
                change.evidence)
        except Exception:
            unaltered = False
        if fresh.after_hash != change.after_hash:
            out.add("patch.after_hash_mismatch", E, Category.PATCH,
                    "The recorded after_hash does not match the proposed content.", fresh.path)
            continue
        if not unaltered:
            out.add("patch.change_altered", E, Category.PATCH,
                    "The change was altered after it was validated.", fresh.path)
            continue
        if len(fresh.path) > policy.max_path_length:
            out.add("policy.path_too_long", E, Category.POLICY,
                    f"The path is longer than {policy.max_path_length} characters.")
            continue
        if fresh.size > policy.max_file_size:
            out.add("policy.file_too_large", E, Category.POLICY,
                    f"The proposed file exceeds {policy.max_file_size} bytes.", fresh.path)
            continue
        total += fresh.size
        valid.append(fresh)
    if total > policy.max_total_bytes:
        out.add("policy.total_too_large", E, Category.POLICY,
                f"The proposed content exceeds {policy.max_total_bytes} bytes.")
    try:
        Patch(patch.patch_id, patch.task_id, patch.project_id, patch.summary, tuple(valid))
    except ProgrammingError as exc:  # conflicts (same path, case-only, file/directory) or a bad summary
        out.add("patch.conflict", E, Category.STRUCTURE, f"The change set is not consistent: {exc.message}")
    except Exception as exc:
        out.add("patch.conflict", E, Category.STRUCTURE, f"The change set is not consistent ({type(exc).__name__}).")
    return supplied, tuple(valid)


# --- plan and analysis consistency, scope ------------------------------------------------------


def check_plan(changes: Iterable[ProposedChange], plan: Plan, out: Findings) -> None:
    steps = {s.step_id for s in plan.steps}
    for c in changes:
        if c.step_id not in steps:
            out.add("plan.unknown_step", E, Category.CONSISTENCY,
                    "The change names a step that is not in the supplied plan.", c.path, step_id=c.step_id)


def scope_roots(analysis: SystemAnalysis) -> tuple[frozenset[str], frozenset[str]]:
    """(cited file paths, directories) the analysis points at. The project root alone is not a scope."""
    paths, dirs = set(), set()
    for c in analysis.components:
        if c.location != ".":
            dirs.add(c.location)
    for item in (*analysis.findings, *analysis.components):
        for line in item.evidence:
            try:
                path = validate_path(line)
            except ValueError:
                continue
            if " " in path:
                continue
            paths.add(path)
            if "/" in path:
                dirs.add(path.rsplit("/", 1)[0])
    return frozenset(paths), frozenset(dirs)


def check_analysis(changes: Iterable[ProposedChange], plan: Plan, analysis: SystemAnalysis, out: Findings) -> None:
    planned = analysis.metadata.get("plan_content_hash")
    if planned is not None and planned != plan.content_hash:
        out.add("analysis.plan_mismatch", W, Category.CONSISTENCY,
                "The system analysis was made against a different plan than the one supplied.")
    paths, dirs = scope_roots(analysis)
    for c in changes:
        if c.path in paths or any(c.path.startswith(d + "/") for d in dirs):
            continue
        out.add("scope.outside_analysis", W, Category.SCOPE,
                "The changed path is outside every location the analysis identified (judged by names only).",
                c.path)


# --- project consistency (read-only snapshot) ----------------------------------------------------


def check_project(changes: Iterable[ProposedChange], snapshot: ProjectSnapshot, out: Findings) -> list[dict]:
    entries = []
    for c in changes:
        entry: dict[str, Any] = {"change_id": c.change_id, "path": c.path, "operation": str(c.operation),
                                 "recorded": str(c.before), "current": None, "current_hash": None}
        entries.append(entry)
        try:
            now = snapshot.before(c.path, c.change_id)
        except ChangeRejectedError as exc:  # symlink in the way, case-only clash, directory or special file
            out.add("project.path_blocked", E, Category.SECURITY, f"The path cannot be changed safely: {exc.message}",
                    c.path)
            continue
        entry.update(current=str(now.state), current_hash=now.hash)
        exists_unknown = now.state is BeforeState.UNVERIFIED and now.note == UNLISTED
        if c.operation is Operation.CREATE:
            if now.state is BeforeState.ABSENT:
                continue
            if exists_unknown:
                out.add("project.state_unverified", W, Category.CONSISTENCY,
                        "Whether the file already exists could not be established.", c.path, inconclusive=True)
            else:
                out.add("project.create_target_exists", E, Category.CONSISTENCY,
                        "The change creates a file that already exists.", c.path)
            continue
        if now.state is BeforeState.ABSENT:
            out.add("project.target_missing", E, Category.CONSISTENCY,
                    f"The change would {c.operation} a file that does not exist.", c.path)
            continue
        if c.before is BeforeState.UNVERIFIED:
            out.add("project.before_unverified", W, Category.CONSISTENCY,
                    "The proposal recorded no verified before-state, so QA cannot confirm it was based on the "
                    "current file.", c.path, inconclusive=True)
            continue
        if now.state is not BeforeState.OBSERVED:
            out.add("project.state_unverified", W, Category.CONSISTENCY,
                    "The current file could not be read to compare with the recorded before-state.", c.path,
                    inconclusive=True)
            continue
        if now.hash != c.before_hash:
            out.add("project.before_hash_mismatch", E, Category.CONSISTENCY,
                    "The file changed after the proposal was made (before_hash differs).", c.path)
    return entries


def bounded(findings: list[QAFinding], limit: int) -> tuple[QAFinding, ...]:
    """Errors first (stable), then cut to ``limit``; a cut is itself an inconclusive finding."""
    order = {E: 0, W: 1, I: 2}
    ranked = sorted(findings, key=lambda f: order[f.severity])
    if len(ranked) <= limit:
        return tuple(ranked)
    note = QAFinding("qa.findings_truncated", W, Category.POLICY,
                     f"{len(ranked) - limit + 1} further findings were omitted (policy limit).", inconclusive=True)
    if limit == 1:  # one slot: a blocking error must survive, or the cut makes the result inconclusive
        return (ranked[0],) if ranked[0].blocking else (note,)
    return (*ranked[: limit - 1], note)
