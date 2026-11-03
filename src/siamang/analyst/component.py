"""Component: one detected part of the project (an application, test suite, configuration, ...).

``location`` is a project-relative directory (``.`` for the root), never an
absolute path. ``kind`` includes ``unknown`` so a project need not fit a
fixed shape. Like findings, a component states its ``confidence`` and the
``evidence`` it rests on.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.analyst.errors import InvalidAnalysisError
from siamang.analyst.finding import Confidence, enum_value, safe_evidence
from siamang.planner.request import json_object, text_field

COMPONENT_ID_RE = re.compile(r"^component_[0-9]{2,3}\Z")
NAME_MAX = 80
DESCRIPTION_MAX = 500
TECHNOLOGIES_MAX = 10
LOCATION_MAX = 200
# Relative POSIX path of plain segments (``.`` and ``..`` segments refused separately), or "." for the root.
_LOCATION_RE = re.compile(r"^(\.|[A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*)\Z")


class ComponentKind(StrEnum):
    APPLICATION = "application"
    LIBRARY = "library"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    TESTS = "tests"
    CONFIGURATION = "configuration"
    DOCUMENTATION = "documentation"
    DEPLOYMENT = "deployment"
    BUILD = "build"
    UNKNOWN = "unknown"


def component_id(position: int) -> str:
    return f"component_{position:02d}"


@dataclass(frozen=True)
class Component:
    component_id: str
    name: str
    kind: ComponentKind
    location: str
    description: str
    technologies: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: Confidence = Confidence.OBSERVED
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.component_id, str) or not COMPONENT_ID_RE.fullmatch(self.component_id):
            raise InvalidAnalysisError("component_id must look like component_01", details=("component_id",))
        cid = self.component_id
        object.__setattr__(self, "name", text_field(self.name, "name", NAME_MAX, error=InvalidAnalysisError))
        object.__setattr__(self, "kind", enum_value(ComponentKind, self.kind, "kind", cid))
        object.__setattr__(self, "confidence", enum_value(Confidence, self.confidence, "confidence", cid))
        location = self.location
        if (not isinstance(location, str) or len(location) > LOCATION_MAX or not _LOCATION_RE.fullmatch(location)
                or any(part in (".", "..") for part in location.split("/") if location != ".")):
            raise InvalidAnalysisError("location must be a project-relative directory", details=(cid,))
        object.__setattr__(self, "description", text_field(self.description, "description", DESCRIPTION_MAX,
                                                           multiline=True, error=InvalidAnalysisError))
        techs = self.technologies
        if isinstance(techs, (str, bytes)) or not isinstance(techs, (list, tuple)) or len(techs) > TECHNOLOGIES_MAX:
            raise InvalidAnalysisError(f"technologies must be a list of at most {TECHNOLOGIES_MAX} names",
                                       details=(cid,))
        object.__setattr__(self, "technologies", tuple(text_field(t, "technology", 40, error=InvalidAnalysisError)
                                                       for t in techs))
        evidence = safe_evidence(self.evidence, cid)
        if self.confidence is not Confidence.UNKNOWN and not evidence:
            raise InvalidAnalysisError(f"an {self.confidence} component needs evidence", details=(cid,))
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "metadata", json_object(self.metadata, "component.metadata",
                                                         error=InvalidAnalysisError))

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "name": self.name,
            "kind": str(self.kind),
            "location": self.location,
            "description": self.description,
            "technologies": list(self.technologies),
            "evidence": list(self.evidence),
            "confidence": str(self.confidence),
            "metadata": thaw(self.metadata),
        }
