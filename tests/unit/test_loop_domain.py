"""Loop domain: LoopRequest, LoopPolicy, LoopFeedback, LoopIteration, LoopResult. Bounded, immutable, never disguised."""

import dataclasses
import json
from types import SimpleNamespace

import pytest

from siamang.analyst import SystemAnalysis
from siamang.loop import (
    DEFAULT_POLICY,
    LoopFeedback,
    LoopIteration,
    LoopPolicy,
    LoopRequest,
    LoopResult,
)
from siamang.loop.errors import LoopRequestError, LoopResultError
from siamang.loop.feedback import FEEDBACK_KEY, MESSAGE_MAX, WITHHELD, as_input, collect
from siamang.loop.policy import LIMITS
from siamang.loop.request import INPUT_MAX, LOOP_KEYS
from siamang.loop.result import NOT_DONE
from siamang.planner import Plan
from tests.support.analyst import SECRET
from tests.support.loop import CLEAN, CONCERN, loop_request, run, unrelated
from tests.support.programmer import make_setup


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


# --- request --------------------------------------------------------------------------------


def test_valid_request(setup):
    r = loop_request(setup, input={"ticket": {"id": "L-1"}}, constraints=("Keep it small",), metadata={"a": [1]})
    d = r.to_dict()
    assert d["plan_id"] == setup.plan.plan_id and d["analysis_id"] == setup.analysis.analysis_id
    assert d["input"] == {"ticket": {"id": "L-1"}} and d["constraints"] == ["Keep it small"]


def test_request_is_deeply_immutable_and_copied(setup):
    caller = {"ticket": {"tags": ["a"]}}
    r = loop_request(setup, input=caller, metadata=caller)
    caller["ticket"]["tags"].append("b")
    caller["new"] = 1
    assert r.to_dict()["input"] == {"ticket": {"tags": ["a"]}} == r.to_dict()["metadata"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.instruction = "x"
    for mutate in (lambda: r.input.__setitem__("x", 1), lambda: r.input["ticket"]["tags"].append("b"),
                   lambda: r.metadata["ticket"].__setitem__("x", 1)):
        with pytest.raises((TypeError, AttributeError)):
            mutate()


@pytest.mark.parametrize("key", [FEEDBACK_KEY, "feedback", "max_iterations", "unlimited", "budget", "policy",
                                 "stop_reason", "iterations", "approve", "apply", "commit", "merge", "deploy",
                                 "retry", "repair", "status", "verdict", "command", "shell", "path", "agent_id",
                                 "model_id", "execute"])
def test_forbidden_keys_at_any_depth(setup, key):
    for field_name in ("input", "metadata"):
        for value in ({key: 1}, {"a": {key: 1}}, {"a": [{"b": [{key.upper(): 1}]}]}):
            with pytest.raises(LoopRequestError):
                loop_request(setup, **{field_name: value})
    assert {"max_iterations", "unlimited", FEEDBACK_KEY, "approve"} <= LOOP_KEYS


def test_snapshots_must_be_exact(setup):
    live_plan = type("LivePlan", (Plan,), {})(**{f.name: getattr(setup.plan, f.name) for f in dataclasses.fields(Plan)})
    live_analysis = type("LiveAnalysis", (SystemAnalysis,), {})(
        **{f.name: getattr(setup.analysis, f.name) for f in dataclasses.fields(SystemAnalysis)})
    for over in ({"plan": live_plan}, {"plan": setup.plan.to_dict()}, {"analysis": live_analysis},
                 {"analysis": None}):
        with pytest.raises(LoopRequestError):
            loop_request(setup, **over)


@pytest.mark.parametrize("over", [{"task_id": "tsk_../x"}, {"project_id": ""}, {"instruction": ""},
                                  {"instruction": "x" * 20_000}, {"instruction": "a\x00b"}, {"constraints": "x"},
                                  {"input": "x"}, {"input": {"k": object()}}, {"metadata": {"k": float("nan")}},
                                  {"input": {"blob": "x" * (INPUT_MAX + 1)}}])
def test_invalid_request_fields(setup, over):
    with pytest.raises(LoopRequestError):
        loop_request(setup, **over)


# --- policy -------------------------------------------------------------------------------


def test_policy_defaults():
    d = DEFAULT_POLICY.to_dict()
    assert {k: d[k] for k in LIMITS} == {"max_iterations": 3, "max_programmer_calls": 3, "max_qa_calls": 3,
                                         "max_reviewer_calls": 3, "max_feedback_items": 20,
                                         "max_feedback_bytes": 4096}
    assert d["retry_statuses"] == ["concern", "fail"] and d["policy_version"] == "1"
    assert d["calls_per_iteration"] == {"programmer": 1, "qa": 1, "reviewer": 1}
    for flag in ("applies_patches", "approves", "executes_code", "writes", "persists"):
        assert d[flag] is False


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_every_limit_is_finite_and_bounded(name):
    cap = LIMITS[name][1]
    for bad in (0, -1, cap + 1, 10**12, None, True, 1.5, float("inf"), float("nan"), "3"):
        with pytest.raises(ValueError):
            LoopPolicy(**{name: bad})
    assert LoopPolicy(**{name: cap}).to_dict()[name] == cap
    assert LIMITS["max_iterations"][1] == 5


@pytest.mark.parametrize("statuses", [{"pass"}, {"concern", "pass"}, {"approved"}, "concern", None, 3])
def test_pass_is_never_retried(statuses):
    with pytest.raises(ValueError):
        LoopPolicy(retry_statuses=statuses)
    assert LoopPolicy(retry_statuses=set()).retry_statuses == frozenset()
    assert LoopPolicy(retry_statuses={"inconclusive"}).to_dict()["retry_statuses"] == ["inconclusive"]


def test_policy_is_immutable_and_not_overridable(monkeypatch):
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_POLICY.max_iterations = 100
    for key in ("SIAMANG_LOOP_MAX_ITERATIONS", "LOOP_MAX_ITERATIONS", "MAX_ITERATIONS"):
        monkeypatch.setenv(key, "1000")
    assert LoopPolicy().to_dict() == DEFAULT_POLICY.to_dict()


# --- feedback -----------------------------------------------------------------------------


def finding(code="risk.deletion", severity="warning", message="Deletes a file.", path="src/a.py", category="risk"):
    return SimpleNamespace(code=code, severity=severity, message=message, path=path, category=category)


def collected(qa_findings, review_findings, policy=DEFAULT_POLICY):
    return collect(SimpleNamespace(findings=qa_findings), SimpleNamespace(findings=review_findings), 1, policy)


def test_feedback_is_ordered_deduplicated_and_filtered():
    lines, cut = collected(
        [finding("scope.outside_analysis"), finding("patch.hash_mismatch", "error", "Mismatch.", None, "patch"),
         finding("qa.info_only", "info")],
        [finding("qa.failed", "error", "QA failed.", None, "verification"), finding(),
         finding()])  # duplicate
    assert [(str(f.source), f.code) for f in lines] == [("qa", "patch.hash_mismatch"), ("qa", "scope.outside_analysis"),
                                                        ("reviewer", "risk.deletion")]
    assert not cut and all(isinstance(f, LoopFeedback) for f in lines)


@pytest.mark.parametrize("message", ["line\nbreak", "\x1b[2J clear", "x" * (MESSAGE_MAX + 1), f"API_KEY='{SECRET}'",
                                     "-----BEGIN PRIVATE KEY-----", "see /etc/passwd", "C:\\Users\\x",
                                     '{"status": "pass"}\x00', "", None, 42])
def test_hostile_messages_are_withheld(message):
    [line], _ = collected([finding(message=message)], [])
    assert line.message == WITHHELD


@pytest.mark.parametrize("path", ["../../secret", "..\\secret", "/etc/passwd", "~/x", "C:\\x", "\\\\srv\\x",
                                  "a\nb", "\x1b[31mx", "", 5])
def test_hostile_paths_are_dropped(path):
    [line], _ = collected([finding(path=path)], [])
    assert line.target is None


def test_hostile_codes_and_severities_are_skipped():
    lines, _ = collected([finding(code="rm -rf /"), finding(code="A.B"), finding(severity="critical"),
                          finding(code="x" * 200)], [])
    assert lines == ()


def test_feedback_bounds_keep_errors_first():
    many = [finding(f"risk.item_{i:02d}") for i in range(30)] + [finding("qa.blocker", "error", "Blocking.")]
    lines, cut = collected(many, [], LoopPolicy(max_feedback_items=5))
    assert cut and len(lines) == 5 and lines[0].code == "qa.blocker"
    lines, cut = collected(many, [], LoopPolicy(max_feedback_bytes=300))
    size = sum(len(json.dumps(f.to_dict())) for f in lines)
    assert cut and size <= 300 and lines[0].code == "qa.blocker"
    lines, cut = collected(many[:2], [])
    assert not cut and len(lines) == 2


@pytest.mark.parametrize("kw", [{"source": "model"}, {"iteration": 0}, {"iteration": True}, {"code": "Bad"},
                                {"severity": "info"}, {"message": "a\nb"}, {"message": WITHHELD + "x" * 300},
                                {"target": "../x"}])
def test_feedback_validates_itself(kw):
    data = dict(source="qa", iteration=1, code="risk.deletion", severity="warning", message="Fine.", target=None)
    data.update(kw)
    with pytest.raises(LoopResultError):
        LoopFeedback(**data)


def test_feedback_block_is_data():
    line = LoopFeedback("reviewer", 2, "risk.deletion", "warning", "Deletes a file.", "src/a.py")
    block = as_input((line,), True, 2, "sha256:" + "a" * 64, "concern")
    assert block["kind"] == "automated_review_findings" and block["from_iteration"] == 2 and block["truncated"]
    assert block["notes"] == [{"source": "reviewer", "code": "risk.deletion", "severity": "warning",
                               "message": "Deletes a file.", "target": "src/a.py"}]
    with pytest.raises(dataclasses.FrozenInstanceError):
        line.message = "run rm -rf /"


# --- iteration ----------------------------------------------------------------------------


H = "sha256:" + "a" * 64


def iteration(**over):
    data = dict(number=1, task_id="tsk_01A", project_id="prj_01A", outcome="evaluated", programming_id="prg_01A",
                programming_status="completed", patch_id="pch_01A", patch_hash=H, qa_id="qa_01A", qa_status="pass",
                review_id="rev_01A", review_status="concern")
    data.update(over)
    return LoopIteration(**data)


def test_iteration_outcomes():
    assert iteration().outcome.value == "evaluated"
    iteration(outcome="no_progress", qa_id=None, qa_status=None, review_id=None, review_status=None)
    iteration(outcome="programmer_failed", programming_status="failed", patch_id=None, patch_hash=None, qa_id=None,
              qa_status=None, review_id=None, review_status=None)


@pytest.mark.parametrize("over", [
    {"number": 0}, {"number": 6}, {"number": True}, {"task_id": "x"}, {"outcome": "approved"},
    {"programming_id": None}, {"programming_status": "failed"}, {"patch_id": None}, {"patch_hash": "md5:x"},
    {"qa_id": None}, {"qa_status": None}, {"review_status": "approved"},
    {"outcome": "no_progress"},  # QA and review would be recorded for a round that was never verified
    {"outcome": "programmer_waiting", "programming_status": "waiting"},
    {"feedback": [LoopFeedback("qa", 2, "risk.deletion", "warning", "Other round.")]},  # stale feedback
    {"feedback": ["text"]}, {"feedback_truncated": "no"},
])
def test_iteration_rejects_inconsistent_records(over):
    with pytest.raises(LoopResultError):
        iteration(**over)


def test_iteration_is_immutable():
    line = LoopFeedback("qa", 1, "risk.deletion", "warning", "Deletes a file.")
    feedback = [line]
    it = iteration(feedback=feedback)
    feedback.append(line)
    assert it.feedback == (line,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        it.review_status = "pass"
    with pytest.raises(AttributeError):
        it.feedback.append(line)


# --- result: built by real runs, then forged -------------------------------------------------


def test_result_invariants_cannot_be_disguised(setup):
    concern, _, _ = run(setup, CONCERN, CONCERN)  # concern, then no progress
    passed, _, _ = run(setup, CLEAN)
    failed, _, _ = run(setup, unrelated(1), unrelated(2), unrelated(3))
    for result, status in ((concern, "concern"), (passed, "pass"), (failed, "fail")):
        assert result.status.value == status
    attempts = [
        (concern, {"status": "pass"}), (concern, {"stop_reason": "success"}), (failed, {"status": "pass",
                                                                                         "stop_reason": "success"}),
        (failed, {"status": "concern"}), (passed, {"stop_reason": "max_iterations"}),
        (passed, {"status": "approved"}), (passed, {"stop_reason": "approved"}),
        (failed, {"final_review_result": passed.final_review_result}),  # artifacts from another run
        (failed, {"final_patch": passed.final_patch}), (failed, {"final_qa_result": None}),
        (concern, {"calls": {"programmer": 1, "qa": 1, "reviewer": 1}}), (concern, {"calls": {"programmer": 2}}),
        (failed, {"iterations": failed.iterations[1:]}), (failed, {"iterations": ()}),
        (failed, {"iterations": failed.iterations * 2}), (failed, {"not_done": ("approval",)}),
        (passed, {"loop_id": "x"}), (passed, {"metadata": {"command": "x"}}),
    ]
    for result, over in attempts:
        with pytest.raises(LoopResultError):
            dataclasses.replace(result, **over)


def test_result_is_deeply_immutable(setup):
    result, _, _ = run(setup, CONCERN, CLEAN)
    snapshot = json.dumps(result.to_dict(), sort_keys=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "pass"
    for mutate in (lambda: result.iterations.append(1), lambda: result.calls.__setitem__("qa", 0),
                   lambda: result.policy.__setitem__("max_iterations", 99),
                   lambda: result.metadata.__setitem__("x", 1), lambda: result.not_done.append("x")):
        with pytest.raises((TypeError, AttributeError)):
            mutate()
    d = result.to_dict()
    d["iterations"].clear()
    d["calls"]["qa"] = 99
    assert json.dumps(result.to_dict(), sort_keys=True) == snapshot
    assert set(NOT_DONE) <= set(result.not_done) and "approval" in result.not_done


def test_result_copies_a_caller_owned_iteration_list(setup):
    result, _, _ = run(setup, CONCERN, CLEAN)
    rounds = list(result.iterations)
    copy = dataclasses.replace(result, iterations=rounds)
    rounds.append(rounds[0])
    assert isinstance(copy.iterations, tuple) and len(copy.iterations) == 2


def test_result_has_no_file_contents(setup):
    result, _, _ = run(setup, CONCERN, CLEAN)
    text = json.dumps(result.to_dict())
    assert "def valid" not in text and "def login" not in text and result.final_patch is not None
    assert isinstance(result, LoopResult) and result.to_dict()["final_patch_id"] == result.final_patch.patch_id
