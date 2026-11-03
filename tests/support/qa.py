"""Test helpers for QA: a real proposal from the Programmer (fake model), and ways to forge one."""

from __future__ import annotations

import dataclasses
from typing import Any

from siamang.programmer import Patch, ProposedChange
from siamang.qa import QARequest, QAService
from tests.support.programmer import AUTH, AUTH_TEXT, STEP, change, make_model, reply, sha  # noqa: F401


def propose(setup, changes: list | None = None) -> Patch:
    """A genuine Phase 15 patch for ``setup`` (default: modify auth.py + create a helper)."""
    changes = changes if changes is not None else [
        change(), change("backend/routes/validation.py", "create", "def valid(user):\n    return bool(user)\n")]
    result = setup.programmer(make_model(reply(changes))).program(setup.request())
    assert result.status.value == "completed", result.error
    return result.patch


def qa_request(setup, patch: Patch, patch_hash: str | None = None, **over) -> QARequest:
    data = dict(task_id=setup.world.task.task_id, project_id=setup.world.project.project_id, plan=setup.plan,
                analysis=setup.analysis, patch=patch, patch_hash=patch_hash or patch.content_hash)
    data.update(over)
    return QARequest(**data)


def verify(setup, patch: Patch, patch_hash: str | None = None, *, policy=None, **over):
    service = QAService(setup.world.engine) if policy is None else QAService(setup.world.engine, policy=policy)
    return service.verify(qa_request(setup, patch, patch_hash, **over))


def forge(obj: Any, **fields) -> Any:
    """A copy of a frozen object with fields forced past validation (what a hostile caller could do)."""
    copy = dataclasses.replace(obj) if not isinstance(obj, ProposedChange) else dataclasses.replace(obj)
    for name, value in fields.items():
        object.__setattr__(copy, name, value)
    return copy


def with_changes(patch: Patch, changes: tuple) -> Patch:
    return forge(patch, changes=tuple(changes))


def codes(result) -> set[str]:
    return {f.code for f in result.findings}
