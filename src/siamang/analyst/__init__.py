"""System Analyst (Phase 14): read-only analysis of a registered project.

Answers "what does the system currently contain, and what matters for the
planned work?" with a structured, immutable, bounded :class:`SystemAnalysis`.
Filesystem access goes only through :class:`ProjectInspector` (confined to
the registered root, no symlinks, no secret files, budgeted); Git only
through the Phase 08 :class:`~siamang.git.GitWorkspaceManager`. It writes
nothing, executes nothing, changes no task, persists nothing, and emits no
events.
"""

from siamang.analyst.analysis import SystemAnalysis
from siamang.analyst.component import Component, ComponentKind
from siamang.analyst.errors import AnalysisError, AnalysisErrorCode
from siamang.analyst.finding import Confidence, Finding, FindingCategory, Severity
from siamang.analyst.git_state import GitState
from siamang.analyst.inspection import ProjectInspector, is_sensitive
from siamang.analyst.policy import DEFAULT_POLICY, AnalysisPolicy
from siamang.analyst.request import AnalysisRequest
from siamang.analyst.service import SystemAnalyst
from siamang.analyst.strategy import KNOWN_STRATEGIES, AnalysisStrategy, BasicStrategy

__all__ = [
    "DEFAULT_POLICY",
    "KNOWN_STRATEGIES",
    "AnalysisError",
    "AnalysisErrorCode",
    "AnalysisPolicy",
    "AnalysisRequest",
    "AnalysisStrategy",
    "BasicStrategy",
    "Component",
    "ComponentKind",
    "Confidence",
    "Finding",
    "FindingCategory",
    "GitState",
    "ProjectInspector",
    "Severity",
    "SystemAnalysis",
    "SystemAnalyst",
    "is_sensitive",
]
