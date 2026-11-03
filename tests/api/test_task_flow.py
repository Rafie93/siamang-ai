"""A task never stops without an explanation, and the Lead understands it before it exists."""
import json
import threading

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.analyst.errors import AnalysisRequestError
from siamang.analyst.request import AnalysisRequest
from siamang.api.store import RunStore
from siamang.repos.workspace import named_in, source_files
from siamang.projects import ProjectRegistry
from siamang.storage import Database
from tests.api.test_http import headers
from tests.api.test_repos import World, git, new_project, snapshot, task, until, wait_execution
from tests.support.loop import CLEAN, CONCERN
from tests.support.programmer import make_model, reply

UNDERSTOOD = {'status': 'ready', 'message': 'Saya paham.', 'questions': [], 'title': 'Tambah tombol kontak',
              'spec': '# Brief\nTambah tombol kontak ke halaman utama.'}
UNCLEAR = {'status': 'need_answers', 'message': 'Belum jelas.', 'spec': '', 'title': '',
           'questions': [{'question': 'Tombol apa?', 'options': ['Email', 'LinkedIn']}]}
TASK_READY = {'status': 'ready', 'message': 'Jelas.', 'questions': [], 'spec': ''}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def scripted(*answers):
    pending = list(answers)
    return make_model(lambda model, request: pending.pop(0) if len(pending) > 1 else pending[0])


def to_design_gate(client, world, task_id):
    world.lead_replies.append(TASK_READY)
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    wait_execution(client, task_id, 'design_approval')
    assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                       json={'decision': 'approve'}).status_code == 200


def stopped(client, task_id, reason):
    return until(lambda: snapshot(client, task_id)['execution']['wait_reason'] == reason
                 and snapshot(client, task_id)['execution']['phase'] != 'queued' and snapshot(client, task_id), reason)


def messages(client, task_id):
    return client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items']


def draft_url(project_id, draft_id=''):
    return f'/v0/projects/{project_id}/drafts' + (f'/{draft_id}' if draft_id else '')


def draft_state(client, project_id, draft_id, state):
    def reached():
        current = client.get(draft_url(project_id, draft_id), headers=headers()).json()
        return current['state'] == state and current
    return until(reached, f'draft {state}')


# -- the Programmer sees the code it must change ------------------------------------------


def test_programmer_is_shown_the_files_whatever_words_the_task_uses(world):
    """The regression behind the stuck tasks: only names matching the instruction reached the model."""
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id, description='Kerjakan sesuai spesifikasi pada projek')
        to_design_gate(client, world, task_id)
        until(lambda: world.programmer.requests, 'the Programmer was called')
        shown = json.loads(world.programmer.requests[0].messages[-1].content.split('\n', 1)[1])['files']
        tracked = source_files(git('ls-tree', '-r', '--name-only', 'main', cwd=world.bare).split())
        assert shown and {f['path'] for f in shown} <= set(tracked)


def test_a_request_can_never_choose_the_focus_files():
    with pytest.raises(AnalysisRequestError):
        AnalysisRequest.from_dict({'task_id': 'tsk_01M6DWXJB2YJWYZA72VTB4PQYV', 'project_id': 'prj_01M6DWEKYMTD61GM0PREE67ED1',
                                   'instruction': 'x', 'focus': ['.env']})


def test_big_repository_uses_only_tracked_files_the_model_picked(world, tmp_path):
    runtime_names = [f'src/m{i}.py' for i in range(20)]
    picker = make_model(json.dumps({'files': ['src/m3.py', '../../etc/passwd', '.env', 'src/m7.py']}), structured=True)

    class Project:
        root_path = str(tmp_path)

    class Task:
        description = 'x'

    app = world.app()
    runtime = app.state.runtime
    runtime._authors = dict(runtime._authors, analyst=picker.router)
    runtime._repos.files = lambda root: runtime_names
    assert runtime._focus(Project, (), Task) == ('src/m3.py', 'src/m7.py')


def test_small_repository_needs_no_model_to_choose(world, tmp_path):
    app = world.app()
    runtime = app.state.runtime
    picker = make_model(json.dumps({'files': []}), structured=True)
    runtime._authors = dict(runtime._authors, analyst=picker.router)
    runtime._repos.files = lambda root: ['index.html', 'logo.png', 'package-lock.json', 'app.min.js', 'style.css']

    class Project:
        root_path = str(tmp_path)

    assert runtime._focus(Project, (), None) == ('index.html', 'style.css')
    assert picker.requests == []


def test_named_files_are_the_fallback_when_no_model_can_choose():
    names = ['a/b.py', 'index.html', 'c.py']
    assert named_in(names, ['ubah a/b.py']) == ['a/b.py', 'index.html']
    assert source_files(['x.PNG', 'y.py']) == ['y.py']


# -- every stop can be answered -----------------------------------------------------------


def test_programmer_question_stops_the_team_and_the_answer_resumes_coding(world):
    world.programmer = scripted(reply([], status='waiting', summary='Butuh keputusan.', unknowns=['Warna utamanya apa?']), CLEAN)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        to_design_gate(client, world, task_id)
        stopped(client, task_id, 'clarification_required')
        asked = messages(client, task_id)[-1]
        assert asked['message'] == 'Butuh keputusan.' and asked['questions'][0]['question'] == 'Warna utamanya apa?'
        kinds = {d['kind']: d['status'] for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}
        assert 'final_report' not in kinds and 'test_plan' not in kinds  # nobody writes about code that does not exist

        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Hijau.'}).status_code == 202
        wait_execution(client, task_id, 'awaiting_approval')
        assert 'Hijau.' in world.programmer.requests[-1].messages[-1].content
        events = [e['phase'] for e in client.get(f'/v0/tasks/{task_id}/execution-events', headers=headers()).json()['items']]
        assert events.count('design_approval') == 1  # the approved design is not asked for again


def test_a_resume_gives_the_programmer_the_design_and_not_old_reports(world):
    world.programmer = scripted(CONCERN, CONCERN, CONCERN, CLEAN)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        to_design_gate(client, world, task_id)
        stopped(client, task_id, 'quality_gate')
        world.authors['lead'] = world.lead.router
        with Database(world.settings.database) as db:
            run_id = RunStore(db).latest(task_id)['run_id']
            db.conn.execute("UPDATE api_documents SET content='LAPORAN-LAMA' WHERE run_id=? AND stage='reporting'", (run_id,))
            db.conn.execute("INSERT OR IGNORE INTO api_documents SELECT 'doc_old',run_id,task_id,agent,'reporting','final_report',title,source,"
                            "'generated','LAPORAN-LAMA',content_sha256,reason,model_id,created_at FROM api_documents WHERE run_id=? LIMIT 1", (run_id,))
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Lanjut.'}).status_code == 202
        wait_execution(client, task_id, 'awaiting_approval')
        assert 'LAPORAN-LAMA' not in world.programmer.requests[-1].messages[-1].content
        assert '"approved_design"' in world.programmer.requests[-1].messages[-1].content


def test_quality_gate_explains_itself_and_takes_direction(world):
    world.programmer = scripted(CONCERN, CONCERN, CONCERN, CLEAN)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        to_design_gate(client, world, task_id)
        waiting = stopped(client, task_id, 'quality_gate')
        assert waiting['execution']['phase'] == 'blocked'
        told = messages(client, task_id)[-1]
        assert told['role'] == 'lead' and 'menahan' in told['message'] and told['questions']
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Perbaiki temuannya.'}).status_code == 202
        wait_execution(client, task_id, 'awaiting_approval')


def test_answering_is_refused_when_nothing_waits_for_an_answer(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        refused = client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'x'})
        assert refused.status_code == 409 and refused.json()['error']['code'] == 'clarification_not_pending'
        to_design_gate(client, world, task_id)
        wait_execution(client, task_id, 'awaiting_approval')
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'x'}).status_code == 409


# -- the conversation before a task exists ------------------------------------------------


def test_lead_asks_about_a_vague_instruction_before_any_task_exists(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        before = client.get(f'/v0/tasks?project_id={project_id}', headers=headers()).json()['items']
        world.lead_replies[:0] = [UNCLEAR, UNDERSTOOD]
        draft_id = client.post(draft_url(project_id), headers=headers(), json={'content': 'tambah tombol'}).json()['draft_id']
        assert draft_id.startswith('dft_')
        asked = draft_state(client, project_id, draft_id, 'need_answers')
        assert asked['messages'][-1]['questions'][0]['question'] == 'Tombol apa?'
        assert client.post(draft_url(project_id, draft_id) + '/confirm', headers=headers()).status_code == 409
        assert client.get(f'/v0/tasks?project_id={project_id}', headers=headers()).json()['items'] == before

        assert client.post(draft_url(project_id, draft_id) + '/messages', headers=headers(), json={'content': 'Email'}).status_code == 202
        ready = draft_state(client, project_id, draft_id, 'ready')
        assert ready['title'] == 'Tambah tombol kontak' and 'tombol kontak' in ready['brief']
        seen = world.lead.requests[-1].messages[-1].content
        assert '"Email"' in seen and '"project_spec"' in seen and '"tracked_files"' in seen


def test_confirming_creates_the_task_starts_it_and_the_lead_does_not_ask_again(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        world.lead_replies[:0] = [UNDERSTOOD]
        draft_id = client.post(draft_url(project_id), headers=headers(), json={'content': 'tombol kontak', 'priority': 'high'}).json()['draft_id']
        draft_state(client, project_id, draft_id, 'ready')
        calls = len(world.lead.requests)
        confirmed = client.post(draft_url(project_id, draft_id) + '/confirm', headers=headers())
        assert confirmed.status_code == 202, confirmed.text
        data = confirmed.json()
        task_id = data['task']['task_id']
        assert data['scheduled'] is True and data['task']['title'] == 'Tambah tombol kontak'
        assert data['task']['priority'] == 'high' and 'tombol kontak' in data['task']['description']
        wait_execution(client, task_id, 'design_approval')
        lead_calls = [r for r in world.lead.requests[calls:] if r.metadata.get('purpose') == 'lead_conversation']
        assert lead_calls == []
        thread = messages(client, task_id)
        assert [m['role'] for m in thread] == ['owner', 'lead']
        assert snapshot(client, task_id)['branch']['branch'].startswith('siamang/tambah-tombol-kontak-')

        again = client.post(draft_url(project_id, draft_id) + '/confirm', headers=headers()).json()
        assert again['task']['task_id'] == task_id and again['duplicate'] is True and again['scheduled'] is False
        assert messages(client, task_id) == thread
        assert client.get(draft_url(project_id), headers=headers()).json()['items'] == []


def test_a_failed_lead_turn_is_recorded_and_can_be_answered_again(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        world.lead_replies[:0] = [{'status': 'need_answers', 'message': 'x', 'questions': [], 'spec': ''}, UNDERSTOOD]
        draft_id = client.post(draft_url(project_id), headers=headers(), json={'content': 'x'}).json()['draft_id']
        assert draft_state(client, project_id, draft_id, 'failed')['error']
        client.post(draft_url(project_id, draft_id) + '/messages', headers=headers(), json={'content': 'coba lagi'})
        draft_state(client, project_id, draft_id, 'ready')


def test_drafts_are_scoped_gated_and_discardable(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        world.lead_replies[:0] = [UNCLEAR]
        draft_id = client.post(draft_url(project_id), headers=headers(), json={'content': 'x'}).json()['draft_id']
        draft_state(client, project_id, draft_id, 'need_answers')
        assert client.get(draft_url(project_id, draft_id), headers=headers('outsider')).status_code == 404
        assert client.post(draft_url(project_id, draft_id) + '/confirm', headers=headers('outsider')).status_code == 404
        assert client.post(draft_url(project_id), headers=headers('reader'), json={'content': 'x'}).status_code == 403
        assert client.post(draft_url(project_id), headers=headers(), json={'content': 'x', 'shell': 'y'}).status_code == 422
        assert client.get(draft_url(project_id, 'dft_missing'), headers=headers()).status_code == 404
        listed = client.get(draft_url(project_id), headers=headers()).json()['items']
        assert listed[0]['draft_id'] == draft_id
        assert client.post(draft_url(project_id, draft_id) + '/discard', headers=headers()).status_code == 200
        assert client.get(draft_url(project_id), headers=headers()).json()['items'] == []
        assert client.post(draft_url(project_id, draft_id) + '/messages', headers=headers(), json={'content': 'x'}).status_code == 409


def test_a_draft_needs_the_approved_spec(world):
    with TestClient(world.app()) as client:
        world.lead_replies[:0] = [{'status': 'need_answers', 'message': 'x', 'spec': '', 'questions': [{'question': 'q', 'options': []}]}]
        project_id = client.post('/v0/projects', headers=headers(), json={'name': 'L', 'repository': 'rafie/landing'}).json()['project_id']
        until(lambda: client.get(f'/v0/projects/{project_id}/intake', headers=headers()).json()['spec']['state'] == 'gathering', 'gathering')
        refused = client.post(draft_url(project_id), headers=headers(), json={'content': 'x'})
        assert refused.status_code == 409 and refused.json()['error']['code'] == 'spec_not_approved'


def test_office_shows_every_project_and_the_task_the_team_is_on(world):
    with TestClient(world.app()) as client:
        first = new_project(client, world)
        second = new_project(client, world, name='Kedua')
        finished, queued = task(client, first), task(client, second, request_id='req_second')
        board = client.get('/v0/office', headers=headers()).json()
        assert {p['project_id'] for p in board['items']} == {first, second} and board['current'] is None
        assert [t['task_id'] for p in board['items'] if p['project_id'] == second for t in p['tasks']] == [queued]
        assert 'root_path' not in json.dumps(board)
        assert client.get('/v0/office', headers=headers('outsider')).json()['items'] == []

        to_design_gate(client, world, finished)
        wait_execution(client, finished, 'awaiting_approval')
        board = client.get('/v0/office', headers=headers()).json()
        row = next(p for p in board['items'] if p['project_id'] == first)
        assert row['waiting'] == {'approval_required': 1} and row['tasks'][0]['execution']['wait_reason'] == 'approval_required'
        assert board['current'] is None


def test_office_names_the_running_task_and_hides_archived_projects(world):
    gate = threading.Event()

    def slow(model, request):
        gate.wait(10)
        return CLEAN

    world.programmer = make_model(slow)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        to_design_gate(client, world, task_id)
        until(lambda: client.get('/v0/office', headers=headers()).json()['current'], 'current')
        assert client.get('/v0/office', headers=headers()).json()['current'] == {'task_id': task_id, 'project_id': project_id}
        gate.set()
        wait_execution(client, task_id, 'awaiting_approval')
        with Database(world.settings.database) as db:
            ProjectRegistry(db).archive(project_id)
        assert client.get('/v0/office', headers=headers()).json()['items'] == []
