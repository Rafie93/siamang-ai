"""LoopService.run: the bounded state machine, stop conditions, call budgets, feedback chain, identity, isolation."""

import dataclasses
import json

import pytest

from siamang.loop import LoopPolicy, LoopService
from siamang.loop.errors import LoopError, LoopErrorCode
from siamang.loop.feedback import FEEDBACK_KEY
from siamang.programmer import ProgrammingPolicy
from siamang.programmer.safety import thaw
from siamang.qa import QAPolicy, QAService
from siamang.reviewer import ReviewerPolicy, ReviewerService
from siamang.storage import Database
from siamang.tasks import TaskResult
from tests.support.analyst import write
from tests.support.loop import (
    CLEAN,
    CONCERN,
    CountingQA,
    CountingReviewer,
    RecordingProgrammer,
    cycling,
    loop_request,
    make_loop,
    run,
    scripted,
    unrelated,
)
from tests.support.programmer import AUTH, change, make_setup, reply, tree
from tests.support.qa import forge
from tests.support.reviewer import TEST_VALIDATION

C = LoopErrorCode
TESTS_ONLY = reply([change(TEST_VALIDATION, "create", "def test_valid():\n    assert True\n")])  # 1 change, review pass
WAITING = reply([], status="waiting", unknowns=["Which validation rules apply?"])


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def other(tmp_path):
    database = Database(tmp_path / "other.db")
    (tmp_path / "o").mkdir()
    yield make_setup(database, tmp_path / "o")
    database.close()


def shape(result):
    return [(i.outcome.value, None if i.review_status is None else i.review_status.value) for i in result.iterations]


def counts(service):
    return (len(service._programmer.requests), service._qa.calls, service._reviewer.calls)


def failing(setup, error, model, **kw):
    with pytest.raises(LoopError) as info:
        make_loop(setup, model, **kw).run(loop_request(setup))
    assert info.value.code is error
    return info.value


# --- one round ----------------------------------------------------------------------------------


def test_one_round_pass(setup):
    r, service, model = run(setup, CLEAN)
    assert (r.status.value, r.stop_reason.value, shape(r)) == ("pass", "success", [("evaluated", "pass")])
    assert counts(service) == (1, 1, 1) and model.calls == 1 and dict(r.calls) == {"programmer": 1, "qa": 1,
                                                                                    "reviewer": 1}
    assert r.final_patch.patch_id == r.iterations[0].patch_id and r.final_review_result.status.value == "pass"
    assert r.iterations[0].feedback == () and r.final_qa_result.status.value == "pass"


def test_one_round_concern_not_retried(setup):
    r, service, _ = run(setup, CONCERN, policy=LoopPolicy(retry_statuses={"fail"}))
    assert (r.status.value, r.stop_reason.value, counts(service)) == ("concern", "not_retryable", (1, 1, 1))


def test_one_round_fail_not_retried(setup):
    r, _, _ = run(setup, unrelated(1), policy=LoopPolicy(retry_statuses=set()))
    assert (r.status.value, r.stop_reason.value, len(r.iterations)) == ("fail", "not_retryable", 1)


def test_one_round_inconclusive_review(setup):
    reviewer = CountingReviewer(setup.world.engine, policy=ReviewerPolicy(max_findings=1))  # truncation -> inconclusive
    service = make_loop(setup, scripted(CONCERN, CLEAN), reviewer=reviewer)
    r = service.run(loop_request(setup))
    assert (r.status.value, r.stop_reason.value, shape(r)) == ("inconclusive", "inconclusive",
                                                               [("evaluated", "inconclusive")])
    assert counts(service) == (1, 1, 1)


def test_qa_inconclusive_never_passes(setup):
    write(setup.root, {AUTH: "x = 1\n" * 50_000})  # too large to observe: the proposal's before-state is unverified
    for policy, reasons in ((None, ("inconclusive", "not_retryable")),
                            (LoopPolicy(retry_statuses={"inconclusive"}), ("programmer_failed",))):
        programmer = RecordingProgrammer(setup.world.engine, models=scripted(reply([change()])).router,
                                         policy=ProgrammingPolicy(max_file_size=64 * 1024, max_context_file_size=1024))
        service = make_loop(setup, None, programmer=programmer, policy=policy)
        r = service.run(loop_request(setup))
        assert r.iterations[0].qa_status.value == "inconclusive" and r.status.value != "pass"
        assert r.status.value == r.final_review_result.status.value and r.stop_reason.value in reasons


# --- repair rounds --------------------------------------------------------------------------------


def test_repair_after_review_concern(setup):
    r, service, _ = run(setup, CONCERN, CLEAN)
    assert (r.status.value, r.stop_reason.value, shape(r)) == ("pass", "success", [("evaluated", "concern"),
                                                                                   ("evaluated", "pass")])
    assert counts(service) == (2, 2, 2) and r.final_patch.patch_id == r.iterations[1].patch_id
    assert r.iterations[0].feedback and r.iterations[1].feedback == ()


def test_repair_after_review_failure(setup):
    r, _, _ = run(setup, unrelated(1), CLEAN)
    assert shape(r) == [("evaluated", "fail"), ("evaluated", "pass")] and r.status.value == "pass"
    assert any(f.code == "alignment.unrelated_patch" and f.severity == "error" for f in r.iterations[0].feedback)


def test_repair_after_qa_failure(setup):
    qa = CountingQA(setup.world.engine, policy=QAPolicy(max_changes=1))  # a 2-change patch fails QA
    service = make_loop(setup, scripted(CLEAN, TESTS_ONLY), qa=qa)
    r = service.run(loop_request(setup))
    assert [i.qa_status.value for i in r.iterations] == ["fail", "pass"]
    assert shape(r) == [("evaluated", "fail"), ("evaluated", "pass")] and r.status.value == "pass"
    assert {(str(f.source), f.code) for f in r.iterations[0].feedback} >= {("qa", "policy.too_many_changes")}


def test_multiple_rounds_then_pass(setup):
    r, service, _ = run(setup, unrelated(1), CONCERN, CLEAN)
    assert shape(r) == [("evaluated", "fail"), ("evaluated", "concern"), ("evaluated", "pass")]
    assert (r.status.value, r.stop_reason.value, counts(service)) == ("pass", "success", (3, 3, 3))


def test_feedback_reaches_exactly_the_next_round(setup):
    r, service, _ = run(setup, unrelated(1), unrelated(2), unrelated(3))
    first, second, third = service._programmer.requests
    assert FEEDBACK_KEY not in first.input
    for request, previous in ((second, r.iterations[0]), (third, r.iterations[1])):
        block = thaw(request.input[FEEDBACK_KEY])
        assert block["from_iteration"] == previous.number and block["previous_patch_hash"] == previous.patch_hash
        assert block["previous_review_status"] == previous.review_status.value
        assert block["notes"] == [{k: v for k, v in f.to_dict().items() if k != "iteration"}
                                  for f in previous.feedback]
        assert block["kind"] == "automated_review_findings" and block["truncated"] is previous.feedback_truncated
    assert r.iterations[2].feedback == ()  # the last round hands nothing to anyone


def test_caller_input_is_kept_next_to_feedback(setup):
    r, service, _ = run(setup, CONCERN, CLEAN, input={"ticket": "L-7"}, constraints=("Keep it small",))
    for request in service._programmer.requests:
        assert request.input["ticket"] == "L-7" and request.constraints == ("Keep it small",)
        assert request.plan is setup.plan and request.analysis is setup.analysis


def test_feedback_bounds_in_the_chain(setup):
    r, service, _ = run(setup, unrelated(1), CLEAN, policy=LoopPolicy(max_feedback_items=1))
    assert len(r.iterations[0].feedback) == 1 and r.iterations[0].feedback_truncated
    block = thaw(service._programmer.requests[1].input[FEEDBACK_KEY])
    assert len(block["notes"]) == 1 and block["truncated"] is True and block["notes"][0]["severity"] == "error"
    r, service, _ = run(setup, unrelated(1), CLEAN, policy=LoopPolicy(max_feedback_bytes=64))
    assert r.iterations[0].feedback == () and r.iterations[0].feedback_truncated


# --- stop conditions --------------------------------------------------------------------------


def test_no_progress(setup):
    r, service, model = run(setup, CONCERN, CONCERN)  # the Programmer repeats the same content
    assert (r.status.value, r.stop_reason.value) == ("concern", "no_progress")
    assert shape(r) == [("evaluated", "concern"), ("no_progress", None)]
    assert r.iterations[0].patch_hash == r.iterations[1].patch_hash
    assert r.iterations[0].patch_id != r.iterations[1].patch_id  # identity differs; content does not
    assert counts(service) == (2, 1, 1) and model.calls == 2  # never verified or reviewed again, no third round
    assert r.final_patch.patch_id == r.iterations[0].patch_id


def test_no_progress_detects_an_earlier_round_too(setup):
    r, _, _ = run(setup, CONCERN, unrelated(1), CONCERN, policy=LoopPolicy(max_iterations=5, max_programmer_calls=5,
                                                                            max_qa_calls=5, max_reviewer_calls=5))
    assert r.stop_reason.value == "no_progress" and len(r.iterations) == 3 and r.status.value == "fail"


def test_max_iterations_with_always_failing_rounds(setup):
    model = cycling(unrelated)  # a different failing patch every time, forever
    service = make_loop(setup, model)
    r = service.run(loop_request(setup))
    assert (r.status.value, r.stop_reason.value) == ("fail", "max_iterations")
    assert counts(service) == (3, 3, 3) and model.calls == 3 and len(r.iterations) == 3


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_iteration_limit_is_exact(setup, limit):
    policy = LoopPolicy(max_iterations=limit, max_programmer_calls=5, max_qa_calls=5, max_reviewer_calls=5)
    model = cycling(unrelated)
    service = make_loop(setup, model, policy=policy)
    r = service.run(loop_request(setup))
    assert len(r.iterations) == limit == model.calls and r.stop_reason.value == "max_iterations"


@pytest.mark.parametrize("budget", ["max_programmer_calls", "max_qa_calls", "max_reviewer_calls"])
def test_call_budgets(setup, budget):
    model = cycling(unrelated)
    service = make_loop(setup, model, policy=LoopPolicy(max_iterations=5, **{budget: 2}))
    r = service.run(loop_request(setup))
    assert r.stop_reason.value == "call_budget" and len(r.iterations) == 2 and model.calls == 2
    assert counts(service) == (2, 2, 2) and r.status.value == "fail"


def test_programmer_failure_and_waiting(setup):
    r, service, _ = run(setup, "not json at all")
    assert (r.status.value, r.stop_reason.value, shape(r)) == ("inconclusive", "programmer_failed",
                                                               [("programmer_failed", None)])
    assert counts(service) == (1, 0, 0) and r.final_patch is None and r.final_review_result is None
    r, _, _ = run(setup, WAITING)
    assert (r.status.value, r.stop_reason.value) == ("inconclusive", "programmer_waiting")


def test_programmer_failure_after_a_round_keeps_the_last_evidence(setup):
    r, _, _ = run(setup, CONCERN, "garbage")
    assert (r.status.value, r.stop_reason.value) == ("concern", "programmer_failed")
    assert shape(r) == [("evaluated", "concern"), ("programmer_failed", None)]
    assert r.final_patch.patch_id == r.iterations[0].patch_id


def test_last_evidence_decides_even_after_a_better_round(setup):
    r, _, _ = run(setup, CONCERN, unrelated(1), policy=LoopPolicy(max_iterations=2))
    assert shape(r) == [("evaluated", "concern"), ("evaluated", "fail")]
    assert (r.status.value, r.stop_reason.value) == ("fail", "max_iterations")  # never the earlier, better status


# --- input refusals ------------------------------------------------------------------------------


def test_input_refusals(setup, other):
    model = scripted(CLEAN)
    service = make_loop(setup, model)
    for over, code in (({"task_id": "tsk_01UNKNOWN"}, C.TASK_NOT_FOUND),
                       ({"project_id": other.world.project.project_id}, C.REQUEST_INVALID),
                       ({"plan": other.plan}, C.REQUEST_INVALID), ({"analysis": other.analysis}, C.REQUEST_INVALID)):
        with pytest.raises(LoopError) as info:
            service.run(loop_request(setup, **over))
        assert info.value.code is code
    with pytest.raises(LoopError) as info:
        service.run(loop_request(setup).to_dict())
    assert info.value.code is C.REQUEST_INVALID and model.calls == 0


def test_component_refusal_stops_the_run(db, tmp_path):
    s = make_setup(db, tmp_path, start=True)
    s.world.engine.complete(s.world.task.task_id, TaskResult(summary="done"))
    err = failing(s, C.COMPONENT_REFUSED, scripted(CLEAN))
    assert err.cause == "programmer_policy_denied" and "iteration_1" in err.details


def test_reviewer_refusal_stops_the_run(setup):
    reviewer = CountingReviewer(setup.world.engine, policy=ReviewerPolicy(max_changes=1))
    err = failing(setup, C.COMPONENT_REFUSED, scripted(CLEAN), reviewer=reviewer)
    assert err.cause == "reviewer_policy_denied" and err.details == ("reviewer", "iteration_1")


def test_constructor_guards(setup):
    engine = setup.world.engine
    p, q, r = RecordingProgrammer(engine), QAService(engine), ReviewerService(engine)
    for args in ((object(), p, q, r), (engine, object(), q, r), (engine, p, object(), r), (engine, p, q, object()),
                 (engine, q, p, r)):
        with pytest.raises(TypeError):
            LoopService(*args)
    with pytest.raises(TypeError):
        LoopService(engine, p, q, r, policy={"max_iterations": 1000})


# --- the artifact chain (faulty components) ----------------------------------------------------


class StaleQA(CountingQA):
    """Round 2 gets round 1's QA result: QA(N) attached to Patch(N+1)."""

    def verify(self, request):
        result = super().verify(request)
        self.first = getattr(self, "first", result)
        return self.first


class StaleReviewer(CountingReviewer):
    """Round 2 gets round 1's review: Review(N) attached to QA(N+1)."""

    def review(self, request):
        result = super().review(request)
        self.first = getattr(self, "first", result)
        return self.first


class ForgingQA(CountingQA):
    def __init__(self, *a, fields, **k):
        super().__init__(*a, **k)
        self.fields = fields

    def verify(self, request):
        return forge(super().verify(request), **self.fields)


class ForgingReviewer(CountingReviewer):
    def __init__(self, *a, fields, **k):
        super().__init__(*a, **k)
        self.fields = fields

    def review(self, request):
        return forge(super().review(request), **self.fields)


class ReplayingProgrammer(RecordingProgrammer):
    """Returns its first result again: a reused programming ID and patch ID."""

    def program(self, request):
        result = super().program(request)
        self.first = getattr(self, "first", result)
        return self.first


def test_stale_qa_is_refused(setup):
    err = failing(setup, C.CHAIN_BROKEN, scripted(CONCERN, CLEAN), qa=StaleQA(setup.world.engine))
    assert err.details == ("iteration_2",)


class ReusedIdQA(CountingQA):
    """Correct for the new patch in every way except that it reuses round 1's QA identity."""

    def verify(self, request):
        result = super().verify(request)
        self.first_id = getattr(self, "first_id", result.qa_id)
        return dataclasses.replace(result, qa_id=self.first_id)


def test_reused_qa_identity_is_refused(setup):
    err = failing(setup, C.CHAIN_BROKEN, scripted(CONCERN, CLEAN), qa=ReusedIdQA(setup.world.engine))
    assert err.details == ("iteration_2",)


def test_stale_review_is_refused(setup):
    err = failing(setup, C.CHAIN_BROKEN, scripted(CONCERN, CLEAN), reviewer=StaleReviewer(setup.world.engine))
    assert err.details == ("iteration_2",)


def test_replayed_programming_result_is_refused(setup):
    programmer = ReplayingProgrammer(setup.world.engine, models=scripted(CONCERN, CLEAN).router)
    err = failing(setup, C.CHAIN_BROKEN, None, programmer=programmer)
    assert err.details == ("iteration_2",)


@pytest.mark.parametrize("fields", [{"patch_id": "pch_01OTHER"}, {"patch_hash": "sha256:" + "0" * 64},
                                    {"qa_id": "qa_../x"}, {"status": "pass", "findings": ()},
                                    {"metadata": {"strategy": "qa.static"}}])
def test_qa_identity_mismatch(setup, fields):
    engine = setup.world.engine
    failing(setup, C.CHAIN_BROKEN, scripted(unrelated(1)), qa=ForgingQA(engine, fields=fields,
                                                                       policy=QAPolicy(max_changes=1)))


@pytest.mark.parametrize("fields", [{"patch_id": "pch_01OTHER"}, {"patch_hash": "sha256:" + "0" * 64},
                                    {"qa_id": "qa_01OTHER"}, {"qa_hash": "sha256:" + "1" * 64},
                                    {"qa_status": "pass"}, {"plan_id": "pln_01OTHER"},
                                    {"analysis_id": "ana_01OTHER"}, {"status": "pass"},
                                    {"status": "pass", "findings": ()}, {"metadata": {"a": [1]}}])
def test_review_identity_mismatch(setup, fields):
    failing(setup, C.CHAIN_BROKEN, scripted(CONCERN), reviewer=ForgingReviewer(setup.world.engine, fields=fields))


def test_task_and_project_mismatch_from_components(setup, other):
    foreign_patch = other.programmer(scripted(CLEAN)).program(other.request()).patch

    class ForeignProgrammer(RecordingProgrammer):
        def program(self, request):
            result = super().program(request)
            return dataclasses.replace(result, task_id=other.world.task.task_id,
                                       project_id=other.world.project.project_id, patch=foreign_patch)

    failing(setup, C.CHAIN_BROKEN, None, programmer=ForeignProgrammer(setup.world.engine,
                                                                      models=scripted(CLEAN).router))
    for fields in ({"task_id": other.world.task.task_id}, {"project_id": other.world.project.project_id}):
        failing(setup, C.CHAIN_BROKEN, scripted(CONCERN), reviewer=ForgingReviewer(setup.world.engine, fields=fields))


def test_unexpected_component_error_is_contained(setup):
    class Exploding(CountingQA):
        def verify(self, request):
            raise RuntimeError("/Users/someone/.ssh/id_rsa sk-SECRET-DO-NOT-LEAK-31337")

    err = failing(setup, C.INTERNAL, scripted(CLEAN), qa=Exploding(setup.world.engine))
    text = json.dumps(err.to_dict())
    assert err.cause == "RuntimeError" and "/Users/someone" not in text and "SECRET" not in text


# --- determinism, isolation, immutability ----------------------------------------------------


def test_deterministic(setup):
    a, _, _ = run(setup, unrelated(1), CONCERN, CLEAN)
    b, _, _ = run(setup, unrelated(1), CONCERN, CLEAN)
    assert a.loop_id != b.loop_id and a.content_hash == b.content_hash and a.content() == b.content()
    assert [i.patch_id for i in a.iterations] != [i.patch_id for i in b.iterations]


def test_runs_are_isolated(setup):
    model = cycling(lambda n: (CONCERN, CLEAN, unrelated(9), unrelated(10), unrelated(11), unrelated(12))[n - 1]
                    if n <= 6 else unrelated(n))
    service = make_loop(setup, model)
    state = dict(vars(service))
    first = service.run(loop_request(setup))
    second = service.run(loop_request(setup))
    assert dict(vars(service)) == state  # no run state is kept on the service
    assert shape(first) == [("evaluated", "concern"), ("evaluated", "pass")]
    assert shape(second) == [("evaluated", "fail")] * 3 and second.stop_reason.value == "max_iterations"
    assert {i.patch_id for i in first.iterations}.isdisjoint({i.patch_id for i in second.iterations})


def test_a_failed_run_does_not_affect_the_next(setup):
    class FlakyQA(CountingQA):
        def verify(self, request):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("boom")
            return super().verify(request)

    service = make_loop(setup, cycling(lambda n: CLEAN), qa=FlakyQA(setup.world.engine))
    with pytest.raises(LoopError):
        service.run(loop_request(setup))
    r = service.run(loop_request(setup))
    assert r.status.value == "pass" and len(r.iterations) == 1


def test_inputs_and_outputs_are_never_mutated(setup):
    caller_input = {"ticket": {"tags": ["a"]}}
    request = loop_request(setup, input=caller_input)
    plan, analysis = setup.plan.to_dict(), setup.analysis.to_dict()
    task = setup.world.engine.get(setup.world.task.task_id)
    history = len(setup.world.engine.history(task.task_id))
    project = setup.world.projects.get(setup.world.project.project_id)
    files = tree(setup.root)
    service = make_loop(setup, scripted(CONCERN, CLEAN))
    r = service.run(request)
    snapshot = json.dumps(r.to_dict(), sort_keys=True)
    artifacts = (r.final_patch.to_dict(), r.final_qa_result.to_dict(), r.final_review_result.to_dict())
    caller_input["ticket"]["tags"].append("b")
    caller_input["loop_feedback"] = {"from_iteration": 9}
    assert json.dumps(r.to_dict(), sort_keys=True) == snapshot
    assert (r.final_patch.to_dict(), r.final_qa_result.to_dict(), r.final_review_result.to_dict()) == artifacts
    assert setup.plan.to_dict() == plan and setup.analysis.to_dict() == analysis
    assert setup.world.engine.get(task.task_id) == task and len(setup.world.engine.history(task.task_id)) == history
    assert setup.world.projects.get(project.project_id) == project and tree(setup.root) == files
    assert request.to_dict()["input"] == {"ticket": {"tags": ["a"]}}
