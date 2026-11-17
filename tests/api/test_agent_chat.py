"""The owner asks one agent about a task or a project: it answers, and nothing in the work moves."""
import json
import sqlite3
import threading

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from tests.api.test_http import headers
from tests.api.test_repos import SECRET_DOC, TASK_READY, World, new_project, snapshot, task, until, wait_execution
from tests.support.programmer import make_model


def chatting(request):
    return set(request.json_schema['properties']) == {'message'}


class ChatWorld(World):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.gate = threading.Event()
        self.gate.set()
        self.answers = []
        self.planner = make_model(self._planner, structured=True)
        self.authors['planner'] = self.planner.router
        self.analyst = make_model(self._analyst, structured=True)
        self.authors['analyst'] = self.analyst.router

    def _planner(self, model, request):
        if chatting(request):
            self.gate.wait(10)
            answer = self.answers.pop(0) if self.answers else 'Jawaban planner.'
            if isinstance(answer, Exception):
                raise answer
            return json.dumps({'message': answer})
        return self._docs(model, request)

    def _analyst(self, model, request):
        if chatting(request):
            return json.dumps({'message': 'Saya memilih ini karena desainnya.'})
        return self._docs(model, request)


@pytest.fixture
def world(tmp_path):
    return ChatWorld(tmp_path)


def thread(client, path):
    return client.get(path, headers=headers()).json()


def answered(client, path, count):
    return until(lambda: (t := thread(client, path)) and len(t['items']) >= count and not t['thinking'] and t, 'answer')


def chat_requests(model):
    return [r for r in model.requests if chatting(r)]


def test_a_project_thread_answers_apart_from_the_intake_and_is_metered(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        intake_before = thread(client, f'/v0/projects/{project_id}/intake')['messages']
        path = f'/v0/projects/{project_id}/agents/planner/chat'
        assert thread(client, path) | {'items': []} == dict(project_id=project_id, task_id=None, agent='planner',
                                                             items=[], thinking=False, available=True)

        posted = client.post(path, headers=headers(), json={'content': 'Apa prioritas pertama?'})
        assert posted.status_code == 202 and posted.json()['state'] == 'thinking'
        items = answered(client, path, 2)['items']
        assert [(m['role'], m['message']) for m in items] == [('owner', 'Apa prioritas pertama?'), ('agent', 'Jawaban planner.')]

        # The Lead's intake never sees the chat, and the question reached the planner with the project's spec.
        assert thread(client, f'/v0/projects/{project_id}/intake')['messages'] == intake_before
        seen = chat_requests(world.planner)[0]
        assert 'Planner' in seen.system_prompt and 'Landing page HTML' in seen.messages[-1].content
        assert 'Apa prioritas pertama?' in seen.messages[-1].content

        # A second question carries the first answer along.
        client.post(path, headers=headers(), json={'content': 'Lalu?'})
        answered(client, path, 4)
        assert 'Jawaban planner.' in chat_requests(world.planner)[1].messages[-1].content

        with sqlite3.connect(world.settings.database) as conn:
            calls = conn.execute("SELECT agent, stage, project_id, task_id FROM api_model_calls WHERE stage='chat'").fetchall()
        assert calls == [('planner', 'chat', project_id, None)] * 2


def test_an_agent_answers_about_its_task_while_the_run_waits_and_the_run_does_not_move(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        world.lead_replies.append(TASK_READY)
        assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
        before = wait_execution(client, task_id, 'design_approval')['execution']

        path = f'/v0/tasks/{task_id}/agents/analyst/chat'
        assert client.post(path, headers=headers(), json={'content': 'Kenapa desainnya begini?'}).status_code == 202
        items = answered(client, path, 2)['items']
        assert items[-1]['message'] == 'Saya memilih ini karena desainnya.'

        # The analyst was shown what it wrote for this task, and the task's own thread stays apart from the project's.
        seen = json.loads(chat_requests(world.analyst)[0].messages[-1].content.split('\n', 1)[1])
        assert seen['task']['task_id'] == task_id and seen['your_documents']
        assert any(d.get('content') == SECRET_DOC for d in seen['your_documents'])
        stored = client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']
        assert sorted(d['kind'] for d in seen['your_documents']) == sorted(d['kind'] for d in stored if d['agent'] == 'analyst')
        assert any(d['agent'] != 'analyst' for d in stored)
        assert thread(client, f'/v0/projects/{project_id}/agents/analyst/chat')['items'] == []
        assert client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'] == []

        after = snapshot(client, task_id)['execution']
        assert (after['run_id'], after['state'], after['phase']) == (before['run_id'], before['state'], before['phase'])


def test_an_agent_without_a_model_or_unknown_is_refused(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        refused = client.post(f'/v0/projects/{project_id}/agents/designer/chat', headers=headers(), json={'content': 'Hai'})
        assert refused.status_code == 503 and refused.json()['error']['code'] == 'agent_has_no_model'
        assert thread(client, f'/v0/projects/{project_id}/agents/designer/chat')['available'] is False
        unknown = client.post(f'/v0/projects/{project_id}/agents/hacker/chat', headers=headers(), json={'content': 'Hai'})
        assert unknown.status_code == 404 and unknown.json()['error']['code'] == 'unknown_agent'
        assert client.post(f'/v0/projects/{project_id}/agents/planner/chat', headers=headers('reader'),
                           json={'content': 'Hai'}).status_code == 403
        assert client.get('/v0/projects/prj_missing/agents/planner/chat', headers=headers()).status_code == 404


def test_one_question_at_a_time_and_a_failure_is_recorded(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        path = f'/v0/projects/{project_id}/agents/planner/chat'
        world.gate.clear()
        assert client.post(path, headers=headers(), json={'content': 'Satu'}).status_code == 202
        assert thread(client, path)['thinking'] is True
        busy = client.post(path, headers=headers(), json={'content': 'Dua'})
        assert busy.status_code == 409 and busy.json()['error']['code'] == 'agent_busy'
        # Another agent's thread is not held up by it.
        assert client.post(f'/v0/projects/{project_id}/agents/analyst/chat', headers=headers(),
                           json={'content': 'Halo'}).status_code == 202
        world.gate.set()
        answered(client, path, 2)

        from siamang.models.errors import ModelTimeoutError
        world.answers.append(ModelTimeoutError('slow'))
        client.post(path, headers=headers(), json={'content': 'Tiga'})
        last = answered(client, path, 4)['items'][-1]
        assert last['role'] == 'agent' and last['error'] == 'timeout' and 'message' not in last
        # A failed turn is not shown to the agent as something it said.
        client.post(path, headers=headers(), json={'content': 'Empat'})
        answered(client, path, 6)
        conversation = json.loads(chat_requests(world.planner)[-1].messages[-1].content.split('\n', 1)[1])['conversation']
        assert [m['message'] for m in conversation] == ['Satu', 'Jawaban planner.', 'Tiga', 'Empat']
