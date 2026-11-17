"""The Designer: UI/UX documents written after the architecture, approved at the same gate, and handed to the Programmer."""
import json

import pytest

pytest.importorskip('fastapi')
pytestmark = pytest.mark.usefixtures('every_document')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.runtime import DESIGN_STAGES
from siamang.config import MODEL_AGENTS, load_config
from siamang.documents.author import batches
from siamang.documents.diagrams import InvalidDiagram, blocks_of, carry_block, diagrams_of, language_of, replace_diagram
from siamang.documents.catalog import AGENTS, CATALOG, SPECS, Source
from tests.api.test_documents import author, documents, wait_for_state
from tests.api.test_http import headers, submit, wait, world  # noqa: F401
from tests.support.loop import CLEAN
from tests.support.programmer import make_model

DESIGNER_KINDS = ['design_tokens', 'wireframes', 'ui_spec', 'user_flow', 'sitemap', 'accessibility']


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
    # The tokens, the wireframes that use them, and the screens they draw come from one call: parts cannot read each other.
    assert batches('designing') == (('design_tokens', 'wireframes', 'ui_spec'), ('user_flow', 'sitemap', 'accessibility'))
    assert SPECS['wireframes'].blocks == ('mermaid', 'html') and SPECS['design_tokens'].blocks == ('mermaid', 'json')
    assert {s.blocks for s in CATALOG if s.kind not in ('wireframes', 'design_tokens')} == {('mermaid',)}


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


WIREFRAMES = ('### Masuk\n\n```html\n<style>.k{color:var(--color-text)}</style>\n<div class="k">Masuk</div>\n```\n\n'
              '```mermaid\nflowchart LR\n  A --> B\n```\n\n### Beranda\n\n```html\n<main>Beranda</main>\n```')
TOKENS = 'Token warna.\n\n```json\n{"colors": {"text": "#111111", "background": "#ffffff"}}\n```'
API_SPEC = 'Contoh respons.\n\n```json\n{"ok": true}\n```\n\n```html\n<p>contoh</p>\n```\n\n```mermaid\nsequenceDiagram\n  A->>B: x\n```'


def drawing_team():
    def answer(model, request):
        kinds = request.json_schema['properties']['documents']['items']['properties']['kind']['enum']
        content = {'wireframes': WIREFRAMES, 'design_tokens': TOKENS, 'api_spec': API_SPEC}
        return json.dumps({'documents': [{'kind': k, 'applicable': True, 'reason': '', 'content': content.get(k, f'isi {k}')}
                                         for k in kinds]})
    return {agent: make_model(answer, structured=True) for agent in ('lead', 'planner', 'analyst', 'designer', 'qa', 'reviewer')}


def edit(client, task_id, kind, index, source, base):
    return client.post(f'/v0/tasks/{task_id}/documents/{kind}/diagrams/{index}', headers=headers('approver'),
                       json={'source': source, 'base_sha256': base})


def test_blocks_are_counted_over_the_languages_a_document_allows():
    assert diagrams_of(WIREFRAMES) == ['flowchart LR\n  A --> B']  # Mermaid alone, as every other document counts
    assert [lang for lang, _ in blocks_of(WIREFRAMES, SPECS['wireframes'].blocks)] == ['html', 'mermaid', 'html']
    replaced = replace_diagram(WIREFRAMES, 2, '<main>Baru</main>', SPECS['wireframes'].blocks)
    assert diagrams_of(replaced, ('html',)) == [diagrams_of(WIREFRAMES, ('html',))[0], '<main>Baru</main>']
    assert '### Beranda' in replaced and 'flowchart LR' in replaced
    tokens = SPECS['design_tokens'].blocks
    assert '"#000000"' in replace_diagram(TOKENS, 0, '{"colors": {"text": "#000000"}}', tokens)
    for bad in ('{"colors": ', '[1, 2]', '"teks"'):
        with pytest.raises(InvalidDiagram):
            replace_diagram(TOKENS, 0, bad, tokens)


def test_an_owner_block_carried_into_a_new_design_keeps_its_language():
    allowed = SPECS['wireframes'].blocks
    assert language_of('  <main/>', allowed) == 'html' and language_of('flowchart LR', allowed) == 'mermaid'
    assert language_of('{"a": 1}', allowed) == 'mermaid'  # JSON is not a block of the wireframes
    kept = carry_block(WIREFRAMES, 0, '<p>Milik pemilik</p>', allowed)
    assert diagrams_of(kept, ('html',))[0] == '<p>Milik pemilik</p>'
    # The new document has a Mermaid block where the owner's HTML was: the HTML is added, nothing is overwritten.
    moved = carry_block(WIREFRAMES, 1, '<p>Milik pemilik</p>', allowed)
    assert moved.endswith('```html\n<p>Milik pemilik</p>\n```') and 'flowchart LR' in moved
    assert carry_block('', 0, '{"colors": {}}', SPECS['design_tokens'].blocks) == '```json\n{"colors": {}}\n```'


def test_the_owner_edits_a_wireframe_and_the_tokens_and_both_reach_the_programmer_first(world):
    programmer = make_model(CLEAN)
    app = create_app(world[0], models=programmer.router, authors={a: m.router for a, m in drawing_team().items()})
    with TestClient(app) as client:
        task_id = submit(client, world[1])
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        assert wait(client, task_id)['execution']['phase'] == 'design_approval'
        written = documents(client, task_id)

        wire = written['wireframes']
        saved = edit(client, task_id, 'wireframes', 2, '<main>Beranda baru</main>', wire['content_sha256'])
        assert saved.status_code == 200 and saved.json()['changed'], saved.text
        tokens = written['design_tokens']['content_sha256']
        assert edit(client, task_id, 'design_tokens', 0, '{"colors": ', tokens).status_code == 422
        assert edit(client, task_id, 'design_tokens', 0, '{"colors": {"text": "#222222"}}', tokens).status_code == 200
        # An example in the Architect's API document is code, not an editable block: only its diagram is.
        api = written['api_spec']['content_sha256']
        assert edit(client, task_id, 'api_spec', 1, 'x', api).status_code == 404
        assert edit(client, task_id, 'api_spec', 0, 'sequenceDiagram\n  B->>A: y', api).status_code == 200

        changes = client.get(f'/v0/tasks/{task_id}/document-edits', headers=headers()).json()['items']
        assert sorted((i['kind'], i['diagram']) for i in changes) == [('api_spec', 0), ('design_tokens', 0), ('wireframes', 2)]

        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                           json={'decision': 'approve'}).status_code == 200
        wait_for_state(client, task_id, 'waiting')
        sent = programmer.requests[0].messages[-1].content
        assert 'Beranda baru' in sent and '#222222' in sent and 'owner_edited' in sent


def test_the_capabilities_say_which_blocks_each_document_can_edit(world):
    with TestClient(create_app(world[0], models=make_model(CLEAN).router)) as client:
        catalog = {d['kind']: d for d in client.get('/v0/capabilities', headers=headers()).json()['documents']}
    assert catalog['wireframes']['blocks'] == ['mermaid', 'html'] and catalog['prd']['blocks'] == ['mermaid']
