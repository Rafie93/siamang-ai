"""ProgrammingPolicy: resource bounds for one programming attempt. Not a permission system.

Conservative fixed defaults, each bounded by a hard cap, so no policy can
permit unbounded changes, content, context, or model output. There is no
environment or config override, and no "unlimited" value.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.planner.request import INSTRUCTION_MAX
from siamang.programmer.change import MAX_FILE_LIMIT
from siamang.programmer.errors import ProgrammingPolicyError
from siamang.programmer.patch import MAX_CHANGES_LIMIT, MAX_TOTAL_LIMIT, SUMMARY_LIMIT
from siamang.programmer.request import ProgrammingRequest
from siamang.programmer.result import NOTES_LIMIT
from siamang.tasks.lifecycle import TaskStatus

NOT_PROGRAMMABLE = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})

# name -> (default, hard cap)
LIMITS: dict[str, tuple[int, int]] = {
    "max_changes": (20, MAX_CHANGES_LIMIT),
    "max_file_size": (256 * 1024, MAX_FILE_LIMIT),  # bytes of proposed content per file
    "max_total_output": (1024 * 1024, MAX_TOTAL_LIMIT),  # bytes of proposed content per patch
    "max_summary": (2000, SUMMARY_LIMIT),
    "max_warnings": (50, NOTES_LIMIT),
    "max_unknowns": (50, NOTES_LIMIT),
    "max_instruction_length": (4000, INSTRUCTION_MAX),
    "max_context_files": (8, 32),  # current files shown to the model (chosen by the analysis, never the caller)
    "max_context_file_size": (32 * 1024, 256 * 1024),  # larger files are not shown
    "max_context_bytes": (256 * 1024, 1024 * 1024),  # serialized model context
    "max_model_response": (2 * 1024 * 1024, 8 * 1024 * 1024),  # raw model text accepted for parsing
    "model_timeout_secs": (120, 600),
}


@dataclass(frozen=True)
class ProgrammingPolicy:
    max_changes: int = LIMITS["max_changes"][0]
    max_file_size: int = LIMITS["max_file_size"][0]
    max_total_output: int = LIMITS["max_total_output"][0]
    max_summary: int = LIMITS["max_summary"][0]
    max_warnings: int = LIMITS["max_warnings"][0]
    max_unknowns: int = LIMITS["max_unknowns"][0]
    max_instruction_length: int = LIMITS["max_instruction_length"][0]
    max_context_files: int = LIMITS["max_context_files"][0]
    max_context_file_size: int = LIMITS["max_context_file_size"][0]
    max_context_bytes: int = LIMITS["max_context_bytes"][0]
    max_model_response: int = LIMITS["max_model_response"][0]
    model_timeout_secs: int = LIMITS["model_timeout_secs"][0]
    programmable_statuses: frozenset[TaskStatus] = frozenset(TaskStatus) - NOT_PROGRAMMABLE

    def __post_init__(self) -> None:
        for name, value in self._limits().items():
            cap = LIMITS[name][1]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")
        if self.max_file_size > self.max_total_output:
            raise ValueError("max_file_size cannot exceed max_total_output")
        statuses = self.programmable_statuses
        if isinstance(statuses, (str, bytes)) or not isinstance(statuses, Iterable):
            raise ValueError("programmable_statuses must be a collection of task statuses")
        try:
            statuses = frozenset(TaskStatus(s) for s in statuses)
        except ValueError:
            raise ValueError("programmable_statuses must be task statuses") from None
        if statuses & NOT_PROGRAMMABLE or not statuses:
            raise ValueError("completed and cancelled tasks cannot be programmed against")
        object.__setattr__(self, "programmable_statuses", statuses)

    def _limits(self) -> dict[str, int]:
        fields = self.__dict__  # plain field read; no dynamic attribute lookup
        return {name: fields[name] for name in LIMITS}

    def check_request(self, request: ProgrammingRequest) -> None:
        if len(request.instruction) > self.max_instruction_length:
            raise ProgrammingPolicyError(f"instruction exceeds {self.max_instruction_length} characters",
                                         details=("instruction",))

    def check_task(self, status: TaskStatus) -> None:
        if status not in self.programmable_statuses:
            raise ProgrammingPolicyError(f"a {status} task cannot be programmed against")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._limits(),
            "programmable_statuses": sorted(str(s) for s in self.programmable_statuses),
            "model_calls_per_request": 1,
            "retries": 0,
            "writes": False,
            "applies_patches": False,
            "executes": False,
        }


DEFAULT_POLICY = ProgrammingPolicy()
