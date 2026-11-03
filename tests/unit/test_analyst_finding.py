"""Finding and Component: explicit confidence, safe evidence, immutable."""

import dataclasses

import pytest

from siamang.analyst import Component, ComponentKind, Confidence, Finding, FindingCategory, Severity
from siamang.analyst.errors import InvalidAnalysisError


def finding(**over):
    data = dict(finding_id="finding_01", category="technology", severity="info", title="Vue frontend",
                description="Declared dependency.", evidence=("frontend/package.json exists",),
                confidence="inferred")
    data.update(over)
    return Finding(**data)


def component(**over):
    data = dict(component_id="component_01", name="frontend", kind="frontend", location="frontend",
                description="Web client.", technologies=("Vue",), evidence=("frontend/package.json exists",),
                confidence="inferred")
    data.update(over)
    return Component(**data)


def test_valid_finding():
    f = finding()
    assert (f.category, f.severity, f.confidence) == (FindingCategory.TECHNOLOGY, Severity.INFO, Confidence.INFERRED)
    assert f.to_dict()["evidence"] == ["frontend/package.json exists"]


def test_finding_is_immutable():
    f = finding(metadata={"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.severity = "critical"
    with pytest.raises(TypeError):
        f.metadata["a"] = 2
    assert isinstance(f.evidence, tuple)


def test_closed_vocabularies():
    assert {c.value for c in Confidence} == {"observed", "inferred", "unknown"}
    assert {s.value for s in Severity} == {"info", "low", "medium", "high", "critical"}
    assert "unknown" in {c.value for c in FindingCategory} and "unknown" in {k.value for k in ComponentKind}


def test_observed_and_inferred_need_evidence():
    with pytest.raises(InvalidAnalysisError):
        finding(confidence="observed", evidence=())
    with pytest.raises(InvalidAnalysisError):
        finding(confidence="inferred", evidence=())
    assert finding(confidence="unknown", evidence=()).evidence == ()


@pytest.mark.parametrize("over", [
    {"finding_id": "f1"}, {"finding_id": "finding_1"}, {"finding_id": None},
    {"category": "vibes"}, {"severity": "urgent"}, {"severity": "INFO"}, {"confidence": "certain"},
    {"title": ""}, {"title": "a\nb"}, {"description": ""},
    {"evidence": "one string"}, {"evidence": ("/Users/someone/project/x",)}, {"evidence": ("~/x",)},
    {"evidence": ("../../etc/passwd",)}, {"evidence": ("C:\\\\x",)}, {"evidence": ("a\nb",)},
    {"evidence": ("x",) * 21}, {"metadata": {"token": "t"}},
])
def test_invalid_findings(over):
    with pytest.raises(InvalidAnalysisError):
        finding(**over)


def test_evidence_and_text_are_redacted():
    f = finding(title="token=abc123secret", description="password: hunter2",
                evidence=("API_KEY=sk-live-123456", "Authorization: Bearer abc.def"))
    text = str(f.to_dict())
    assert "abc123secret" not in text and "hunter2" not in text and "sk-live-123456" not in text
    assert "abc.def" not in text


def test_valid_component():
    c = component()
    assert c.kind is ComponentKind.FRONTEND and c.technologies == ("Vue",)
    assert component(location=".", kind="unknown", confidence="unknown", evidence=()).location == "."
    assert component(location=".github/workflows").location == ".github/workflows"


@pytest.mark.parametrize("over", [
    {"component_id": "c1"}, {"kind": "microservice-ish"}, {"location": "/abs"}, {"location": "../x"},
    {"location": "a/../b"}, {"location": "a//b"}, {"location": ""}, {"location": "a/./b"}, {"location": "~x"},
    {"technologies": "Vue"}, {"technologies": ("x",) * 11}, {"confidence": "observed", "evidence": ()},
    {"name": ""}, {"metadata": {"secret": "x"}},
])
def test_invalid_components(over):
    with pytest.raises(InvalidAnalysisError):
        component(**over)


def test_component_is_immutable():
    c = component()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.location = "."
    assert isinstance(c.technologies, tuple) and isinstance(c.evidence, tuple)
