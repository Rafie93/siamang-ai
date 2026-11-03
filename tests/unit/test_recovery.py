"""Phase 28: three bounded tiers, checkpoints that resume, compromises that reach the PR, and honest stops."""
import pytest

from siamang.recovery import (
    Advice,
    CheckpointStore,
    RecoveryEngine,
    RecoveryPolicy,
    Stage,
    StageOutcome,
    render_debt,
)


def ok(**output):
    return StageOutcome(True, output)


def fail(why="boom"):
    return StageOutcome(False, failure=why)


class Spy(Stage):
    def __init__(self, name, run, seen):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "run", run)
        object.__setattr__(self, "seen", seen)


def stage(name, *script):
    """A stage that answers from a script (an outcome, or a function of the context), recording its contexts."""
    queue, seen = list(script), []

    def run(context):
        seen.append(context)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item(context) if callable(item) else item
    return Spy(name, run, seen)


def test_tier_one_retries_with_the_failure_as_feedback():
    a = stage("a", fail("test_x failed"), ok(done=1))
    result = RecoveryEngine().run("r1", [a])
    assert result.ok and result.tiers_used == (1,) and not result.debt
    assert a.seen[1].feedback == "test_x failed" and a.seen[1].attempt == 2


def test_tier_two_changes_approach_and_the_compromise_becomes_debt():
    a = stage("a", fail(), fail(), lambda c: ok(via=c.approach) if c.approach else fail())
    advisor = lambda name, history: Advice("change_approach", "use a simpler parser", compromise="no streaming support")
    result = RecoveryEngine(advisor=advisor).run("r1", [a])
    assert result.ok and result.outputs["a"] == {"via": "use a simpler parser"} and result.tiers_used == (1, 2)
    assert [(d.stage, d.tier) for d in result.debt] == [("a", 2)]
    assert "no streaming support" in render_debt(result.debt) and render_debt([]) == ""


def test_tier_two_split_runs_every_part_and_fails_if_one_does():
    a = stage("a", fail(), fail(), lambda c: ok() if c.subtask != "b" else fail("b broke"))
    result = RecoveryEngine(advisor=lambda n, h: Advice("split", subtasks=("x", "b"))).run("r1", [a])
    assert not result.ok and "part 'b' failed" in result.reason


def test_tier_three_replans_only_what_remains():
    a, b = stage("a", ok(v=1)), stage("b", fail())
    c, d = stage("c", ok()), stage("d", ok())
    seen = {}

    def replan(remaining, failure):
        seen["remaining"], seen["failure"] = list(remaining), failure
        return [c, d]
    result = RecoveryEngine(replanner=replan).run("r1", [a, b, stage("z", ok())])
    assert result.ok and result.completed == ("a", "c", "d") and 3 in result.tiers_used
    assert seen == {"remaining": ["b", "z"], "failure": "boom"}
    assert any(d.tier == 3 for d in result.debt)


def test_it_stops_with_the_true_stage_and_reason_when_nothing_helps():
    a, b = stage("a", ok()), stage("b", fail("still red"))
    result = RecoveryEngine().run("r1", [a, b])
    assert not result.ok and result.stopped_at == "b" and result.reason == "still red" and result.completed == ("a",)


def test_replanning_is_bounded_and_cannot_loop():
    calls = []

    def replan(remaining, failure):
        calls.append(1)
        return [stage("again", fail())]
    result = RecoveryEngine(replanner=replan, policy=RecoveryPolicy(replans=1)).run("r1", [stage("a", fail())])
    assert not result.ok and len(calls) == 1


def test_the_attempt_budget_stops_the_run():
    result = RecoveryEngine(policy=RecoveryPolicy(retries=3, max_attempts=2)).run("r1", [stage("a", fail())])
    assert not result.ok and "budget" in result.reason and len(result.attempts) == 2


def test_an_exception_is_a_failure_that_does_not_leak_its_message():
    def run(context):
        raise RuntimeError("password=hunter2")
    result = RecoveryEngine(policy=RecoveryPolicy(retries=0)).run("r1", [Stage("a", run)])
    assert not result.ok and "hunter2" not in str(result.to_dict()) and "RuntimeError" in result.reason


def test_checkpoints_resume_without_rerunning_finished_stages(tmp_path):
    store = CheckpointStore(tmp_path)
    a1, b1 = stage("a", ok(n=1)), stage("b", fail())
    first = RecoveryEngine(checkpoints=store, policy=RecoveryPolicy(retries=0)).run("run-1", [a1, b1])
    assert not first.ok and store.load("run-1") == {"a": {"n": 1}}
    a2, b2 = stage("a", fail("must not run")), stage("b", ok())
    second = RecoveryEngine(checkpoints=store).run("run-1", [a2, b2])
    assert second.ok and second.resumed == ("a",) and not a2.seen and second.outputs["a"] == {"n": 1}
    assert store.load("run-1") == {}  # a finished run leaves no checkpoint


def test_a_damaged_checkpoint_is_ignored_and_run_ids_are_plain(tmp_path):
    store = CheckpointStore(tmp_path)
    (tmp_path / "r.json").write_text("{not json")
    assert store.load("r") == {}
    with pytest.raises(ValueError):
        store.load("../escape")


def test_stage_compromises_are_debt_and_names_must_be_unique():
    result = RecoveryEngine().run("r1", [Stage("a", lambda c: StageOutcome(True, {}, compromise="skipped e2e"))])
    assert [d.note for d in result.debt] == ["skipped e2e"]
    with pytest.raises(ValueError):
        RecoveryEngine().run("r1", [stage("a", ok()), stage("a", ok())])
