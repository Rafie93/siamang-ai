"""SystemAnalysis: structured, immutable, bounded; content excludes identity; policy bounds."""

import dataclasses

import pytest

from siamang.analyst import DEFAULT_POLICY, AnalysisPolicy, Component, Finding, SystemAnalysis
from siamang.analyst.analysis import MAX_FINDINGS_LIMIT, MAX_OUTPUT_LIMIT
from siamang.analyst.errors import InvalidAnalysisError

TSK, PRJ = "tsk_01ABC", "prj_01ABC"


def f(i, **over):
    data = dict(finding_id=f"finding_{i:02d}", category="structure", severity="info", title=f"F{i}",
                description="d", evidence=("README.md exists",))
    data.update(over)
    return Finding(**data)


def c(i):
    return Component(f"component_{i:02d}", f"c{i}", "application", ".", "d", evidence=("README.md exists",))


def analysis(**over):
    data = dict(analysis_id="ana_01ABC", task_id=TSK, project_id=PRJ, objective="Add login validation",
                summary="Summary.", components=(c(1),), findings=(f(1), f(2)), risks=("r",), unknowns=("u",),
                recommendations=("x",))
    data.update(over)
    return SystemAnalysis(**data)


def test_valid_and_structured():
    a = analysis()
    d = a.to_dict()
    assert set(d) == {"analysis_id", "task_id", "project_id", "objective", "summary", "components", "findings",
                      "constraints", "risks", "unknowns", "recommendations", "metadata", "content_hash"}
    assert d["content_hash"].startswith("sha256:") and a.size > 0


def test_immutable_collections():
    a = analysis(metadata={"k": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.findings = ()
    for coll in (a.findings, a.components, a.risks, a.unknowns, a.recommendations, a.constraints):
        assert isinstance(coll, tuple)
    with pytest.raises(TypeError):
        a.metadata["k"] = 2
    findings = [f(1)]
    b = analysis(findings=findings)
    findings.append(f(2))
    assert len(b.findings) == 1


def test_content_excludes_identity():
    a, b = analysis(analysis_id="ana_A"), analysis(analysis_id="ana_B")
    assert a.content() == b.content() and a.content_hash == b.content_hash and "analysis_id" not in a.content()
    assert analysis(risks=("other",)).content_hash != a.content_hash


@pytest.mark.parametrize("over", [
    {"analysis_id": "pln_01"}, {"analysis_id": "ana_"}, {"task_id": "tsk_x\n"}, {"project_id": "/x"},
    {"summary": ""}, {"objective": ""},
    {"findings": (f(1), f(1))}, {"components": (c(1), c(1))},
    {"findings": ({"finding_id": "finding_01"},)}, {"components": ("backend",)},
    {"risks": "one"}, {"unknowns": ("x" * 501,)}, {"recommendations": ("a",) * 51},
    {"metadata": {"api_key": "x"}},
])
def test_invalid(over):
    with pytest.raises(InvalidAnalysisError):
        analysis(**over)


def test_hard_caps():
    with pytest.raises(InvalidAnalysisError):
        analysis(findings=tuple(f(i) for i in range(1, MAX_FINDINGS_LIMIT + 2)))
    line = "src/" + "x" * 290 + ".py"  # 298-char evidence lines
    big = tuple(f(i, description="d" * 1000, evidence=(line,) * 20) for i in range(1, MAX_FINDINGS_LIMIT + 1))
    assert sum(len(x.description) + 20 * len(line) for x in big) > MAX_OUTPUT_LIMIT
    with pytest.raises(InvalidAnalysisError) as info:
        analysis(findings=big)
    assert info.value.details == ("analysis",)


def test_redacted_notes():
    a = analysis(risks=("leaked password=hunter2 in logs",))
    assert "hunter2" not in str(a.to_dict())


# --- policy -------------------------------------------------------------------------


def test_default_policy_is_conservative():
    d = DEFAULT_POLICY.to_dict()
    assert d["writes"] is False and d["follows_symlinks"] is False and d["reads_secret_files"] is False
    assert (d["max_files"], d["max_file_size"], d["max_depth"]) == (20, 65536, 3)
    assert d["analyzable_statuses"] == ["failed", "queued", "running", "waiting"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_POLICY.max_files = 10_000


@pytest.mark.parametrize("over", [
    {"max_files": 0}, {"max_files": 201}, {"max_file_size": 2 * 1024 * 1024}, {"max_total_bytes": 10**9},
    {"max_directory_entries": 10**6}, {"max_depth": 7}, {"max_findings": MAX_FINDINGS_LIMIT + 1},
    {"max_output_size": MAX_OUTPUT_LIMIT + 1}, {"max_files": True}, {"max_depth": 1.5},
    {"analyzable_statuses": {"completed"}}, {"analyzable_statuses": "queued"}, {"analyzable_statuses": set()},
])
def test_policy_cannot_be_unbounded(over):
    with pytest.raises(ValueError):
        AnalysisPolicy(**over)
