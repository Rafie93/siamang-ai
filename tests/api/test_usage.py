"""Tokens and time per project, task, and agent: every model call and every agent's work is on the record."""
import json
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api import usage
from siamang.api.app import create_app
from siamang.api.store import RunStore
from siamang.documents.catalog import AGENTS
from siamang.models import ProviderOutput, Usage
from siamang.projects import ProjectRegistry
from siamang.storage import Database
from siamang.tasks import TaskEngine, TaskRequest
from tests.api.test_documents import wait_for_state
from tests.api.test_http import headers, submit, wait, world  # noqa: F401
from tests.support.loop import CLEAN
from tests.support.programmer import INSTRUCTION, make_model

# Each agent's model reports its own token count per call, so a call counted toward the wrong agent shows.
UNITS = {'lead': 10, 'planner': 20, 'analyst': 30, 'designer': 40, 'programmer': 70, 'qa': 50, 'reviewer': 60}
CODING = 100


def writer(unit):
    def answer(model, request):
        kinds = request.json_schema['properties']['documents']['items']['properties']['kind']['enum']
        docs = [{'kind': k, 'applicable': True, 'content': f'isi {k}', 'reason': ''} for k in kinds]
        return ProviderOutput(json.dumps({'documents': docs}), usage=Usage(unit, unit // 10, cache_read_tokens=unit // 2))
    return make_model(answer, structured=True)


def coder():
    return make_model(lambda model, request: ProviderOutput(CLEAN, usage=Usage(CODING, 7, cache_write_tokens=40)))


def app(world, programmer=None):
    return create_app(world[0], models=(programmer or coder()).router,
                      authors={agent: writer(unit).router for agent, unit in UNITS.items()})


def rows(world, table):
    with Database(world[0].database) as db:
        return [dict(r) for r in db.conn.execute(f'SELECT * FROM {table} ORDER BY position')]


def test_every_call_of_a_task_is_counted_toward_its_agent(world):
    with TestClient(app(world)) as client:
        task_id = submit(client, world[1])
        assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
        assert wait(client, task_id)['execution']['phase'] == 'design_approval'
        client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})
        wait_for_state(client, task_id, 'waiting')
        shown = client.get(f'/v0/tasks/{task_id}/usage', headers=headers()).json()
        listed = client.get('/v0/tasks', params={'project_id': world[1].project_id}, headers=headers()).json()['items']
        portfolio = client.get('/v0/portfolio', headers=headers()).json()['items']
        assert client.get(f'/v0/tasks/{task_id}/usage', headers=headers('outsider')).status_code == 404

    calls, spans = rows(world, 'api_model_calls'), rows(world, 'api_agent_activity')
    assert calls and all(c['task_id'] == task_id and c['run_id'] and c['project_id'] == world[1].project_id for c in calls)
    assert {c['agent'] for c in calls} == set(AGENTS)  # nothing outside an activity
    for call in calls:
        coding = call['agent'] == 'programmer' and call['purpose'] != 'documents'
        assert call['input_tokens'] == (CODING if coding else UNITS[call['agent']]), call
        assert call['outcome'] == 'ok' and call['duration_ms'] >= 0
    assert {(s['agent'], s['stage']) for s in spans} >= {
        ('lead', 'briefing'), ('planner', 'planning'), ('analyst', 'analysis'), ('designer', 'designing'),
        ('programmer', 'programming'), ('qa', 'qa'), ('reviewer', 'reviewing'), ('lead', 'reporting')}
    assert all(s['outcome'] == 'done' for s in spans)

    assert shown['complete'] is True and [a['agent'] for a in shown['agents']] == list(AGENTS)
    by_agent = {a['agent']: a for a in shown['agents']}
    assert by_agent['planner']['input_tokens'] == 20 * by_agent['planner']['calls']
    assert by_agent['planner']['cache_read_tokens'] == 10 * by_agent['planner']['calls']
    assert all(a['active_ms'] >= 0 and a['models'] == ['fake-a'] for a in shown['agents'])
    total = shown['totals']
    assert total['input_tokens'] == sum(c['input_tokens'] for c in calls)
    assert total['output_tokens'] == sum(c['output_tokens'] for c in calls)
    assert total['total_tokens'] == total['input_tokens'] + total['output_tokens']
    assert total['calls'] == len(calls) and total['failed_calls'] == total['unreported_calls'] == 0
    assert total['work_ms'] > 0 and total['waiting_ms'] > 0  # it waited at the design gate

    [item] = [t for t in listed if t['task_id'] == task_id]
    assert item['usage']['total_tokens'] == total['total_tokens'] and item['usage']['work_ms'] == total['work_ms']
    [mine] = [p for p in portfolio if p['project_id'] == world[1].project_id]
    assert mine['usage']['total_tokens'] == total['total_tokens']
    with Database(world[0].database) as db:
        assert 'usage' in {r['kind'] for r in RunStore(db).changes(0, 10_000)}  # the stream says it changed


def test_a_failed_call_counts_as_a_call_with_unknown_tokens(world):
    from siamang.models.errors import ModelTimeoutError
    slow = make_model(structured=True, fail_with=ModelTimeoutError('slow'))
    with TestClient(create_app(world[0], models=coder().router, authors={'planner': slow.router})) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        assert wait(client, task_id)['status'] == 'failed'
        shown = client.get(f'/v0/tasks/{task_id}/usage', headers=headers()).json()
    planner = next(a for a in shown['agents'] if a['agent'] == 'planner')
    assert planner['calls'] == planner['failed_calls'] == planner['unreported_calls'] > 0
    assert planner['input_tokens'] == planner['output_tokens'] == 0
    assert [s['outcome'] for s in rows(world, 'api_agent_activity') if s['agent'] == 'planner'] == ['failed']


# --- the arithmetic, on records written directly


T0 = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def call(project_id, task_id=None, draft_id=None, agent='programmer', tokens=(100, 10), outcome='ok'):
    return dict(request_id='mrq_x', project_id=project_id, task_id=task_id, draft_id=draft_id, agent=agent,
                stage='programming', provider_id='claude', model_id='claude-sonnet', outcome=outcome,
                input_tokens=tokens[0], output_tokens=tokens[1], started_at=at(0), finished_at=at(1), duration_ms=1000)


def activity(project_id, task_id, start, end, agent='programmer', package=None, draft_id=None):
    return dict(project_id=project_id, task_id=task_id, draft_id=draft_id, agent=agent, stage='programming',
                package=package, outcome='done', started_at=at(start), finished_at=at(end), duration_ms=(end - start) * 1000)


@pytest.fixture
def records(world):
    db = Database(world[0].database)
    store = RunStore(db)
    store.initialize()
    engine = TaskEngine(db, ProjectRegistry(db))
    made = [engine.create(TaskRequest.from_dict({'project_id': world[1].project_id, 'description': INSTRUCTION,
                                                 'request_id': f'req_usage{i}', 'source': 'api'})).task for i in (1, 2)]
    yield db, store, made
    db.close()


def test_parallel_work_counts_once_and_each_agent_apart(records, world):
    db, store, (one, _) = records
    pid = world[1].project_id
    for start, end, package in [(0, 10, 'api'), (5, 12, 'web'), (20, 25, 'api')]:  # two Programmers side by side
        store.record_usage('activity', activity(pid, one.task_id, start, end, package=package))
    store.record_usage('activity', activity(pid, one.task_id, 12, 15, agent='qa'))
    shown = usage.task_usage(db, one, store.usage_since(), at(60))
    by_agent = {a['agent']: a for a in shown['agents']}
    assert by_agent['programmer']['active_ms'] == 17_000 and by_agent['qa']['active_ms'] == 3_000
    assert by_agent['qa']['calls'] == 0  # QA checks without a model, and its time still counts


def test_a_draft_belongs_to_its_task_and_the_rest_to_the_project(records, world):
    db, store, (one, two) = records
    pid = world[1].project_id
    became, dropped = store.add_draft(pid, 'buat login', {}), store.add_draft(pid, 'lupakan', {})
    store.update_draft(became, task_id=one.task_id)
    store.record_usage('call', call(pid, draft_id=became, agent='lead', tokens=(300, 30)))
    store.record_usage('call', call(pid, draft_id=dropped, agent='lead', tokens=(50, 5)))
    store.record_usage('call', call(pid, agent='lead', tokens=(7, 1)))  # the project's intake
    store.record_usage('call', call(pid, one.task_id, tokens=(1000, 100)))
    store.record_usage('call', call(pid, two.task_id, tokens=(None, None), outcome='timeout'))
    project = ProjectRegistry(db).get(pid)
    shown = usage.project_usage(db, project, store.usage_since(), at(60))
    tasks = {t['task_id']: t for t in shown['tasks']}
    assert tasks[one.task_id]['input_tokens'] == 1300 and tasks[one.task_id]['calls'] == 2
    assert tasks[two.task_id]['input_tokens'] == 0 and tasks[two.task_id]['unreported_calls'] == 1
    assert shown['outside_tasks']['input_tokens'] == 57 and shown['outside_tasks']['calls'] == 2
    assert shown['totals']['input_tokens'] == 1357 == sum(t['input_tokens'] for t in shown['tasks']) + 57
    assert [t['task_id'] for t in shown['tasks']] == [one.task_id, two.task_id]  # most tokens first
    assert usage.task_usage(db, one, store.usage_since(), at(60))['totals']['input_tokens'] == 1300


def test_work_time_follows_the_runs_and_stops_at_the_last_heartbeat(records, world):
    db, store, (one, two) = records

    def event(task, run_id, second, state, **note):
        db.conn.execute('INSERT INTO api_run_events(run_id,task_id,timestamp,payload) VALUES (?,?,?,?)',
                        (run_id, task.task_id, at(second), json.dumps(dict(state=state, **note))))

    event(one, 'req_a', 0, 'scheduled')
    event(one, 'req_a', 2, 'running')
    event(one, 'req_a', 12, 'waiting')      # ten seconds of work, then the owner
    event(one, 'req_a', 112, 'running')     # a hundred seconds waiting
    event(one, 'req_a', 3_600, 'interrupted', last_heartbeat=at(130))  # the server died at 130 and came back an hour later
    event(two, 'req_b', 0, 'running')       # still running: counts up to now
    times = usage.run_time(db, [one.task_id, two.task_id], usage.to_ms(at(30)))
    assert times[one.task_id] == (10_000 + 18_000, 100_000)
    assert times[two.task_id] == (30_000, 0)


def test_a_restart_records_the_last_heartbeat_of_a_cut_off_run(records, world):
    db, store, (one, _) = records
    run_id = store.create(one.task_id)
    store.update(run_id, state='running', phase='programming')
    beat = store.get(run_id)['heartbeat_at']
    with TestClient(create_app(world[0])):  # startup finds the run it did not finish
        pass
    last = store.events(one.task_id)[-1]
    assert last['state'] == 'interrupted' and last['last_heartbeat'] == beat


def test_a_record_without_a_project_is_not_kept_and_none_is_rewritten(records, world):
    db, store, (one, _) = records
    assert store.record_usage('call', call(None)) is False
    assert store.record_usage('call', call(world[1].project_id, one.task_id)) is True
    store.record_usage('activity', activity(world[1].project_id, one.task_id, 0, 1))
    for table in ('api_model_calls', 'api_agent_activity'):
        assert db.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1
        with pytest.raises(Exception, match='append only'):
            db.conn.execute(f'DELETE FROM {table}')
        with pytest.raises(Exception, match='append only'):
            db.conn.execute(f"UPDATE {table} SET agent='lead'")


def test_union_of_spans():
    assert usage.union_ms([]) == 0
    assert usage.union_ms([(0, 10), (2, 3), (10, 15), (20, 21)]) == 16
