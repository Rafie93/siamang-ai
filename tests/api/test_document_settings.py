"""The owner chooses which documents each agent produces; a switched-off document is neither asked for nor recorded."""
import hashlib
from dataclasses import replace

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.settings import Principal
from siamang.documents.catalog import CATALOG, DEFAULT_DISABLED, SPECS, disabled_kinds
from tests.api.test_documents import app, author, documents, team, wait_for_state  # noqa: F401
from tests.api.test_http import headers, submit, wait, world  # noqa: F401

OWNER = 'test-owner-credential-' + 'e' * 32
OFF_BY_DEFAULT = {'wbs', 'complexity', 'acceptance_criteria', 'dependencies', 'technical_design', 'ui_spec',
                  'user_flow', 'accessibility', 'implementation_notes', 'coverage_report', 'ci_results',
                  'build_artifacts', 'qa_verdict'}


@pytest.fixture
def owned(world):
    owner = Principal('owner', hashlib.sha256(OWNER.encode()).hexdigest(), frozenset({'read', 'configure'}), frozenset({'*'}))
    settings = replace(world[0], principals=world[0].principals + (owner,))
    return (settings,) + world[1:]


def as_owner():
    return {'Authorization': 'Bearer ' + OWNER}


def asked(model):
    """Every document kind a model was asked to write."""
    return {k for r in model.requests if r.json_schema and 'documents' in r.json_schema.get('properties', {})
            for k in r.json_schema['properties']['documents']['items']['properties']['kind']['enum']}


def run_to_the_end(client, project):
    task_id = submit(client, project)
    client.post(f'/v0/tasks/{task_id}/run', headers=headers())
    wait(client, task_id)
    client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})
    wait_for_state(client, task_id, 'waiting')
    return documents(client, task_id)


def test_the_defaults_are_the_documents_the_owner_named():
    assert DEFAULT_DISABLED == OFF_BY_DEFAULT
    assert disabled_kinds({}) == OFF_BY_DEFAULT
    assert disabled_kinds({'wbs': True, 'prd': False, 'nonsense': False}) == OFF_BY_DEFAULT - {'wbs'} | {'prd'}


def test_by_default_a_switched_off_document_is_never_asked_for_nor_recorded(world, team):
    with TestClient(app(world, team)) as client:
        written = run_to_the_end(client, world[1])
    assert set(written) == {s.kind for s in CATALOG} - OFF_BY_DEFAULT
    assert not asked(team['planner']) & OFF_BY_DEFAULT
    assert asked(team['planner']) == {'prd', 'roadmap', 'task_list', 'priorities'}


def test_the_owner_switches_documents_and_the_next_run_follows(owned, team):
    with TestClient(app(owned, team)) as client:
        before = client.get('/v0/settings/documents', headers=headers('reader')).json()['items']
        assert {d['kind'] for d in before if not d['enabled']} == OFF_BY_DEFAULT
        assert all(d['default_enabled'] == d['enabled'] for d in before)

        for kind, enabled in (('wbs', True), ('roadmap', False)):
            response = client.post('/v0/settings/documents', headers=as_owner(), json={'kind': kind, 'enabled': enabled})
            assert response.status_code == 200, response.text
        after = {d['kind']: d for d in response.json()['items']}
        assert after['wbs']['enabled'] and not after['wbs']['default_enabled']
        assert not after['roadmap']['enabled'] and after['roadmap']['default_enabled']
        catalog = {d['kind']: d for d in client.get('/v0/capabilities', headers=headers()).json()['documents']}
        assert catalog['wbs']['enabled'] and not catalog['roadmap']['enabled'] and len(catalog) == len(SPECS)

        written = run_to_the_end(client, owned[1])
    assert 'wbs' in written and 'roadmap' not in written
    assert 'wbs' in asked(team['planner']) and 'roadmap' not in asked(team['planner'])


def test_switching_a_document_needs_configure_on_every_project_and_a_known_kind(owned, team):
    with TestClient(app(owned, team)) as client:
        body = {'kind': 'wbs', 'enabled': True}
        assert client.post('/v0/settings/documents', headers=headers(), json=body).status_code == 403
        assert client.post('/v0/settings/documents', headers=as_owner(), json={'kind': 'nope', 'enabled': True}).status_code == 422
        assert client.post('/v0/settings/documents', headers=as_owner(), json={'kind': 'wbs', 'enabled': 'yes'}).status_code == 422
        items = client.get('/v0/settings/documents', headers=headers()).json()['items']
        assert not next(d for d in items if d['kind'] == 'wbs')['enabled']
