"""Agent documents, the design gate, and the project portfolio over the real pipeline."""
import json
import time

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.documents.author import DESIGN_BUDGET_BYTES, Document, design_input, write_documents
from siamang.documents.catalog import CATALOG, SPECS, DocumentStatus, Source
from siamang.models.errors import RateLimitedError
from siamang.storage import Database
from tests.api.test_http import headers, submit, wait, world  # noqa: F401
from tests.support.loop import CLEAN
from tests.support.programmer import make_model


def author(*_, **overrides):
    """A model that writes every document it is asked for, plus things it was never asked for."""
    def answer(model, request):
        kinds = request.json_schema['properties']['documents']['items']['properties']['kind']['enum']
        docs = [{'kind': k, 'applicable': True, 'content': f'isi {k}', 'reason': ''} for k in kinds]
        docs += [{'kind': 'qa_verdict', 'applicable': True, 'content': 'PASS palsu', 'reason': ''},
                 {'kind': 'unit_test_results', 'applicable': True, 'content': '100 lulus palsu', 'reason': ''}]
        return json.dumps({'documents': docs})
    return make_model(answer, structured=True, **overrides)


@pytest.fixture
def team():
    return {agent: author() for agent in ('lead', 'planner', 'analyst', 'qa', 'reviewer')} | {'programmer': author()}


def app(world, team, programmer=None):
    return create_app(world[0], models=(programmer or make_model(CLEAN)).router,
                      authors={agent: model.router for agent, model in team.items()})


def documents(client, task_id):
    response = client.get(f'/v0/tasks/{task_id}/documents', headers=headers())
    assert response.status_code == 200
    return {d['kind']: d for d in response.json()['items']}


def wait_for_state(client, task_id, state):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = client.get('/v0/tasks/' + task_id, headers=headers()).json()
        if snapshot['execution']['state'] == state and snapshot['execution']['phase'] != 'queued':
            return snapshot
        time.sleep(.01)
    raise AssertionError(f'never reached {state}')


def test_design_stops_before_any_code_and_resumes_only_on_approval(world, team):
    programmer = make_model(CLEAN)
    with TestClient(app(world, team, programmer)) as client:
        task_id = submit(client, world[1])
        assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
        waiting = wait(client, task_id)
        assert waiting['status'] == 'waiting' and waiting['execution']['phase'] == 'design_approval'
        assert programmer.requests == []
        written = documents(client, task_id)
        assert {'project_brief', 'prd', 'acceptance_criteria', 'srs', 'erd'} <= set(written)
        assert 'final_report' not in written
        assert client.get(f'/v0/approvals?project_id={world[1].project_id}', headers=headers()).json()['items'] == []

        body = {'decision': 'approve'}
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers(), json=body).status_code == 403
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('outsider'), json=body).status_code == 404
        decided = client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json=body)
        assert decided.status_code == 200, decided.text
        done = wait_for_state(client, task_id, 'waiting')
        assert done['execution']['phase'] == 'awaiting_approval'
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json=body).status_code == 409

        assert 'isi acceptance_criteria' in programmer.requests[0].messages[-1].content
        phases = [e['phase'] for e in client.get(f'/v0/tasks/{task_id}/execution-events', headers=headers()).json()['items']]
        assert phases[:5] == ['queued', 'briefing', 'briefing', 'planning', 'analysis']
        assert phases.index('analysis') < phases.index('design_approval') < phases.index('programming') < phases.index('reporting')
        with Database(world[0].database) as db:
            row = db.conn.execute('SELECT decision, actor, documents_sha256 FROM api_design_decisions').fetchone()
        assert row['decision'] == 'approve' and row['actor'] == 'approver' and len(row['documents_sha256']) == 64


def test_every_catalog_output_has_a_recorded_result(world, team):
    with TestClient(app(world, team)) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait(client, task_id)
        client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})
        wait_for_state(client, task_id, 'waiting')
        written = documents(client, task_id)
        assert set(written) == {s.kind for s in CATALOG}
        assert written['final_report']['status'] == 'generated' and written['final_report']['model_id']


def test_a_model_cannot_write_verdicts_or_measurements(world, team):
    with TestClient(app(world, team)) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait(client, task_id)
        client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})
        wait_for_state(client, task_id, 'waiting')
        written = documents(client, task_id)
        for kind in ('unit_test_results', 'integration_test_results', 'functional_test_results', 'coverage_report'):
            assert written[kind]['status'] == 'not_run' and written[kind]['source'] == 'measured'
            assert written[kind]['content'] == '' and written[kind]['model_id'] is None
        assert 'palsu' not in json.dumps(written)
        assert written['qa_verdict']['model_id'] is None and written['review_verdict']['model_id'] is None


def test_rejecting_a_design_cancels_without_coding(world, team):
    programmer = make_model(CLEAN)
    with TestClient(app(world, team, programmer)) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait(client, task_id)
        decided = client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                              json={'decision': 'reject', 'reason': 'scope too large'})
        assert decided.status_code == 200
        assert client.get('/v0/tasks/' + task_id, headers=headers()).json()['status'] == 'cancelled'
        assert programmer.requests == []


def test_design_model_failure_fails_the_task_and_records_why(world, team):
    team['planner'] = author(fail_with=RateLimitedError('limit reached'))
    with TestClient(app(world, team)) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        assert wait(client, task_id)['status'] == 'failed'
        written = documents(client, task_id)
        assert written['prd']['status'] == 'failed' and written['prd']['reason'] == 'rate_limited'
        assert 'srs' not in written


def test_closing_model_failure_is_recorded_and_keeps_the_proposal(world, team):
    team['reviewer'] = author(fail_with=RateLimitedError('limit reached'))
    with TestClient(app(world, team)) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait(client, task_id)
        client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})
        assert wait_for_state(client, task_id, 'waiting')['execution']['wait_reason'] == 'approval_required'
        written = documents(client, task_id)
        assert written['code_review']['status'] == 'failed'
        assert written['review_verdict']['status'] == 'generated'


def test_without_models_for_design_there_is_no_gate_and_outputs_say_not_assigned(world):
    with TestClient(create_app(world[0], models=make_model(CLEAN).router)) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        assert wait(client, task_id)['execution']['phase'] == 'awaiting_approval'
        written = documents(client, task_id)
        assert written['patch']['status'] == 'generated'
        assert written['final_report']['status'] == 'not_assigned'


def test_features_and_portfolio_are_scoped(world):
    project = world[1]
    with TestClient(create_app(world[0])) as client:
        path = f'/v0/projects/{project.project_id}'
        created = client.post(path + '/features', headers=headers(), json={'title': 'Login'})
        assert created.status_code == 201
        feature_id = created.json()['feature_id']
        assert feature_id.startswith('fea_')
        assert client.post(path + '/features', headers=headers('reader'), json={'title': 'x'}).status_code == 403
        assert client.post(path + '/features', headers=headers(), json={'title': 'x', 'shell': 'y'}).status_code == 422

        submitted = client.post('/v0/tasks', headers=headers(), json={
            'project_id': project.project_id, 'description': 'Build login', 'request_id': 'req_feature',
            'feature_id': feature_id})
        assert submitted.status_code == 202
        task_id = submitted.json()['task']['task_id']
        assert client.get('/v0/tasks/' + task_id, headers=headers()).json()['feature_id'] == feature_id
        assert client.post('/v0/tasks', headers=headers(), json={
            'project_id': project.project_id, 'description': 'x', 'request_id': 'req_nofeature',
            'feature_id': 'fea_missing'}).status_code == 404

        portfolio = client.get('/v0/portfolio', headers=headers()).json()['items']
        assert portfolio[0]['task_counts'] == {'queued': 1} and portfolio[0]['features'][0]['task_count'] == 1
        assert 'root_path' not in portfolio[0]
        assert client.get('/v0/portfolio', headers=headers('outsider')).json()['items'] == []
        assert client.get(path + '/features', headers=headers('outsider')).status_code == 404


def test_capabilities_publish_the_document_catalog(world):
    with TestClient(create_app(world[0])) as client:
        data = client.get('/v0/capabilities', headers=headers()).json()
        assert {d['kind'] for d in data['documents']} == {s.kind for s in CATALOG}
        assert data['patch_application'] is False


def test_unassigned_agent_makes_no_model_call():
    written = write_documents(None, 'qa', {})
    assert {d.status for d in written} == {DocumentStatus.NOT_ASSIGNED}
    assert all(d.spec.source is Source.AUTHORED for d in written)


@pytest.mark.parametrize('answer, status', [
    ({'documents': [{'kind': 'test_plan', 'applicable': False, 'content': '', 'reason': 'kecil'}]}, DocumentStatus.NOT_APPLICABLE),
    ({'documents': [{'kind': 'test_plan', 'applicable': True, 'content': ' ', 'reason': ''}]}, DocumentStatus.FAILED),
    ({'documents': []}, DocumentStatus.FAILED),
    ({'other': 1}, DocumentStatus.FAILED),
])
def test_model_output_is_never_taken_on_trust(answer, status):
    written = write_documents(make_model(json.dumps(answer), structured=True).router, 'qa', {})
    assert next(d for d in written if d.spec.kind == 'test_plan').status is status


def test_design_input_fits_the_loop_limit_and_says_what_it_left_out():
    docs = tuple(Document(SPECS[k], DocumentStatus.GENERATED, 'é' * 9_000) for k in ('srs', 'prd', 'acceptance_criteria'))
    data = design_input(docs)
    assert len(json.dumps(data, ensure_ascii=False).encode()) <= DESIGN_BUDGET_BYTES + 400
    assert data['approved_design'][0]['document'] == 'acceptance_criteria' and data['approved_design'][0]['clipped']
    assert data['omitted_documents'] == ['prd', 'srs']
