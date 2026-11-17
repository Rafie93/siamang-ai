"""The owner edits a diagram at the design gate: what is kept, what is refused, and what reaches the Programmer."""
import hashlib
import json
import threading

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.store import RunStore
from siamang.documents.author import DESIGN_BUDGET_BYTES, Document, design_input
from siamang.documents.catalog import SPECS, DocumentStatus
from siamang.documents.diagrams import InvalidDiagram, changed_diagrams, diagrams_of, replace_diagram
from siamang.storage import Database
from tests.api.test_documents import documents, wait_for_state
from tests.api.test_http import headers, submit, wait, world  # noqa: F401
from tests.api.test_repos import TASK_READY, World, _new_file, deliver, new_project, snapshot, task, wait_execution
from tests.support.loop import CLEAN, CONCERN
from tests.support.programmer import make_model

CLASS_DIAGRAM = 'Pengantar.\n\n```python\nprint(1)\n```\n\n```mermaid\nclassDiagram\n  class Lama\n```\n\nTengah.\n\n```mermaid\nclassDiagram\n  class Kedua\n```\n\nPenutup.'
EDITED = 'classDiagram\n  class Baru'


def author():
    def answer(model, request):
        kinds = request.json_schema['properties']['documents']['items']['properties']['kind']['enum']
        return json.dumps({'documents': [{'kind': k, 'applicable': True, 'reason': '',
                                          'content': CLASS_DIAGRAM if k == 'class_diagram' else f'isi {k}'} for k in kinds]})
    return make_model(answer, structured=True)


@pytest.fixture
def team():
    return {agent: author() for agent in ('lead', 'planner', 'analyst', 'qa', 'reviewer', 'programmer')}


def at_the_gate(client, world):
    task_id = submit(client, world[1])
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    assert wait(client, task_id)['execution']['phase'] == 'design_approval'
    return task_id


def edit(client, task_id, base, *, kind='class_diagram', index=0, source=EDITED, who='approver'):
    return client.post(f'/v0/tasks/{task_id}/documents/{kind}/diagrams/{index}', headers=headers(who),
                       json={'source': source, 'base_sha256': base})


def test_an_edit_changes_one_diagram_keeps_the_old_text_and_reaches_the_programmer_first(world, team):
    programmer = make_model(CLEAN)
    app = create_app(world[0], models=programmer.router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = at_the_gate(client, world)
        before = documents(client, task_id)['class_diagram']
        assert before['edited_by'] is None
        assert client.get(f'/v0/tasks/{task_id}/document-edits', headers=headers()).json()['items'] == []

        saved = edit(client, task_id, before['content_sha256'])
        assert saved.status_code == 200, saved.text
        after = documents(client, task_id)['class_diagram']
        assert after['content'] == before['content'].replace('class Lama', 'class Baru')
        assert after['content_sha256'] == saved.json()['content_sha256'] != before['content_sha256']
        assert after['edited_by'] == 'approver'

        with Database(world[0].database) as db:
            kept = db.conn.execute('SELECT * FROM api_document_revisions').fetchall()
            assert [(r['previous_content'], r['diagram'], r['edited_by']) for r in kept] == [(before['content'], 0, 'approver')]
            with pytest.raises(Exception, match='append only'):
                db.conn.execute('DELETE FROM api_document_revisions')

        summary = client.get(f'/v0/tasks/{task_id}/document-edits', headers=headers()).json()
        assert [(i['kind'], i['diagram'], i['added'], i['removed'], i['edited_by']) for i in summary['items']] == [
            ('class_diagram', 0, 1, 1, 'approver')]
        assert summary['clipped'] == []

        with Database(world[0].database) as db:
            edited_digest = RunStore(db).documents_digest(after['run_id'])
        body = {'decision': 'approve'}
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json=body).status_code == 200
        wait_for_state(client, task_id, 'waiting')
        sent = programmer.requests[0].messages[-1].content
        assert 'class Baru' in sent and 'class Lama' not in sent and 'owner_edited' in sent
        with Database(world[0].database) as db:
            assert db.conn.execute('SELECT documents_sha256 FROM api_design_decisions').fetchone()[0] == edited_digest

        assert edit(client, task_id, after['content_sha256'], source='classDiagram\n  class Terlambat').status_code == 409


def test_what_an_edit_may_not_do(world, team):
    app = create_app(world[0], models=make_model(CLEAN).router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = at_the_gate(client, world)
        written = documents(client, task_id)
        base = written['class_diagram']['content_sha256']
        assert edit(client, task_id, base, who='outsider').status_code == 404
        response = client.post(f'/v0/tasks/{task_id}/documents/class_diagram/diagrams/0', headers=headers(),
                               json={'source': EDITED, 'base_sha256': base})
        assert response.status_code == 403
        assert edit(client, task_id, 'a' * 64).json()['error']['code'] == 'document_changed'
        assert edit(client, task_id, base, index=9).status_code == 404
        assert edit(client, task_id, base, kind='nope').status_code == 404
        assert edit(client, task_id, base, source='a\n```\nsisipan').status_code == 422
        assert edit(client, task_id, base, source='   \n').status_code == 422
        assert edit(client, task_id, base, source=diagrams_of(CLASS_DIAGRAM)[0]).json()['changed'] is False
        assert documents(client, task_id)['class_diagram']['edited_by'] is None
        with Database(world[0].database) as db:
            assert db.conn.execute('SELECT COUNT(*) FROM api_document_revisions').fetchone()[0] == 0


def test_a_rewrite_never_loses_the_owner_edit(world, team):
    app = create_app(world[0], models=make_model(CLEAN).router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = at_the_gate(client, world)
        edit(client, task_id, documents(client, task_id)['class_diagram']['content_sha256'])
        edited = documents(client, task_id)['class_diagram']
        with Database(world[0].database) as db:
            RunStore(db).save_documents(edited['run_id'], task_id, (Document(SPECS['class_diagram'], DocumentStatus.GENERATED, 'tulis ulang'),))
            kept = db.conn.execute('SELECT previous_content, diagram FROM api_document_revisions ORDER BY position').fetchall()
        assert kept[-1]['previous_content'] == edited['content'] and kept[-1]['diagram'] is None
        assert documents(client, task_id)['class_diagram']['edited_by'] is None
        assert client.get(f'/v0/tasks/{task_id}/document-edits', headers=headers()).json()['items'] == []


def test_diagrams_are_numbered_past_other_code_blocks_and_only_the_named_one_changes():
    assert diagrams_of(CLASS_DIAGRAM) == ['classDiagram\n  class Lama', 'classDiagram\n  class Kedua']
    replaced = replace_diagram(CLASS_DIAGRAM, 1, 'x\r\ny\n')
    assert diagrams_of(replaced) == ['classDiagram\n  class Lama', 'x\ny']
    assert 'print(1)' in replaced and 'Penutup.' in replaced
    assert replace_diagram('```mermaid\nlama', 0, 'baru') == '```mermaid\nbaru'  # unclosed, as it renders
    assert changed_diagrams(CLASS_DIAGRAM, replaced) == [dict(diagram=1, added=2, removed=2)]
    with pytest.raises(IndexError):
        replace_diagram(CLASS_DIAGRAM, 2, 'x')
    with pytest.raises(InvalidDiagram):
        replace_diagram(CLASS_DIAGRAM, 0, '```')


def test_an_edit_reaches_the_programmer_even_behind_documents_that_fill_the_input():
    big = 'é' * 9_000
    docs = tuple(Document(SPECS[k], DocumentStatus.GENERATED, big) for k in ('acceptance_criteria', 'prd', 'srs'))
    plain = Document(SPECS['class_diagram'], DocumentStatus.GENERATED, '```mermaid\nclassDiagram\n```')
    assert 'class_diagram' in design_input((*docs, plain))['omitted_documents']

    data = design_input((*docs, Document(SPECS['class_diagram'], DocumentStatus.GENERATED, 'prosa lama\n' + plain.content.replace('classDiagram', 'classDiagram\n  class Baru'), edited_by='owner')))
    first = data['approved_design'][0]
    assert first['document'] == 'class_diagram' and first['owner_edited'] and 'class Baru' in first['text']
    assert 'prosa lama' not in first['text']
    assert data['approved_design'][1]['document'] == 'acceptance_criteria'
    assert 'design_precedence' in data
    assert len(json.dumps(data, ensure_ascii=False).encode()) <= DESIGN_BUDGET_BYTES + 400


def test_a_huge_edit_is_clipped_to_its_share_and_says_so():
    source = '```mermaid\nclassDiagram\n' + 'A --> B : "x"\n' * 2_000 + '```'
    data = design_input((Document(SPECS['class_diagram'], DocumentStatus.GENERATED, source, edited_by='owner'),
                         Document(SPECS['acceptance_criteria'], DocumentStatus.GENERATED, 'kriteria')), budget=3_000)
    assert data['approved_design'][0]['clipped'] and data['approved_design'][1]['text'] == 'kriteria'
    assert len(json.dumps(data, ensure_ascii=False).encode()) <= 3_000


def seed_diagram(settings, task_id, kind='class_diagram', content='Isi.\n\n```mermaid\nclassDiagram\n  class Lama\n```'):
    """The test models write plain text; a real one draws."""
    with Database(settings.database) as db:
        db.conn.execute('UPDATE api_documents SET content=?, content_sha256=? WHERE task_id=? AND kind=?',
                        (content, hashlib.sha256(content.encode()).hexdigest(), task_id, kind))


def edits(client, task_id):
    response = client.get(f'/v0/tasks/{task_id}/document-edits', headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def send(client, task_id, who='approver', **body):
    return client.post(f'/v0/tasks/{task_id}/design-edits/send', headers=headers(who), json=body)


def test_a_stopped_run_goes_again_on_the_edited_diagram_and_the_change_is_on_record(world, team):
    sent = threading.Event()

    def programmer(model, request):
        return CLEAN if sent.is_set() else CONCERN
    coder = make_model(programmer)
    app = create_app(world[0], models=coder.router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = at_the_gate(client, world)
        base = documents(client, task_id)['class_diagram']['content_sha256']
        assert edits(client, task_id)['mode'] == 'gate'
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'}).status_code == 200
        stopped = wait_for_state(client, task_id, 'waiting')
        assert stopped['execution']['wait_reason'] == 'quality_gate'
        state = edits(client, task_id)
        assert state['mode'] == 'stopped' and state['pending'] is False
        assert send(client, task_id).json()['error']['code'] == 'no_design_edits'

        assert edit(client, task_id, base).status_code == 200
        state = edits(client, task_id)
        assert state['pending'] is True and [i['kind'] for i in state['items']] == ['class_diagram']
        assert send(client, task_id, who='outsider').status_code == 404
        assert send(client, task_id, who='operator').status_code == 403

        before = len(coder.requests)
        sent.set()
        accepted = send(client, task_id, note='Ikuti relasi baru.')
        assert accepted.status_code == 202, accepted.text
        done = wait_for_state(client, task_id, 'waiting')
        assert done['execution']['phase'] == 'awaiting_approval', done['execution']
        given = coder.requests[before].messages[-1].content
        assert 'class Baru' in given and 'class Lama' not in given

        decisions = client.get(f'/v0/tasks/{task_id}/autonomy', headers=headers()).json()['items']
        record = next(d for d in decisions if d['outcome'] == 'edits_sent')
        assert record['actor'] == 'approver' and record['seal_valid'] and record['reasons'] == ['Ikuti relasi baru.']
        assert edits(client, task_id)['pending'] is False


def test_sending_an_edit_does_not_count_as_a_new_answer_so_a_retry_keeps_the_design():
    from siamang.api.runtime import _basis
    from types import SimpleNamespace as N
    task, project = N(description='d'), N(name='n', description='p')
    answer = {'role': 'owner', 'message': 'jawaban'}
    assert _basis(task, '', [answer, {'role': 'owner', 'message': 'kirim', 'design_edit': True}], project) == _basis(task, '', [answer], project)
    assert _basis(task, '', [answer], project) != _basis(task, '', [], project)


def test_an_edit_on_a_shipped_task_waits_as_a_proposal_and_becomes_a_revision_that_uses_it(tmp_path):
    github = World(tmp_path)
    answers = [CLEAN, _new_file('revisi')]
    github.programmer = make_model(lambda model, request: answers.pop(0))
    with TestClient(github.app()) as client:
        project_id = new_project(client, github)
        task_id = task(client, project_id)
        assert deliver(client, github, task_id)['publication'] == 'published'
        seed_diagram(github.settings, task_id)
        shipped = documents(client, task_id)['class_diagram']
        assert edits(client, task_id)['mode'] == 'revision'
        assert send(client, task_id).json()['error']['code'] == 'no_design_edits'

        saved = edit(client, task_id, shipped['content_sha256'])
        assert saved.status_code == 200 and saved.json()['mode'] == 'revision', saved.text
        assert documents(client, task_id)['class_diagram']['content'] == shipped['content']  # the record of what shipped
        proposed = edits(client, task_id)
        assert proposed['pending'] and [(i['diagram'], i['source'], i['added'], i['removed']) for i in proposed['items']] == [(0, EDITED, 1, 1)]

        # Writing the original back withdraws it; the document's hash still guards the edit.
        assert edit(client, task_id, shipped['content_sha256'], source='classDiagram\n  class Lama').status_code == 200
        assert edits(client, task_id)['items'] == []
        assert edit(client, task_id, shipped['content_sha256']).status_code == 200

        github.lead_replies.append(TASK_READY)
        made = send(client, task_id, note='Jaga nama lama.')
        assert made.status_code == 202, made.text
        revision = made.json()['task']['task_id']
        # This approver may create the task but not run it: it says so, and the task waits for someone who may.
        assert made.json()['mode'] == 'revision' and made.json()['scheduled'] is False and made.json()['reason'] == 'forbidden'
        assert revision != task_id
        assert client.post(f'/v0/tasks/{revision}/run', headers=headers()).status_code == 202
        assert 'Class Diagram' in made.json()['task']['description'] and 'Jaga nama lama.' in made.json()['task']['description']
        assert edits(client, task_id)['items'] == []
        assert send(client, task_id).json()['error']['code'] == 'no_design_edits'

        wait_execution(client, revision, 'design_approval')
        drawn = documents(client, revision)['class_diagram']
        assert 'class Baru' in drawn['content'] and drawn['edited_by'] == 'approver'  # a fresh design, with the owner's diagram in it
        assert documents(client, task_id)['class_diagram']['content'] == shipped['content']
        assert [i['kind'] for i in edits(client, revision)['items']] == ['class_diagram']

        before = len(github.programmer.requests)
        assert client.post(f'/v0/tasks/{revision}/design-decision', headers=headers('approver'), json={'decision': 'approve'}).status_code == 200
        wait_execution(client, revision, 'awaiting_approval')
        given = github.programmer.requests[before].messages[-1].content
        assert 'class Baru' in given and 'owner_edited' in given
        assert snapshot(client, revision)['branch']['revises_task_id'] == task_id


def test_diagrams_stay_closed_when_nothing_can_carry_a_change(world, team):
    app = create_app(world[0], models=make_model(CLEAN).router, authors={a: m.router for a, m in team.items()})
    with TestClient(app) as client:
        task_id = at_the_gate(client, world)
        base = documents(client, task_id)['class_diagram']['content_sha256']
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'}).status_code == 200
        done = wait_for_state(client, task_id, 'waiting')
        assert done['execution']['wait_reason'] == 'approval_required'  # a finished proposal: approve or reject it first
        assert edits(client, task_id)['mode'] is None
        assert edit(client, task_id, base).json()['error']['code'] == 'design_not_pending'
        assert send(client, task_id).json()['error']['code'] == 'design_not_pending'
