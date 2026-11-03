"""Phase 27-28 through the real runtime: tools in the sandbox, three recovery tiers, checkpoints, and debt in the PR."""
import contextlib
import json

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.tooling import SandboxToolRunner
from siamang.executor import ToolExecutor, ToolPolicy
from siamang.recovery import RecoveryPolicy
from siamang.sandbox import RawRun, SandboxPolicy, SandboxService
from siamang.verification import Check, VerificationService
from tests.api.test_http import headers
from tests.api.test_repos import TASK_READY, World, new_project, snapshot, task, until, wait_execution
from tests.support.programmer import make_model, reply
from tests.support.reviewer import clean_changes

VALIDATION = clean_changes()[0]


class Engine:
    """A container engine that answers each command with the next scripted exit code (the last one repeats)."""

    def __init__(self, *codes, up=True):
        self.codes, self.up, self.argvs = list(codes), up, []

    def available(self):
        return self.up

    def run(self, work, argv, policy):
        self.argvs.append(argv)
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        return RawRun(code, 'FAILED tests/test_a.py::test_login' if code else 'all passed')


class Scripted:
    """The Programmer's model: counts one-shot proposals and records the input each one was given."""

    def __init__(self):
        self.calls, self.inputs = 0, []

    def __call__(self, model, request):
        self.calls += 1
        self.inputs.append(json.loads(request.messages[-1].content.split('\n', 1)[1])['request']['input'])
        return reply([dict(c, content=c['content'] + f'# versi {self.calls}\n') for c in clean_changes()])


def verifier(engine):
    return VerificationService(SandboxService(engine, SandboxPolicy('img')), (Check('test', ('pytest',)),))


@pytest.fixture
def world(tmp_path):
    world = World(tmp_path)
    world.script = Scripted()
    world.programmer = make_model(world.script)
    world.advice = []

    def lead(model, request):
        if 'action' in request.json_schema['properties']:
            return json.dumps(world.advice.pop(0))
        return world._lead(model, request)
    world.lead = make_model(lead, structured=True)
    world.authors['lead'] = world.lead.router
    return world


def start(client, world, task_id):
    world.lead_replies.append(TASK_READY)
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    wait_execution(client, task_id, 'design_approval')
    assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                       json={'decision': 'approve'}).status_code == 200


def verdict(client, task_id):
    until(lambda: snapshot(client, task_id)['execution']['phase'] in ('awaiting_approval', 'blocked'), 'a verdict')
    return snapshot(client, task_id)


def report_of(client, task_id):
    from siamang.api.store import RunStore
    from siamang.storage.database import Database
    with Database(client.app.state.runtime.settings.database) as db:
        return json.loads(RunStore(db).latest(task_id)['report'])


def bundle_of(client, task_id):
    from siamang.api.store import RunStore
    from siamang.storage.database import Database
    runtime = client.app.state.runtime
    with Database(runtime.settings.database) as db:
        return runtime.load_bundle(RunStore(db).latest(task_id))[2]


@contextlib.contextmanager
def run_with(world, *, engine=None, recovery=None, tools=None):
    with TestClient(world.app()) as client:
        runtime = client.app.state.runtime
        runtime._verifier = verifier(engine) if engine else None
        runtime._recovery, runtime._tools = recovery, tools
        task_id = task(client, new_project(client, world))
        start(client, world, task_id)
        yield client, task_id, verdict(client, task_id)


# --- Phase 27: the Programmer works in the sandbox through the runtime -------------------------------


def test_the_programmer_edits_and_runs_in_the_sandbox_and_the_run_reaches_approval(tmp_path):
    world = World(tmp_path)
    prompts = []
    calls = iter([
        *({'tool': 'write_file', 'path': c['path'], 'content': c['content'], 'reason': 'validasi'}
          for c in clean_changes()),
        {'tool': 'run', 'argv': ['pytest']}, {'tool': 'finish', 'summary': 'validasi ditambahkan'}])

    def model(m, request):
        prompts.append(request.messages[-1].content)
        return json.dumps(next(calls))
    world.programmer = make_model(model)
    agent_engine = Engine(0)
    tools = SandboxToolRunner(ToolExecutor(SandboxService(agent_engine, SandboxPolicy('img')), ToolPolicy()))
    with run_with(world, engine=Engine(0), tools=tools) as (client, task_id, state):
        assert state['execution']['phase'] == 'awaiting_approval'
        patch = bundle_of(client, task_id).final_patch
        assert [c.path for c in patch.changes] == [c['path'] for c in clean_changes()]
        assert {c.step_id for c in patch.changes} == {'step_04'}  # an implementation step, chosen by the strategy
        assert agent_engine.argvs == [('pytest',)] and 'OBSERVATION 3' in prompts[-1]
        assert report_of(client, task_id)['verification']['passed']


# --- Phase 28: recovery tiers ------------------------------------------------------------------------


def test_tier_one_retries_with_the_failure_and_the_run_passes(world):
    engine = Engine(1, 0)
    with run_with(world, engine=engine, recovery=RecoveryPolicy(retries=1, advisor_rounds=0, replans=0)) as (client, task_id, state):
        assert state['execution']['phase'] == 'awaiting_approval' and world.script.calls == 2
        assert 'test_login' in json.dumps(world.script.inputs[-1]['verification_failure'])
        recovery = report_of(client, task_id)['recovery']
        assert recovery['status'] == 'completed' and recovery['tiers_used'] == [1] and not recovery['debt']
        assert not list((world.settings.database.parent / 'checkpoints').glob('*.json'))  # cleared once saved


def test_tier_two_advice_changes_the_approach_and_its_compromise_reaches_the_pull_request(world):
    world.advice.append({'action': 'change_approach', 'approach': 'validasi lewat fungsi murni', 'subtasks': [],
                         'compromise': 'tanpa pesan error per field'})
    with run_with(world, engine=Engine(1, 0), recovery=RecoveryPolicy(
        retries=0, advisor_rounds=1, replans=0)) as (client, task_id, state):
        assert state['execution']['phase'] == 'awaiting_approval'
        assert world.script.inputs[-1]['recovery_approach'] == 'validasi lewat fungsi murni'
        recovery = report_of(client, task_id)['recovery']
        assert recovery['tiers_used'] == [2] and recovery['debt'][0]['note'] == 'tanpa pesan error per field'
        run_id = state['execution']['run_id']
        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                           json={'decision': 'approve'}).status_code == 200
        until(lambda: snapshot(client, task_id)['branch']['publication'] in ('published', 'failed'), 'publication')
        assert snapshot(client, task_id)['branch']['publication'] == 'published', snapshot(client, task_id)['branch'].get('error')
        body = world.github.created[0]['body']
        assert '## Technical debt' in body and 'tanpa pesan error per field' in body


def test_tier_three_plans_the_remaining_work_again(world):
    with run_with(world, engine=Engine(1, 0), recovery=RecoveryPolicy(
        retries=0, advisor_rounds=0, replans=1)) as (client, task_id, state):
        assert state['execution']['phase'] == 'awaiting_approval'
        recovery = report_of(client, task_id)['recovery']
        assert 3 in recovery['tiers_used'] and recovery['completed'] == ['deliver_replanned']
        assert any(d['tier'] == 3 for d in recovery['debt'])


def test_when_every_tier_fails_the_run_stops_with_the_true_reason(world):
    world.advice.append({'action': 'give_up', 'approach': '', 'subtasks': [], 'compromise': ''})
    with run_with(world, engine=Engine(1), recovery=RecoveryPolicy(
        retries=1, advisor_rounds=1, replans=1, max_attempts=6)) as (client, task_id, state):
        assert state['execution']['phase'] == 'blocked' and state['execution']['wait_reason'] == 'quality_gate'
        recovery = report_of(client, task_id)['recovery']
        assert recovery['status'] == 'stopped' and recovery['stopped_at'] == 'deliver_replanned'
        assert 'test_login' in recovery['reason'] and recovery['tiers_used'] == [1, 2, 3]
        assert not report_of(client, task_id)['verification']['passed']


def test_without_recovery_the_old_verify_loop_is_unchanged(world):
    with run_with(world, engine=Engine(1, 0)) as (client, task_id, state):
        assert state['execution']['phase'] == 'awaiting_approval' and world.script.calls == 2
        assert 'recovery' not in report_of(client, task_id)


# --- Phase 28: checkpoints ---------------------------------------------------------------------------


def test_a_finished_delivery_is_not_redone_after_a_crash_before_the_report_was_saved(world, monkeypatch):
    from siamang.api.runtime import Runtime
    real, crashes = Runtime._sign, []

    def crash_once(self, *args):
        if not crashes:
            crashes.append(1)
            raise RuntimeError('crash before the report is saved')
        return real(self, *args)
    monkeypatch.setattr(Runtime, '_sign', crash_once)
    with TestClient(world.app()) as client:
        runtime = client.app.state.runtime
        runtime._verifier, runtime._recovery = verifier(Engine(0)), RecoveryPolicy()
        task_id = task(client, new_project(client, world))
        start(client, world, task_id)
        until(lambda: snapshot(client, task_id)['status'] == 'failed', 'the crash')
        assert world.script.calls == 1 and list((world.settings.database.parent / 'checkpoints').glob('*.json'))
        assert client.post(f'/v0/tasks/{task_id}/retry', headers=headers()).status_code == 200
        start(client, world, task_id)
        assert verdict(client, task_id)['execution']['phase'] == 'awaiting_approval'
        assert world.script.calls == 1  # the Programmer, QA, Reviewer, and checks were not run again
        assert report_of(client, task_id)['recovery']['resumed'] == ['deliver']


# --- configuration -----------------------------------------------------------------------------------


def test_the_operators_configuration_turns_both_on_and_off(tmp_path):
    from dataclasses import replace

    from siamang.api.runtime import Runtime
    base = World(tmp_path).settings

    def runtime(text):
        path = tmp_path / 'siamang.toml'
        path.write_text(text)
        return Runtime(replace(base, config=path))
    off = runtime('')
    assert off.tooling() is None and off.programmer_options() == {} and off.recovery() is None
    on = runtime('[sandbox]\nenabled = true\nimage = "python:3.12-slim"\ntools = true\ntool_turns = 5\n'
                 '[recovery]\nenabled = true\nretries = 2\nreplans = 0\n')
    options = on.programmer_options()
    assert options['strategy'] == 'programming.tools' and options['tools'].max_turns == 5
    assert (on.recovery().retries, on.recovery().replans) == (2, 0)
    from siamang.config import ConfigError, load_config
    with pytest.raises(ConfigError):
        path = tmp_path / 'bad.toml'
        path.write_text('[sandbox]\ntools = true\n')  # tools without the sandbox
        load_config(path, env={})
