"""Phase 27 inside the Programmer: tool turns in the sandbox become the same validated patch as a one-shot reply."""
import json

import pytest

from siamang.api.tooling import SandboxToolRunner
from siamang.executor import ToolExecutor, ToolPolicy
from siamang.sandbox import RawRun, SandboxPolicy, SandboxService
from tests.support.programmer import AUTH, AUTH_TEXT, STEP, make_model, make_setup, reply, tree

FIXED = "def login(user):\n    return bool(user)\n"


class Engine:
    def __init__(self, *codes, up=True):
        self.codes, self.up, self.seen = list(codes), up, []

    def available(self):
        return self.up

    def run(self, work, argv, policy):
        self.seen.append((work / AUTH).read_text())
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        return RawRun(code, "1 passed" if code == 0 else "FAILED test_login")


def runner(engine, **policy):
    return SandboxToolRunner(ToolExecutor(SandboxService(engine, SandboxPolicy("img")), ToolPolicy(**policy)))


def script(*calls):
    queue = [c if isinstance(c, str) else json.dumps(c) for c in calls]
    return lambda model, request: queue.pop(0)


def write(path=AUTH, content=FIXED, **extra):
    return {"tool": "write_file", "path": path, "content": content, **extra}


RUN = {"tool": "run", "argv": ["pytest"]}


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


def programmer(setup, model, engine, **policy):
    return setup.programmer(model, strategy="programming.tools", tools=runner(engine, **policy))


def test_the_agent_fixes_what_it_observed_and_the_patch_is_validated(setup):
    engine = Engine(1, 0)
    model = make_model(script(RUN, write(step_id=STEP, reason="validate input"), RUN,
                              {"tool": "finish", "summary": "login validated"}))
    before = tree(setup.root)
    result = programmer(setup, model, engine).program(setup.request())
    assert result.status.value == "completed" and result.metadata["strategy"] == "programming.tools"
    change = result.changes[0]
    assert (change.path, change.step_id, change.reason, change.after_content) == (AUTH, STEP, "validate input", FIXED)
    assert change.before.value == "observed"  # the Programmer, not the agent, established the before-state
    assert result.metadata["sandbox_verified"] is True and result.metadata["sandbox_runs"] == 2
    assert result.metadata["model_calls"] == model.calls == 4
    assert engine.seen == [AUTH_TEXT, FIXED]  # the second command saw the edit
    assert tree(setup.root) == before and not any("not verified" in w for w in result.warnings)


def test_an_unverified_edit_is_said_so_and_an_unknown_step_falls_back_to_a_plan_step(setup):
    model = make_model(script(write(step_id="step_999"), {"tool": "finish", "summary": "done"}))
    result = programmer(setup, model, Engine(0)).program(setup.request())
    assert result.status.value == "completed" and result.changes[0].step_id in {st.step_id for st in setup.plan.steps}
    assert any("not verified" in w for w in result.warnings) and result.metadata["sandbox_verified"] is False


def test_secret_files_never_enter_the_patch(setup):
    model = make_model(script(write(".env", "A=1"), write(path="../x.py"), {"tool": "finish", "summary": "nothing"}))
    result = programmer(setup, model, Engine(0)).program(setup.request())
    assert result.status.value == "failed" and result.patch is None  # both writes were refused, no change remains


def test_without_a_container_engine_it_proposes_in_one_shot_and_says_so(setup):
    model = make_model(reply())
    result = programmer(setup, model, Engine(0, up=False)).program(setup.request())
    assert result.status.value == "completed" and model.calls == 1
    assert any("No sandbox" in w for w in result.warnings) and result.metadata["sandbox_mode"] == "unavailable"


def test_a_decision_only_the_owner_can_make_becomes_a_waiting_result(setup):
    model = make_model(script({"tool": "finish", "summary": "blocked", "questions": ["Pakai OAuth atau password?"]}))
    result = programmer(setup, model, Engine(0)).program(setup.request())
    assert result.status.value == "waiting" and "Pakai OAuth atau password?" in result.unknowns


def test_an_agent_that_never_finishes_is_bounded_by_its_turns(setup):
    model = make_model(lambda m, r: json.dumps({"tool": "list_files"}))
    result = programmer(setup, model, Engine(0), max_turns=3).program(setup.request())
    assert result.status.value == "failed" and model.calls == 3
    assert result.error["cause"] == "turn_limit"


def test_the_strategy_is_chosen_only_by_id_and_needs_no_runner_to_exist(setup):
    plain = setup.programmer(make_model(reply()), strategy="programming.tools")  # no runner: one-shot with a note
    assert plain.program(setup.request()).metadata["sandbox_mode"] == "unavailable"
    with pytest.raises(ValueError):
        setup.programmer(make_model(reply()), strategy="programming.shell")
