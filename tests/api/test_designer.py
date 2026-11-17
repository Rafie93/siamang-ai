"""The Designer: UI/UX documents written after the architecture, approved at the same gate, and handed to the Programmer."""
import json

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.runtime import DESIGN_STAGES
from siamang.config import MODEL_AGENTS, load_config
from siamang.documents.author import batches
from siamang.documents.catalog import AGENTS, CATALOG, Source
from tests.api.test_documents import author, documents, wait_for_state
from tests.api.test_http import headers, submit, wait, world  # noqa: F401
from tests.support.loop import CLEAN
from tests.support.programmer import make_model

DESIGNER_KINDS = ['user_flow', 'sitemap', 'design_tokens', 'ui_spec', 'accessibility']


def phases(client, task_id):
    items = client.get(f'/v0/tasks/{task_id}/execution-events', headers=headers()).json()['items']
    return [e['phase'] for e in items]


def test_the_designer_writes_only_authored_design_documents_between_the_architecture_and_the_gate():
    mine = [s for s in CATALOG if s.agent == 'designer']
    assert [s.kind for s in mine] == DESIGNER_KINDS
    assert {(s.stage, s.source) for s in mine} == {('designing', Source.AUTHORED)}
    assert DESIGN_STAGES.index('designing') == DESIGN_STAGES.index('analysis') + 1 == len(DESIGN_STAGES) - 1
    assert AGENTS.index('analyst') < AGENTS.index('designer') < AGENTS.index('programmer')
    assert 'designer' in MODEL_AGENTS
    assert [len(g) for g in batches('designing')] == [3, 2]


def test_the_designer_reads_the_architecture_and_its_design_reaches_the_programmer(world):
    team = {agent: author() for agent in ('lead', 'planner', 'analyst', 'designer', 'qa', 'reviewer', 'programmer')}
    programmer = make_model(CLEAN)
    app = create_app(world[0], models=programmer.router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = submit(client, world[1])
        assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
        assert wait(client, task_id)['execution']['phase'] == 'design_approval'
        assert programmer.requests == []

        written = documents(client, task_id)
        assert all(written[k]['status'] == 'generated' and written[k]['stage'] == 'designing' for k in DESIGNER_KINDS)
        seen = [json.loads(r.messages[-1].content.split('\n', 1)[1]) for r in team['designer'].requests]
        assert len(seen) == 2 and all(s['prior_documents']['architecture'] == 'isi architecture' for s in seen)
        assert all('ui_spec' not in r.messages[-1].content for r in team['analyst'].requests)
        order = [p for p in dict.fromkeys(phases(client, task_id)) if p in ('analysis', 'designing', 'design_approval')]
        assert order == ['analysis', 'designing', 'design_approval']

        body = {'decision': 'approve'}
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json=body).status_code == 200
        wait_for_state(client, task_id, 'waiting')
        sent = programmer.requests[0].messages[-1].content
        assert 'isi ui_spec' in sent and 'isi design_tokens' in sent


def test_without_a_designer_model_the_gate_stands_and_its_documents_say_not_assigned(world):
    team = {agent: author() for agent in ('lead', 'planner', 'analyst', 'qa', 'reviewer', 'programmer')}
    app = create_app(world[0], models=make_model(CLEAN).router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        assert wait(client, task_id)['execution']['phase'] == 'design_approval'
        written = documents(client, task_id)
        assert {written[k]['status'] for k in DESIGNER_KINDS} == {'not_assigned'}
        assert 'designing' not in phases(client, task_id)  # no model, so no work to show


def test_a_designer_model_alone_is_enough_for_a_design_gate(world):
    designer = author()
    app = create_app(world[0], models=make_model(CLEAN).router, authors={'designer': designer.router})
    with TestClient(app) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        assert wait(client, task_id)['execution']['phase'] == 'design_approval'
        assert documents(client, task_id)['ui_spec']['status'] == 'generated'


def test_the_designer_can_be_given_its_own_model(tmp_path):
    path = tmp_path / 'siamang.toml'
    path.write_text('[storage]\npath = "x.db"\n\n[providers.echo]\ntype = "echo"\n\n[models.e]\nprovider = "echo"\n'
                    'capabilities = ["text_generation"]\n\n[agents.designer]\nmodel = "e"\n')
    assert load_config(path, env={}).agents['designer'].model == 'e'
