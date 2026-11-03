"""HTTP contracts, real pipeline boundaries, recovery and adversarial authorization."""
import hashlib
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.settings import APISettings, Principal
from siamang.api.store import RunStore
from siamang.storage import Database
from siamang.projects import ProjectRegistry
from siamang.tasks import TaskEngine, TaskRequest
from tests.support.analyst import sample_project
from tests.support.loop import CLEAN, CONCERN, scripted
from tests.support.programmer import INSTRUCTION, make_model, reply, tree

TOKENS = {'operator': 'test-operator-credential-' + 'a' * 32,
          'approver': 'test-approver-credential-' + 'b' * 32,
          'reader': 'test-reader-credential-' + 'c' * 32,
          'outsider': 'test-outsider-credential-' + 'd' * 32}


def headers(who='operator'):
    return {'Authorization': 'Bearer ' + TOKENS[who]}


@pytest.fixture
def world(tmp_path):
    path = tmp_path / 'siamang.db'
    root = sample_project(tmp_path / 'project')
    with Database(path) as db:
        project = ProjectRegistry(db).register(name='Banuva', root_path=str(root))
    permissions = {'operator': {'read', 'submit', 'run', 'cancel', 'retry'},
                   'approver': {'read', 'approve'}, 'reader': {'read'}, 'outsider': {'read', 'approve'}}
    principals = tuple(Principal(name, hashlib.sha256(token.encode()).hexdigest(),
                                 frozenset(permissions[name]), frozenset({project.project_id})
                                 if name != 'outsider' else frozenset()) for name, token in TOKENS.items())
    settings = APISettings(path, principals, b'test-seal-material' * 3)
    return settings, project, root


def submit(client, project, request_id='req_httpone'):
    response = client.post('/v0/tasks', headers=headers(), json={'project_id': project.project_id,
                           'description': INSTRUCTION, 'request_id': request_id})
    assert response.status_code == 202, response.text
    assert response.json()['scheduled'] is False
    return response.json()['task']['task_id']


def wait(client, task_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get('/v0/tasks/' + task_id, headers=headers())
        assert response.status_code == 200, response.text
        if response.json()['execution']['state'] not in ('running', 'scheduled'):
            return response.json()
        time.sleep(.01)
    raise AssertionError('proposal worker did not finish')


@pytest.mark.parametrize('path', ['/v0/health', '/v0/capabilities', '/v0/projects', '/v0/openapi.json'])
@pytest.mark.parametrize('authorization', [None, 'Bearer invalid', 'Basic invalid'])
def test_all_read_routes_require_auth(world, path, authorization):
    with TestClient(create_app(world[0])) as client:
        response = client.get(path, headers={} if authorization is None else {'Authorization': authorization})
        assert response.status_code == 401
        assert response.headers['www-authenticate'] == 'Bearer'


def test_projects_task_and_idempotency_contract(world):
    settings, project, root = world
    with TestClient(create_app(settings)) as client:
        shown = client.get('/v0/projects', headers=headers()).json()
        assert shown['items'][0]['project_id'] == project.project_id
        assert 'root_path' not in shown['items'][0] and str(root) not in json.dumps(shown)
        task_id = submit(client, project)
        task_id2 = submit(client, project)
        assert task_id == task_id2
        snapshot = client.get('/v0/tasks/' + task_id, headers=headers()).json()
        assert snapshot['status'] == 'queued' and snapshot['execution']['state'] == 'unknown'
        assert snapshot['execution']['activity_known'] is False
        assert client.get('/v0/tasks', params={'project_id': project.project_id}, headers=headers()).status_code == 200
        events = client.get('/v0/tasks/' + task_id + '/events', headers=headers()).json()
        assert [e['type'] for e in events['items']] == ['task.accepted']
        assert client.get('/v0/tasks/' + task_id + '/events', params={'after_seq': events['next_after_seq']},
                          headers=headers()).json()['items'] == []
        assert client.get('/v0/tasks/' + task_id, headers=headers('outsider')).status_code == 404
        assert client.get('/v0/projects', headers=headers('outsider')).json()['items'] == []
        assert client.get('/v0/tasks/' + task_id).status_code == 401


@pytest.mark.parametrize('field', ['shell', 'command', 'source', 'agent_id', 'policy', 'seal_key', 'root_path'])
def test_submit_refuses_authority_execution_and_paths(world, field):
    with TestClient(create_app(world[0])) as client:
        response = client.post('/v0/tasks', headers=headers(), json={'project_id': world[1].project_id,
                               'description': INSTRUCTION, 'request_id': 'req_attack', field: 'untrusted'})
        assert response.status_code == 422 and 'untrusted' not in response.text


def test_permission_and_model_fail_closed(world):
    with TestClient(create_app(world[0])) as client:
        task_id = submit(client, world[1])
        assert client.post('/v0/tasks/' + task_id + '/run', headers=headers('reader')).status_code == 403
        response = client.post('/v0/tasks/' + task_id + '/run', headers=headers())
        assert response.status_code == 503 and response.json()['error']['code'] == 'model_not_configured'
        assert client.get('/v0/tasks/' + task_id, headers=headers()).json()['status'] == 'queued'
        assert client.get('/v0/capabilities', headers=headers()).json()['model_configured'] is False


def test_real_chain_stages_reports_explicit_approval_and_restart(world):
    settings, project, root = world
    before = tree(root)
    model = scripted(CLEAN)
    with TestClient(create_app(settings, models=model.router)) as client:
        task_id = submit(client, project)
        result = client.post('/v0/tasks/' + task_id + '/run', headers=headers())
        assert result.status_code == 202, result.text
        run_id = result.json()['run_id']
        snapshot = wait(client, task_id)
        assert snapshot['status'] == 'waiting', snapshot
        assert snapshot['execution']['phase'] == 'awaiting_approval', snapshot
        assert snapshot['execution']['wait_reason'] == 'approval_required'
        assert snapshot['execution']['activity_known'] is False
        phases = [e['phase'] for e in client.get('/v0/tasks/' + task_id + '/execution-events',
                                               headers=headers()).json()['items']]
        assert phases == ['queued', 'planning', 'analysis', 'programming', 'qa', 'reviewing', 'awaiting_approval']
        reports = client.get('/v0/tasks/' + task_id + '/reports', headers=headers())
        assert reports.status_code == 200, reports.text
        assert reports.json()['patch_applied'] is False
        assert reports.json()['items'][0]['loop']['status'] == 'pass'
        assert client.get('/v0/approvals', params={'project_id': project.project_id},
                          headers=headers()).json()['items'][0]['eligible'] is True
        assert client.post('/v0/approvals/' + run_id, headers=headers(), json={'decision': 'approve'}).status_code == 403
    # Durable, sealed evidence must still work after the worker process is recreated.
    with TestClient(create_app(settings)) as client:
        assert client.get('/v0/tasks/' + task_id, headers=headers()).json()['execution']['phase'] == 'awaiting_approval'
        response = client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                               json={'decision': 'approve', 'reason': 'Reviewed the static evidence.'})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'approved'
        duplicate = client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                                json={'decision': 'approve', 'reason': 'Reviewed the static evidence.'})
        assert duplicate.json() == response.json()
        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                           json={'decision': 'reject', 'reason': 'Changed my mind.'}).status_code == 409
        task = client.get('/v0/tasks/' + task_id, headers=headers()).json()
        assert task['status'] == 'completed' and task['result']['details']['patch_applied'] is False
    assert model.calls == 1 and tree(root) == before


def test_failure_evidence_cannot_be_approved_and_can_be_rejected(world):
    model = scripted(CONCERN, CONCERN)
    with TestClient(create_app(world[0], models=model.router)) as client:
        task_id = submit(client, world[1])
        run_id = client.post('/v0/tasks/' + task_id + '/run', headers=headers()).json()['run_id']
        task = wait(client, task_id)
        assert task['execution']['phase'] == 'blocked'
        assert task['execution']['wait_reason'] == 'quality_gate'
        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                           json={'decision': 'approve'}).status_code == 409
        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                           json={'decision': 'reject'}).status_code == 422
        response = client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                               json={'decision': 'reject', 'reason': 'Quality gate did not pass.'})
        assert response.status_code == 200, response.text
        assert client.get('/v0/tasks/' + task_id, headers=headers()).json()['status'] == 'cancelled'


def test_approval_rejects_tampered_evidence(world):
    with TestClient(create_app(world[0], models=scripted(CLEAN).router)) as client:
        task_id = submit(client, world[1])
        run_id = client.post('/v0/tasks/' + task_id + '/run', headers=headers()).json()['run_id']
        wait(client, task_id)
        with Database(world[0].database) as db:
            db.conn.execute("UPDATE api_runs SET bundle='{}' WHERE run_id=?", (run_id,))
        response = client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'approve'})
        assert response.status_code == 409 and response.json()['error']['code'] == 'evidence_invalid'


def test_restart_marks_active_worker_interrupted_without_success_or_retry(world):
    settings, project, _ = world
    with Database(settings.database) as db:
        engine = TaskEngine(db, ProjectRegistry(db))
        task = engine.create(TaskRequest(project.project_id, INSTRUCTION, 'test')).task
        engine.start(task.task_id)
        store = RunStore(db)
        store.initialize()
        run_id = store.create(task.task_id)
        store.update(run_id, state='running', phase='programming')
    with TestClient(create_app(settings)) as client:
        task = client.get('/v0/tasks/' + task.task_id, headers=headers()).json()
        assert task['status'] == 'waiting'
        assert task['execution']['state'] == 'interrupted'
        assert task['execution']['wait_reason'] == 'worker_interrupted'
        assert task['execution']['activity_known'] is False
        response = client.post('/v0/tasks/' + task['task_id'] + '/retry', headers=headers())
        assert response.status_code == 200 and response.json()['scheduled'] is False
        assert response.json()['task']['retry_count'] == 1


def test_active_stage_cancel_and_worker_busy(world):
    started, release = threading.Event(), threading.Event()
    def answer(model, request):
        started.set()
        assert release.wait(10)
        return CLEAN
    model = make_model(answer)
    before = tree(world[2])
    with TestClient(create_app(world[0], models=model.router)) as client:
        task_id = submit(client, world[1])
        assert client.post('/v0/tasks/' + task_id + '/run', headers=headers()).status_code == 202
        assert started.wait(5)
        try:
            task = client.get('/v0/tasks/' + task_id, headers=headers()).json()
            assert task['status'] == 'running' and task['execution']['phase'] == 'programming'
            assert task['execution']['activity_known'] is True
            other = submit(client, world[1], 'req_another')
            assert client.post('/v0/tasks/' + other + '/run', headers=headers()).status_code == 409
            assert client.post('/v0/tasks/' + task_id + '/cancel', headers=headers()).status_code == 200
        finally:
            release.set()
    assert tree(world[2]) == before
    with Database(world[0].database) as db:
        assert RunStore(db).latest(task_id)['bundle'] is None


def test_legacy_running_and_waiting_never_infer_a_stage(world):
    with Database(world[0].database) as db:
        engine = TaskEngine(db, ProjectRegistry(db))
        t = engine.create(TaskRequest(world[1].project_id, INSTRUCTION, 'test')).task
        engine.start(t.task_id)
    with TestClient(create_app(world[0])) as client:
        response = client.get('/v0/tasks/' + t.task_id, headers=headers()).json()
        assert response['status'] == 'running' and response['execution']['phase'] is None
        assert response['execution']['activity_known'] is False
        assert response['execution']['recovery_required'] is True


def test_programmer_failure_is_a_failed_task_with_a_durable_report(world):
    model = scripted('{invalid model output')
    with TestClient(create_app(world[0], models=model.router)) as client:
        task_id = submit(client, world[1])
        assert client.post('/v0/tasks/' + task_id + '/run', headers=headers()).status_code == 202
        task = wait(client, task_id)
        assert task['status'] == 'failed' and task['last_error']['code'] == 'PROGRAMMER_FAILED'
        assert task['execution']['phase'] == 'failed' and task['execution']['activity_known'] is False
        reports = client.get('/v0/tasks/' + task_id + '/reports', headers=headers()).json()
        assert reports['items'][0]['loop']['stop_reason'] == 'programmer_failed'
        assert '{invalid' not in json.dumps(reports)


def test_stale_heartbeat_is_not_activity():
    old = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    row = dict(run_id='req_stale', state='running', phase='programming', iteration=1, wait_reason=None,
               heartbeat_at=old, updated_at=old, created_at=old)
    assert RunStore.snapshot(row)['activity_known'] is False and RunStore.snapshot(row)['stale'] is True


def test_programmer_question_reaches_the_owner_and_nothing_is_approvable(world):
    model = scripted(reply([], status='waiting', unknowns=['Need more task detail.']))
    with TestClient(create_app(world[0], models=model.router)) as client:
        task_id = submit(client, world[1])
        assert client.post('/v0/tasks/' + task_id + '/run', headers=headers()).status_code == 202
        task = wait(client, task_id)
        assert task['execution']['wait_reason'] == 'clarification_required'
        asked = client.get('/v0/tasks/' + task_id + '/messages', headers=headers()).json()['items']
        assert asked[-1]['questions'][0]['question'] == 'Need more task detail.'
        assert client.get('/v0/tasks/' + task_id + '/reports', headers=headers()).json()['items'] == []
        assert client.get('/v0/approvals', params={'project_id': world[1].project_id},
                          headers=headers()).json()['items'] == []


def test_explicit_retry_creates_a_new_attempt_with_preserved_history(world):
    model = scripted(CONCERN, CONCERN, CLEAN)
    with TestClient(create_app(world[0], models=model.router)) as client:
        task_id = submit(client, world[1])
        old = client.post('/v0/tasks/' + task_id + '/run', headers=headers()).json()['run_id']
        assert wait(client, task_id)['execution']['wait_reason'] == 'quality_gate'
        assert client.post('/v0/tasks/' + task_id + '/retry', headers=headers()).status_code == 200
        new = client.post('/v0/tasks/' + task_id + '/run', headers=headers()).json()['run_id']
        assert old != new
        assert wait(client, task_id)['execution']['phase'] == 'awaiting_approval'
        assert len(client.get('/v0/tasks/' + task_id + '/reports', headers=headers()).json()['items']) == 2
        assert client.post('/v0/approvals/' + old, headers=headers('approver'),
                           json={'decision': 'approve'}).status_code == 409


def test_decision_integrity_and_project_scope(world):
    with TestClient(create_app(world[0], models=scripted(CLEAN).router)) as client:
        task_id = submit(client, world[1])
        run_id = client.post('/v0/tasks/' + task_id + '/run', headers=headers()).json()['run_id']
        wait(client, task_id)
        assert client.post('/v0/approvals/' + run_id, headers=headers('outsider'),
                           json={'decision': 'approve'}).status_code == 403
        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'),
                           json={'decision': 'approve'}).status_code == 200
        with Database(world[0].database) as db:
            db.conn.execute("UPDATE api_runs SET decision='{}' WHERE run_id=?", (run_id,))
        response = client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'approve'})
        assert response.status_code == 409 and response.json()['error']['code'] == 'decision_invalid'


def test_single_runtime_ownership(world):
    with TestClient(create_app(world[0])):
        with pytest.raises(RuntimeError, match='only one'):
            with TestClient(create_app(world[0])):
                pass


@pytest.mark.parametrize('extra', ['approver_id', 'authorized', 'policy', 'seal_key', 'force'])
def test_approval_caller_cannot_claim_authority(world, extra):
    with TestClient(create_app(world[0])) as client:
        response = client.post('/v0/approvals/req_test', headers=headers('approver'),
                               json={'decision': 'approve', extra: 'untrusted'})
        assert response.status_code == 422


def test_bounded_body_and_query_and_safe_errors(world):
    with TestClient(create_app(world[0]), raise_server_exceptions=False) as client:
        assert client.post('/v0/tasks', headers=headers(), content=b'x' * 65537).status_code == 413
        assert client.post('/v0/tasks', headers=headers(), content=b'{broken').status_code == 422
        response = client.get('/v0/tasks', params={'project_id': world[1].project_id, 'limit': -1}, headers=headers())
        assert response.status_code == 422 and 'input' not in response.text
        assert client.get('/v0/health', headers=headers()).headers['cache-control'] == 'no-store'
        assert client.get('/openapi.json').status_code == 404
        schema = client.get('/v0/openapi.json', headers=headers()).json()
        assert schema['components']['securitySchemes']['HTTPBearer']['scheme'] == 'bearer'
