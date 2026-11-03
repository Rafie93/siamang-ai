"""AnalysisPolicy: resource and scope bounds for one analysis. Not a permission system.

Conservative fixed defaults, each bounded by a hard cap, so no policy can
permit unbounded reading, listing, or output. There is no environment or
config override.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.analyst.analysis import MAX_COMPONENTS_LIMIT, MAX_FINDINGS_LIMIT, MAX_OUTPUT_LIMIT
from siamang.analyst.errors import AnalysisPolicyError
from siamang.analyst.request import AnalysisRequest
from siamang.planner.request import INSTRUCTION_MAX
from siamang.tasks.lifecycle import TaskStatus

NOT_ANALYZABLE = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})

# name -> (default, hard cap)
LIMITS: dict[str, tuple[int, int]] = {
    "max_files": (20, 200),  # files whose content may be read
    "max_file_size": (64 * 1024, 1024 * 1024),  # larger files are noted, never read
    "max_total_bytes": (256 * 1024, 8 * 1024 * 1024),  # all file reads together
    "max_directory_entries": (2000, 20_000),  # all listed entries together
    "max_depth": (3, 6),  # directory levels below the project root
    "max_findings": (50, MAX_FINDINGS_LIMIT),
    "max_components": (20, MAX_COMPONENTS_LIMIT),
    "max_output_size": (128 * 1024, MAX_OUTPUT_LIMIT),  # serialized analysis bytes
    "max_instruction_length": (4000, INSTRUCTION_MAX),
}


@dataclass(frozen=True)
class AnalysisPolicy:
    max_files: int = LIMITS["max_files"][0]
    max_file_size: int = LIMITS["max_file_size"][0]
    max_total_bytes: int = LIMITS["max_total_bytes"][0]
    max_directory_entries: int = LIMITS["max_directory_entries"][0]
    max_depth: int = LIMITS["max_depth"][0]
    max_findings: int = LIMITS["max_findings"][0]
    max_components: int = LIMITS["max_components"][0]
    max_output_size: int = LIMITS["max_output_size"][0]
    max_instruction_length: int = LIMITS["max_instruction_length"][0]
    analyzable_statuses: frozenset[TaskStatus] = frozenset(TaskStatus) - NOT_ANALYZABLE

    def __post_init__(self) -> None:
        values = {"max_files": self.max_files, "max_file_size": self.max_file_size,
                  "max_total_bytes": self.max_total_bytes, "max_directory_entries": self.max_directory_entries,
                  "max_depth": self.max_depth, "max_findings": self.max_findings,
                  "max_components": self.max_components, "max_output_size": self.max_output_size,
                  "max_instruction_length": self.max_instruction_length}
        for name, value in values.items():
            cap = LIMITS[name][1]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")
        statuses = self.analyzable_statuses
        if isinstance(statuses, (str, bytes)) or not isinstance(statuses, Iterable):
            raise ValueError("analyzable_statuses must be a collection of task statuses")
        try:
            statuses = frozenset(TaskStatus(s) for s in statuses)
        except ValueError:
            raise ValueError("analyzable_statuses must be task statuses") from None
        if statuses & NOT_ANALYZABLE or not statuses:
            raise ValueError("completed and cancelled tasks cannot be analyzed")
        object.__setattr__(self, "analyzable_statuses", statuses)

    def check_request(self, request: AnalysisRequest) -> None:
        if len(request.instruction) > self.max_instruction_length:
            raise AnalysisPolicyError(f"instruction exceeds {self.max_instruction_length} characters",
                                      details=("instruction",))

    def check_task(self, status: TaskStatus) -> None:
        if status not in self.analyzable_statuses:
            raise AnalysisPolicyError(f"a {status} task cannot be analyzed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_files": self.max_files,
            "max_file_size": self.max_file_size,
            "max_total_bytes": self.max_total_bytes,
            "max_directory_entries": self.max_directory_entries,
            "max_depth": self.max_depth,
            "max_findings": self.max_findings,
            "max_components": self.max_components,
            "max_output_size": self.max_output_size,
            "max_instruction_length": self.max_instruction_length,
            "analyzable_statuses": sorted(str(s) for s in self.analyzable_statuses),
            "writes": False,
            "follows_symlinks": False,
            "reads_secret_files": False,
        }


DEFAULT_POLICY = AnalysisPolicy()
