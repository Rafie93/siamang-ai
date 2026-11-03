"""ToolRunner: the Programmer's seam to an agent that reads, edits, and runs commands in a sandbox.

The Programmer defines only the shape (what comes back); whoever builds it supplies the runner, so the Programmer
imports no executor and no sandbox. What a runner returns is a *draft*: the Programmer still validates every file
path, step, size, and before-state exactly as it does for a one-shot reply.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolFile:
    path: str
    operation: str  # create | modify | delete
    content: str | None
    step_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ToolRun:
    files: tuple[ToolFile, ...]
    summary: str
    questions: tuple[str, ...]
    verified: bool  # a command exited 0 after the last edit
    turns: int
    runs: int
    status: str  # the runner's own status word
    reason: str = ""
    last_run: dict | None = None


class ToolRunner(Protocol):
    max_turns: int

    def available(self) -> bool: ...

    def run(self, project_root: str, task: str, ask: Callable[[str, str], str]) -> ToolRun: ...
