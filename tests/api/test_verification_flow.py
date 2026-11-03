"""A run passes only when the sandbox ran its checks; a failing check returns to the Programmer as data."""
import json

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.sandbox import RawRun, SandboxPolicy, SandboxService
from siamang.verification import Check, VerificationService
from tests.api.test_http import headers
from tests.api.test_repos import TASK_READY, World, new_project, snapshot, task, until, wait_execution
from tests.support.programmer import make_model, reply
from tests.support.reviewer import clean_changes


class Engine:
    """Stands in for the container engine: one scripted exit code per check run."""

    def __init__(self, *codes, up=True):
        self.codes, self.up, self.workspaces = list(codes), up, []

    def available(self):
        return self.up

    def run(self, work, argv, policy):
        self.workspaces.append({p.relative_to(work).as_posix(): p.read_text() for p in work.rglob('*') if p.is_file()})
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        return RawRun(code, 'FAILED tests/test_a.py::test_login' if code else 'all passed')


class Scripted:
    def __init__(self):
        self.calls, self.inputs = 0, []

    def __call__(self, model, request):
        self.calls += 1
        self.inputs.append(json.loads(request.messages[-1].content.split('\n', 1)[1])['request']['input'])
        changes = clean_changes()
        if self.calls > 1:
            changes = [dict(c, content=c['content'] + f'# versi {self.calls}\n') for c in changes]
        return reply(changes)


@pytest.fixture
def world(tmp_path):
    world = World(tmp_path)
    world.script = Scripted()
    world.programmer = make_model(world.script)
    return world


def run_until(client, world, engine, checks=(Check('test', ('pytest',)),)):
    runtime = client.app.state.runtime
    runtime._verifier = VerificationService(SandboxService(engine, SandboxPolicy('img')), checks)
    project_id = new_project(client, world)
    task_id = task(client, project_id)
    world.lead_replies.append(TASK_READY)
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    wait_execution(client, task_id, 'design_approval')
    assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                       json={'decision': 'approve'}).status_code == 200
    until(lambda: snapshot(client, task_id)['execution']['phase'] in ('awaiting_approval', 'blocked'), 'a verdict')
    return task_id, snapshot(client, task_id)


def report_of(client, task_id):
    from siamang.api.store import RunStore
    from siamang.storage.database import Database
    with Database(client.app.state.runtime.settings.database) as db:
        return json.loads(RunStore(db).latest(task_id)['report'])


def test_passing_checks_reach_approval_with_the_evidence(world):
    engine = Engine(0)
    with TestClient(world.app()) as client:
        task_id, state = run_until(client, world, engine)
        assert state['execution']['phase'] == 'awaiting_approval'
        verification = report_of(client, task_id)['verification']
        assert verification['passed'] and verification['checks'][0]['exit_code'] == 0
        assert any(f.endswith('.html') or f.endswith('.py') for f in engine.workspaces[0])  # the patch was in the copy
        assert world.script.calls == 1


def test_a_failing_check_returns_to_the_programmer_then_passes(world):
    engine = Engine(1, 0)
    with TestClient(world.app()) as client:
        task_id, state = run_until(client, world, engine)
        assert state['execution']['phase'] == 'awaiting_approval' and world.script.calls == 2
        failure = world.script.inputs[-1]['verification_failure']
        assert failure['failed_checks'][0]['check'] == 'test' and 'test_login' in failure['failed_checks'][0]['output_tail']
        assert report_of(client, task_id)['verification']['passed']


def test_checks_that_keep_failing_block_the_run_at_the_limit(world):
    engine = Engine(1)
    with TestClient(world.app()) as client:
        task_id, state = run_until(client, world, engine)
        assert state['execution']['phase'] == 'blocked' and state['execution']['wait_reason'] == 'quality_gate'
        assert world.script.calls == 3  # the first try and two returns (verify_attempts)
        assert not report_of(client, task_id)['verification']['passed']


def test_without_an_engine_the_run_is_not_a_pass(world):
    with TestClient(world.app()) as client:
        task_id, state = run_until(client, world, Engine(0, up=False))
        assert state['execution']['phase'] == 'blocked' and world.script.calls == 1
        assert report_of(client, task_id)['verification']['status'] == 'unavailable'


def test_without_a_configured_command_the_run_is_not_a_pass(world):
    with TestClient(world.app()) as client:
        task_id, state = run_until(client, world, Engine(0), checks=())
        assert state['execution']['phase'] == 'blocked'
        assert report_of(client, task_id)['verification']['status'] == 'not_configured'
