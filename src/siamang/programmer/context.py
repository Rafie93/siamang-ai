"""ProgrammingContext: the bounded, scrubbed data a strategy may show a model. Never the whole project.

What goes in:

* the task (ID, description) and the request (instruction, input, constraints);
* the project's ID, name, and type: **no** ``root_path``;
* the Plan's objective, steps, and constraints;
* the SystemAnalysis: summary, components, findings (each with its
  ``observed``/``inferred``/``unknown`` confidence), risks, unknowns, and
  recommendations;
* at most ``max_context_files`` current files, chosen **only** from the
  analysis evidence (never by the caller or the model), each small, plain
  text, and free of credential material and machine paths. Withheld files
  are counted and reported as unknowns.

Every other string is scrubbed: the project root and home-directory paths
become placeholders, credential-looking text is withheld, and the shared
redactor runs last. The serialized context is bounded by
``max_context_bytes``: files are dropped from the end first; if the rest
still does not fit, programming is refused.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.log import redact
from siamang.planner.plan import Plan
from siamang.programmer.errors import ProgrammingPolicyError
from siamang.programmer.policy import ProgrammingPolicy
from siamang.programmer.request import ProgrammingRequest
from siamang.programmer.safety import find_secret, forbidden_reason, thaw, validate_path
from siamang.programmer.snapshot import ProjectSnapshot
from siamang.projects import Project
from siamang.tasks.definition import Task

PROJECT_PLACEHOLDER = "<project>"
PATH_PLACEHOLDER = "<path withheld>"
SECRET_PLACEHOLDER = "[withheld: credential material]"
_MACHINE_PATH_RE = re.compile(r"(?:(?<![\w.~-])(?:/Users|/home|/root|/private|/var/folders)/|(?<![\w/])~/)[^\s\"'<>]*")
HOME_MARKERS = ("/Users/", "/home/", "/root/")


def scrub(text: str, root_path: str) -> str:
    """Model-bound text: no machine paths, no credential material."""
    if root_path and root_path != "/":
        text = text.replace(root_path, PROJECT_PLACEHOLDER)
    text = _MACHINE_PATH_RE.sub(PATH_PLACEHOLDER, text)
    if find_secret(text):
        return SECRET_PLACEHOLDER
    return redact(text)


def _scrub_all(value: Any, root_path: str) -> Any:
    if isinstance(value, str):
        return scrub(value, root_path)
    if isinstance(value, dict):
        return {k: _scrub_all(v, root_path) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_all(v, root_path) for v in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def candidate_paths(analysis: SystemAnalysis) -> tuple[str, ...]:
    """Evidence lines that are plain, allowed file paths, in analysis order, deduplicated."""
    seen: dict[str, None] = {}
    for item in (*analysis.findings, *analysis.components):
        for line in item.evidence:
            try:
                path = validate_path(line)
            except ValueError:
                continue
            if forbidden_reason(path) is None and " " not in path:
                seen.setdefault(path, None)
    return tuple(seen)


@dataclass(frozen=True)
class ContextFile:
    path: str
    content: str


@dataclass(frozen=True)
class ProgrammingContext:
    request: ProgrammingRequest
    task: Task
    project: Project
    plan: Plan
    analysis: SystemAnalysis
    files: tuple[ContextFile, ...]
    withheld: tuple[str, ...]  # "path: reason", for unknowns
    policy: ProgrammingPolicy

    @property
    def step_ids(self) -> frozenset[str]:
        return frozenset(s.step_id for s in self.plan.steps)

    def payload(self) -> dict[str, Any]:
        """The model-bound data. Scrubbed, deterministic, without any filesystem location."""
        root = self.project.root_path
        a = self.analysis
        body = {
            "task": {"task_id": self.task.task_id, "title": self.task.title, "description": self.task.description},
            "request": {"instruction": self.request.instruction,
                        "input": thaw(self.request.input),
                        "constraints": list(self.request.constraints)},
            "project": {"project_id": self.project.project_id, "name": self.project.name,
                        "project_type": str(self.project.project_type)},
            "plan": {"objective": self.plan.objective, "constraints": list(self.plan.constraints),
                     "steps": [{"step_id": s.step_id, "title": s.title, "description": s.description,
                                "role": str(s.role), "dependencies": list(s.dependencies),
                                "constraints": list(s.constraints)} for s in self.plan.steps]},
            "analysis": {
                "summary": a.summary,
                "components": [{k: v for k, v in c.to_dict().items() if k != "metadata"} for c in a.components],
                "findings": [{k: v for k, v in f.to_dict().items() if k != "metadata"} for f in a.findings],
                "risks": list(a.risks),
                "unknowns": list(a.unknowns),
                "recommendations": list(a.recommendations),
            },
        }
        body = _scrub_all(body, root)
        body["files"] = [{"path": f.path, "content": f.content} for f in self.files]  # vetted in build_context
        body["limits"] = {"max_changes": self.policy.max_changes, "max_file_size": self.policy.max_file_size,
                          "max_total_output": self.policy.max_total_output}
        return body

    def serialized(self) -> str:
        return _canonical(self.payload())


def build_context(request: ProgrammingRequest, task: Task, project: Project, snapshot: ProjectSnapshot,
                  policy: ProgrammingPolicy) -> ProgrammingContext:
    machine = (project.root_path, *HOME_MARKERS)
    files: list[ContextFile] = []
    withheld: list[str] = []
    for path in candidate_paths(request.analysis):
        if len(files) >= policy.max_context_files:
            withheld.append(f"{path}: context file limit")
            continue
        text, reason = snapshot.context_text(path, machine)
        if text is None:
            if reason != "not found":
                withheld.append(f"{path}: {reason}")
            continue
        files.append(ContextFile(path, text))

    def make(shown: list[ContextFile], dropped: list[str]) -> ProgrammingContext:
        return ProgrammingContext(request, task, project, request.plan, request.analysis, tuple(shown),
                                  tuple(dropped), policy)

    context = make(files, withheld)
    while len(context.serialized().encode("utf-8")) > policy.max_context_bytes:
        if not files:
            raise ProgrammingPolicyError(f"model context exceeds {policy.max_context_bytes} bytes",
                                         details=("context",))
        dropped = files.pop()
        withheld.append(f"{dropped.path}: context size limit")
        context = make(files, withheld)
    return context
