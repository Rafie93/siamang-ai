"""Phase 27 in the runtime: the Programmer's tool runner, backed by the sandbox executor.

Kept apart from ``runtime.py`` so the Programmer sees only :class:`~siamang.programmer.tools.ToolRunner`. The sandbox
service itself is built by the runtime; this module only translates the executor's result into the Programmer's.
"""
from __future__ import annotations

from siamang.executor import ToolExecutor
from siamang.programmer.tools import ToolFile, ToolRun


class SandboxToolRunner:
    def __init__(self, executor: ToolExecutor):
        self._executor = executor
        self.max_turns = executor.policy.max_turns

    def available(self) -> bool:
        return self._executor.available()

    def run(self, project_root, task, ask) -> ToolRun:
        result = self._executor.run(project_root, task, ask)
        files = tuple(ToolFile(c.path, c.operation, c.after_content, c.step_id, c.reason) for c in result.changes)
        return ToolRun(files, result.summary, result.questions, result.verified, result.turns, result.runs,
                       str(result.status), result.reason, result.last_run)
