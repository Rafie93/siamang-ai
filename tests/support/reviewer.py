"""Test helpers for the Reviewer: a real Programmer patch, a real QAResult, and ways to forge either."""

from __future__ import annotations

import dataclasses
import hashlib

from siamang.analyst.finding import Finding
from siamang.programmer import Patch, ProposedChange
from siamang.qa import QAResult
from siamang.reviewer import ReviewerService, ReviewRequest
from tests.support.programmer import change
from tests.support.qa import codes, forge, propose, verify, with_changes  # noqa: F401

VALIDATION = "backend/routes/validation.py"
TEST_VALIDATION = "tests/test_validation.py"


def clean_changes() -> list[dict]:
    """A focused, tested change that the Reviewer has no concern about (QA passes it too)."""
    return [change(VALIDATION, "create", "def valid(user):\n    return bool(user)\n"),
            change(TEST_VALIDATION, "create", "def test_valid():\n    assert True\n")]


def chain(setup, changes: list | None = None) -> tuple[Patch, QAResult]:
    """A genuine Programmer patch and the genuine QAResult for it."""
    patch = propose(setup, changes)
    return patch, verify(setup, patch)


def review_request(setup, patch: Patch, qa: QAResult, *, patch_hash: str | None = None, qa_hash: str | None = None,
                   **over) -> ReviewRequest:
    data = dict(task_id=setup.world.task.task_id, project_id=setup.world.project.project_id, plan=setup.plan,
                analysis=setup.analysis, patch=patch, patch_hash=patch_hash or patch.content_hash, qa_result=qa,
                qa_hash=qa_hash or qa.content_hash)
    data.update(over)
    return ReviewRequest(**data)


def review(setup, patch: Patch, qa: QAResult, *, policy=None, **over):
    service = ReviewerService(setup.world.engine) if policy is None else ReviewerService(setup.world.engine,
                                                                                          policy=policy)
    return service.review(review_request(setup, patch, qa, **over))


# --- direct patches: exact control over every change (still genuine Phase 15 objects) --------------


def create(path: str, content: str = "def run():\n    return True\n", step: str = "step_03") -> dict:
    return dict(path=path, operation="create", step_id=step, reason="Needed by the task.", after_content=content,
                before="absent")


def modify(setup, path: str, content: str = "updated = True\n", step: str = "step_03") -> dict:
    current = (setup.root / path).read_bytes()
    return dict(path=path, operation="modify", step_id=step, reason="Needed by the task.", after_content=content,
                before="observed", before_hash="sha256:" + hashlib.sha256(current).hexdigest())


def delete(setup, path: str, step: str = "step_03") -> dict:
    current = (setup.root / path).read_bytes()
    return dict(path=path, operation="delete", step_id=step, reason="No longer needed.", before="observed",
                before_hash="sha256:" + hashlib.sha256(current).hexdigest())


def direct(setup, *specs: dict) -> Patch:
    changes = tuple(ProposedChange(change_id=f"change_{i:02d}", **spec) for i, spec in enumerate(specs, start=1))
    return Patch("pch_01DIRECT", setup.world.task.task_id, setup.world.project.project_id, "Direct patch.", changes)


def judged(setup, *specs: dict, policy=None):
    """Patch -> real QA -> Reviewer, for a direct patch."""
    patch = direct(setup, *specs)
    return review(setup, patch, verify(setup, patch), policy=policy)


def with_known_paths(setup, *paths: str):
    """The same setup, with an analysis that also cites ``paths`` as evidence (a real, re-validating analysis)."""
    extra = Finding("finding_99", "structure", "info", "Known files", "Files observed in the project.",
                    evidence=tuple(paths))
    analysis = dataclasses.replace(setup.analysis, findings=(*setup.analysis.findings, extra))
    return dataclasses.replace(setup, analysis=analysis)
