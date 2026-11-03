"""New project → Lead's questions → approved spec → task branch → pull request, with real Git.

Only GitHub's API is faked: clone, fetch, commit, and push run against a local bare repository.
"""
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app, repository_name
from siamang.api.runtime import RuntimeRefusal
from siamang.api.settings import APISettings, Principal
from siamang.repos.client import PullRequest, RepoClient, RepoError, RepoInfo
from siamang.repos.workspace import branch_name, read_latest, write_changes
from tests.api.test_http import TOKENS, headers
from tests.support.analyst import sample_project
from tests.support.loop import CLEAN
from tests.support.programmer import INSTRUCTION, change, make_model, reply

REPOSITORY = 'rafie/landing'
SECRET_DOC = 'PRD-RAHASIA-TIDAK-BOLEH-DI-REPO'


def git(*args, cwd):
    env = {'PATH': '/usr/bin:/bin:/opt/homebrew/bin', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
    return subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class FakeGitHub(RepoClient):
    """Real Git over file://, with GitHub's own API answered from memory."""

    def __init__(self, base):
        super().__init__(remote=f'file://{base}/{{}}.git', protocol='file')
        self.pulls, self.can_write, self.created, self.empty, self.workflow_runs = {}, True, [], False, {}

    def _run(self, argv, **kwargs):  # the local tests have no gh: drop the credential helper
        helper = argv.index('credential.helper=!gh auth git-credential') if 'credential.helper=!gh auth git-credential' in argv else None
        if helper is not None:
            argv = argv[:helper - 1] + argv[helper + 1:]
        return super()._run(argv, **kwargs)

    def info(self, repository):
        return RepoInfo(repository, None if self.empty else 'main', False, self.can_write)

    def ci_runs(self, repository, branch, commit_sha):
        return self.workflow_runs.get(commit_sha, [])

    def ci_failed_log(self, repository, run_id, limit=6_000):
        return 'langkah build gagal: ghp_RAHASIA0123456789012345678901234567'

    def identity(self):
        return 'rafie', '1+rafie@users.noreply.github.com'

    def find_pull_request(self, repository, branch):
        return self.pulls.get(branch)

    def open_pull_request(self, repository, branch, *, base, title, body):
        self.created.append(dict(branch=branch, base=base, title=title, body=body))
        pull = PullRequest(len(self.pulls) + 1, f'https://github.com/{repository}/pull/{len(self.pulls) + 1}', 'OPEN')
        self.pulls[branch] = pull
        return pull


class World:
    def __init__(self, tmp_path):
        self.base = tmp_path / 'remote'
        self.bare = self.base / (REPOSITORY + '.git')
        self.bare.parent.mkdir(parents=True)
        seed = sample_project(tmp_path / 'seed')
        git('init', '-b', 'main', cwd=seed)
        git('add', '-A', cwd=seed)
        git('commit', '-m', 'seed', cwd=seed)
        git('clone', '--bare', str(seed), str(self.bare), cwd=tmp_path)
        permissions = {'operator': {'read', 'submit', 'run', 'cancel', 'retry'}, 'approver': {'read', 'approve', 'submit'},
                       'reader': {'read'}, 'outsider': {'read', 'submit'}}
        self.settings = APISettings(tmp_path / 'data' / 'siamang.db', tuple(
            Principal(name, hashlib.sha256(token.encode()).hexdigest(), frozenset(permissions[name]),
                      frozenset() if name == 'outsider' else frozenset({'*'})) for name, token in TOKENS.items()),
            b'test-seal-material' * 3)
        self.github = FakeGitHub(self.base)
        self.lead_replies = []
        self.programmer = make_model(CLEAN)
        self.lead = make_model(self._lead, structured=True)
        self.authors = {'lead': self.lead.router, 'planner': make_model(self._docs, structured=True).router,
                        'analyst': make_model(self._docs, structured=True).router}

    def _lead(self, model, request):
        if 'status' in request.json_schema['properties']:
            return json.dumps(self.lead_replies.pop(0))
        return self._docs(model, request)

    @staticmethod
    def _docs(model, request):
        kinds = request.json_schema['properties']['documents']['items']['properties']['kind']['enum']
        return json.dumps({'documents': [{'kind': k, 'applicable': True, 'content': SECRET_DOC, 'reason': ''} for k in kinds]})

    def app(self):
        return create_app(self.settings, models=self.programmer.router, authors=self.authors, repos=self.github)

    def remote_branches(self):
        return git('for-each-ref', '--format=%(refname:short)', 'refs/heads', cwd=self.bare).split()


QUESTIONS = {'status': 'need_answers', 'message': 'Ada yang perlu saya tanyakan.', 'spec': '',
             'questions': [{'question': 'Cuma landing page?', 'options': ['Ya', 'Tidak']}]}
READY = {'status': 'ready', 'message': 'Spesifikasi siap.', 'questions': [], 'spec': '# Spesifikasi\nLanding page HTML.'}
TASK_READY = {'status': 'ready', 'message': 'Jelas.', 'questions': [], 'spec': ''}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def until(check, what, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('never: ' + what)


def intake(client, project_id):
    return client.get(f'/v0/projects/{project_id}/intake', headers=headers()).json()


def spec_state(client, project_id, state):
    return until(lambda: intake(client, project_id)['spec']['state'] == state and intake(client, project_id),
                 f'spec {state}')


def new_project(client, world, name='Landing'):
    world.lead_replies[:0] = [QUESTIONS, READY]
    response = client.post('/v0/projects', headers=headers(), json={'name': name, 'repository': REPOSITORY})
    assert response.status_code == 202, response.text
    project_id = response.json()['project_id']
    spec_state(client, project_id, 'gathering')
    assert client.post(f'/v0/projects/{project_id}/intake/messages', headers=headers(),
                       json={'content': 'Ya, HTML polos.'}).status_code == 202
    spec_state(client, project_id, 'pending_approval')
    assert client.post(f'/v0/projects/{project_id}/intake/approve', headers=headers('approver')).status_code == 200
    return project_id


def task(client, project_id, request_id='req_landing', **extra):
    response = client.post('/v0/tasks', headers=headers(), json={
        'project_id': project_id, 'description': INSTRUCTION, 'request_id': request_id, **extra})
    assert response.status_code == 202, response.text
    return response.json()['task']['task_id']


def snapshot(client, task_id):
    return client.get('/v0/tasks/' + task_id, headers=headers()).json()


def wait_execution(client, task_id, phase):
    def reached():
        current = snapshot(client, task_id)
        assert current['status'] != 'failed' and current['execution']['phase'] != 'blocked', current
        return current['execution']['phase'] == phase and current
    return until(reached, phase)


def deliver(client, world, task_id):
    """Run a task through both gates up to its pull request."""
    world.lead_replies.append(TASK_READY)
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    wait_execution(client, task_id, 'design_approval')
    assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                       json={'decision': 'approve'}).status_code == 200
    run_id = wait_execution(client, task_id, 'awaiting_approval')['execution']['run_id']
    decided = client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'approve'})
    assert decided.status_code == 200, decided.text
    return until(lambda: snapshot(client, task_id)['branch']['publication'] in ('published', 'failed')
                 and snapshot(client, task_id)['branch'], 'publication')


def test_lead_asks_first_and_the_spec_needs_the_owners_approval(world):
    with TestClient(world.app()) as client:
        world.lead_replies[:0] = [QUESTIONS, READY]
        project_id = client.post('/v0/projects', headers=headers(), json={
            'name': 'Landing', 'repository': f'https://github.com/{REPOSITORY}'}).json()['project_id']
        state = spec_state(client, project_id, 'gathering')
        assert state['messages'][0]['questions'][0]['question'] == 'Cuma landing page?'
        assert state['repository']['default_branch'] == 'main' and 'root_path' not in json.dumps(state)
        assert (world.settings.database.parent / 'workspaces' / project_id / '.git').is_dir()

        refused = client.post('/v0/tasks', headers=headers(), json={
            'project_id': project_id, 'description': 'x', 'request_id': 'req_early'})
        assert refused.status_code == 409 and refused.json()['error']['code'] == 'spec_not_approved'

        client.post(f'/v0/projects/{project_id}/intake/messages', headers=headers(), json={'content': 'Ya'})
        state = spec_state(client, project_id, 'pending_approval')
        assert 'Landing page HTML' in state['spec']['content']
        assert client.post('/v0/tasks', headers=headers(), json={
            'project_id': project_id, 'description': 'x', 'request_id': 'req_early2'}).status_code == 409
        assert client.post(f'/v0/projects/{project_id}/intake/approve', headers=headers()).status_code == 403
        assert client.post(f'/v0/projects/{project_id}/intake/approve', headers=headers('approver')).status_code == 200
        assert task(client, project_id)

        # the Lead saw the repository and the answers, as data
        seen = world.lead.requests[1].messages[-1].content
        assert '"tracked_files"' in seen and '"Ya"' in seen


def test_approved_task_reaches_a_pull_request_on_its_own_branch_only(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        main_before = git('rev-parse', 'main', cwd=world.bare)
        task_id = task(client, project_id)
        branch = deliver(client, world, task_id)

        assert branch['publication'] == 'published', branch
        assert branch['branch'].startswith('siamang/add-login-validation-to-auth-')
        assert branch['pr_url'].endswith('/pull/1') and len(branch['commit_sha']) == 40
        assert git('rev-parse', 'main', cwd=world.bare) == main_before
        assert git('rev-parse', branch['branch'], cwd=world.bare) == branch['commit_sha']
        assert git('log', '-1', '--format=%ae', branch['branch'], cwd=world.bare) == '1+rafie@users.noreply.github.com'
        assert world.github.created[0]['base'] == 'main'


def test_agent_documents_stay_in_siamang(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        branch = deliver(client, world, task_id)
        documents = client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']
        assert any(d['content'] == SECRET_DOC for d in documents)
        changed = git('diff', '--name-only', 'main', branch['branch'], cwd=world.bare).split()
        assert changed and not any('docs/siamang' in path for path in changed)
        blob = git('log', '-p', 'main..' + branch['branch'], cwd=world.bare)
        assert SECRET_DOC not in blob and SECRET_DOC not in world.github.created[0]['body']


def test_nothing_reaches_git_before_final_approval_or_after_rejection(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        world.lead_replies.append(TASK_READY)
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait_execution(client, task_id, 'design_approval')
        client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})
        run_id = wait_execution(client, task_id, 'awaiting_approval')['execution']['run_id']
        assert world.remote_branches() == ['main']
        client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'reject', 'reason': 'no'})
        assert snapshot(client, task_id)['branch']['publication'] == 'none'
        assert client.post(f'/v0/tasks/{task_id}/publish', headers=headers('approver')).status_code == 409
        assert world.remote_branches() == ['main'] and world.github.created == []


def _new_file(name):
    return reply([change(f'backend/routes/{name}.py', 'create', 'def extra():\n    return 1\n'),
                  change(f'tests/test_{name}.py', 'create', 'def test_extra():\n    assert True\n')])


def test_revision_joins_the_open_pull_request(world):
    answers = [CLEAN, _new_file('revisi')]
    world.programmer = make_model(lambda model, request: answers.pop(0))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        first = deliver(client, world, task(client, project_id))
        assert first['publication'] == 'published', first
        revision_id = task(client, project_id, 'req_revision', revises_task_id=first['task_id'])
        second = deliver(client, world, revision_id)
        assert second['publication'] == 'published', second
        assert second['branch'] == first['branch'] and second['pr_number'] == first['pr_number']
        assert second['commit_sha'] != first['commit_sha'] and len(world.github.created) == 1
        assert git('rev-parse', first['branch'], cwd=world.bare) == second['commit_sha']
        assert git('merge-base', '--is-ancestor', first['commit_sha'], second['commit_sha'], cwd=world.bare) == ''


def test_revision_after_merge_gets_a_fresh_branch_from_the_default(world):
    answers = [CLEAN, _new_file('revisi')]
    world.programmer = make_model(lambda model, request: answers.pop(0))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        first = deliver(client, world, task(client, project_id))
        world.github.pulls[first['branch']] = PullRequest(first['pr_number'], first['pr_url'], 'MERGED')
        git('update-ref', 'refs/heads/main', first['commit_sha'], cwd=world.bare)  # the merge
        revision_id = task(client, project_id, 'req_revision', revises_task_id=first['task_id'])
        second = deliver(client, world, revision_id)
        assert second['publication'] == 'published', second
        assert second['branch'] != first['branch'] and second['pr_number'] != first['pr_number']
        assert git('merge-base', '--is-ancestor', first['commit_sha'], second['commit_sha'], cwd=world.bare) == ''
        assert git('rev-parse', 'main', cwd=world.bare) == first['commit_sha']


def test_revision_base_must_be_published_and_of_the_same_project(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        unpublished = task(client, project_id)
        response = client.post('/v0/tasks', headers=headers(), json={
            'project_id': project_id, 'description': 'x', 'request_id': 'req_r1', 'revises_task_id': unpublished})
        assert response.status_code == 409 and response.json()['error']['code'] == 'revision_base_not_published'
        response = client.post('/v0/tasks', headers=headers(), json={
            'project_id': project_id, 'description': 'x', 'request_id': 'req_r2', 'revises_task_id': 'tsk_missing'})
        assert response.status_code == 404


def test_task_questions_wait_for_the_owner_then_resume(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        world.lead_replies[:0] = [QUESTIONS, TASK_READY]
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        waiting = wait_execution(client, task_id, 'clarification')
        assert waiting['status'] == 'waiting' and waiting['execution']['wait_reason'] == 'clarification_required'
        assert client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items'] == []
        assert client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'][0]['questions']

        reader = client.post(f'/v0/tasks/{task_id}/messages', headers=headers('reader'), json={'content': 'x'})
        assert reader.status_code == 403
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Ya'}).status_code == 202
        wait_execution(client, task_id, 'design_approval')
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'x'}).status_code == 409


def test_programmer_question_reaches_the_owner_and_the_answer_returns_to_it(world):
    question = reply([], status='waiting', summary='Tag sudah ada, perlu keputusan.', unknowns=['Ganti atau pertahankan?'])
    answers = [question, CLEAN]
    world.programmer = make_model(lambda model, request: answers.pop(0))
    closing = make_model(world._docs, structured=True)
    world.authors |= {'qa': closing.router, 'reviewer': closing.router}
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        world.lead_replies.append(TASK_READY)
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait_execution(client, task_id, 'design_approval')
        client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'})

        waiting = wait_execution(client, task_id, 'clarification')
        assert waiting['execution']['wait_reason'] == 'clarification_required'
        asked = client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'][-1]
        assert asked['message'] == 'Tag sudah ada, perlu keputusan.'
        assert asked['questions'][0]['question'] == 'Ganti atau pertahankan?'
        kinds = {d['kind'] for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}
        assert 'code_review' not in kinds and 'prd' in kinds  # no quota spent reviewing nothing
        assert closing.requests == []

        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Pertahankan.'}).status_code == 202
        wait_execution(client, task_id, 'awaiting_approval')
        second = world.programmer.requests[1].messages[-1].content
        assert 'Pertahankan.' in second and 'Ganti atau pertahankan?' in second
        documents = client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']
        assert len({d['kind'] for d in documents}) == len(documents) and any(d['kind'] == 'code_review' for d in documents)


def test_design_agents_read_the_repository_before_writing(world):
    planner = make_model(world._docs, structured=True)
    world.authors['planner'] = planner.router
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        world.lead_replies.append(TASK_READY)
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        wait_execution(client, task_id, 'design_approval')
        seen = planner.requests[0].messages[-1].content
        assert '"tracked_files"' in seen and '"file_samples"' in seen and 'Landing page HTML' in seen


def test_publish_failure_is_reported_and_can_be_retried(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        real = world.github.open_pull_request
        world.github.open_pull_request = lambda *a, **k: (_ for _ in ()).throw(RepoError('pull_request_failed'))
        task_id = task(client, project_id)
        branch = deliver(client, world, task_id)
        assert branch['publication'] == 'failed' and branch['error'] == 'pull_request_failed'
        assert client.post(f'/v0/tasks/{task_id}/publish', headers=headers()).status_code == 403
        world.github.open_pull_request = real
        # the commit already reached the branch: the retry must only open the pull request
        pushed = branch['commit_sha']
        assert pushed and git('rev-parse', branch['branch'], cwd=world.bare) == pushed
        assert client.post(f'/v0/tasks/{task_id}/publish', headers=headers('approver')).status_code == 202
        done = until(lambda: snapshot(client, task_id)['branch']['publication'] != 'publishing' and snapshot(client, task_id)['branch'], 'retry')
        assert done['publication'] == 'published', done
        assert done['commit_sha'] == pushed and done['pr_url'] and git('rev-parse', branch['branch'], cwd=world.bare) == pushed


@pytest.mark.parametrize('field, value, code', [('can_write', False, 'repository_not_writable')])
def test_clone_refuses_a_repository_that_cannot_be_written(world, field, value, code):
    setattr(world.github, field, value)
    with TestClient(world.app()) as client:
        project_id = client.post('/v0/projects', headers=headers(), json={
            'name': 'NoWrite', 'repository': REPOSITORY}).json()['project_id']
        state = until(lambda: intake(client, project_id)['repository']['state'] == 'failed' and intake(client, project_id), 'failed')
        assert state['repository']['error'] == code
        world.github.can_write = True
        world.lead_replies.append(QUESTIONS)
        assert client.post(f'/v0/projects/{project_id}/clone', headers=headers()).status_code == 202
        until(lambda: intake(client, project_id)['repository']['state'] == 'ready', 'ready')
        spec_state(client, project_id, 'gathering')


def test_new_project_needs_an_unrestricted_scope_and_a_real_repository(world):
    with TestClient(world.app()) as client:
        assert client.post('/v0/projects', headers=headers('outsider'), json={'name': 'x', 'repository': 'a/b'}).status_code == 403
        assert client.post('/v0/projects', headers=headers('reader'), json={'name': 'x', 'repository': 'a/b'}).status_code == 403
        for bad in ('https://evil.example/a/b', 'file:///etc/passwd', 'a/b/c', '-x/y', 'a', '../a/b', 'git@github.com:a/b.git'):
            response = client.post('/v0/projects', headers=headers(), json={'name': 'x', 'repository': bad})
            assert response.status_code == 422, bad
        assert client.post('/v0/projects', headers=headers(), json={'name': 'x', 'repository': 'a/b', 'root_path': '/etc'}).status_code == 422


@pytest.mark.parametrize('value, expected', [
    ('Rafie93/Rafie93.github.io', 'Rafie93/Rafie93.github.io'),
    ('https://github.com/Rafie93/Rafie93.github.io', 'Rafie93/Rafie93.github.io'),
    ('https://github.com/Rafie93/Rafie93.github.io.git', 'Rafie93/Rafie93.github.io'),
    ('https://github.com/a/b/', 'a/b'),
])
def test_repository_names_are_normalized(value, expected):
    assert repository_name(value) == expected


def test_repository_name_refusal_is_typed():
    with pytest.raises(RuntimeRefusal):
        repository_name('https://github.com.evil/a/b')


# --- the client's own limits, with real Git ------------------------------------------------


@pytest.fixture
def clone(world, tmp_path):
    root = tmp_path / 'clone'
    world.github.clone(REPOSITORY, root)
    return root


@pytest.mark.parametrize('branch', ['main', 'master', 'siamang/', 'siamang/../main', 'siamang/A', 'refs/heads/main',
                                    '--force', 'siamang/x y', 'siamang/x:main', '+siamang/x'])
def test_only_siamang_branches_can_be_prepared_or_pushed(world, clone, branch):
    with pytest.raises(RepoError) as caught:
        world.github.prepare(clone, branch, 'main')
    assert caught.value.code == 'invalid_branch'
    with pytest.raises(RepoError):
        world.github.commit_and_push(clone, branch, base='main', paths=('a',), message='m', author_name='n', author_email='e@e')
    assert world.remote_branches() == ['main']


def test_push_refuses_a_checkout_on_another_branch(world, clone):
    world.github.prepare(clone, 'siamang/one', 'main')
    (clone / 'x.txt').write_text('x')
    with pytest.raises(RepoError) as caught:
        world.github.commit_and_push(clone, 'siamang/two', base='main', paths=('x.txt',), message='m',
                                     author_name='n', author_email='e@e')
    assert caught.value.code == 'refused_wrong_branch' and world.remote_branches() == ['main']


def test_a_moved_remote_branch_is_not_overwritten(world, clone, tmp_path):
    world.github.prepare(clone, 'siamang/one', 'main')
    (clone / 'x.txt').write_text('x')
    world.github.commit_and_push(clone, 'siamang/one', base='main', paths=('x.txt',), message='m', author_name='n', author_email='e@e')
    moved = git('rev-parse', 'siamang/one', cwd=world.bare)
    other = tmp_path / 'other'
    world.github.clone(REPOSITORY, other)
    git('checkout', 'siamang/one', cwd=other)
    (other / 'y.txt').write_text('y')
    git('add', 'y.txt', cwd=other)
    git('commit', '-m', 'someone else', cwd=other)
    git('push', 'origin', 'siamang/one', cwd=other)
    moved = git('rev-parse', 'siamang/one', cwd=world.bare)
    (clone / 'z.txt').write_text('z')
    with pytest.raises(RepoError) as caught:
        world.github.commit_and_push(clone, 'siamang/one', base='main', paths=('z.txt',), message='m', author_name='n', author_email='e@e')
    assert caught.value.code == 'push_failed' and git('rev-parse', 'siamang/one', cwd=world.bare) == moved


def test_prepare_discards_leftovers_in_the_managed_clone(world, clone):
    (clone / 'junk.txt').write_text('junk')
    world.github.prepare(clone, 'siamang/one', 'main')
    assert not (clone / 'junk.txt').exists()


@pytest.mark.parametrize('base', ['--upload-pack=x', '-x', 'a..b', ''])
def test_base_branch_cannot_become_an_option(world, clone, base):
    with pytest.raises(RepoError):
        world.github.prepare(clone, 'siamang/one', base)


def test_errors_never_repeat_tool_output(world, tmp_path):
    with pytest.raises(RepoError) as caught:
        world.github.clone('rafie/does-not-exist', tmp_path / 'nope')
    assert str(caught.value) == 'clone_failed'


# --- applying a patch ----------------------------------------------------------------------


def _patch(changes):
    from siamang.programmer.patch import Patch
    return Patch('pch_x', 'tsk_x', 'prj_x', 'summary', tuple(changes))


def _change(path, *, operation='create', before='absent', before_hash=None, content='new\n'):
    from siamang.programmer.change import ProposedChange
    return ProposedChange(f'change_{sum(map(ord, path)) % 99:02d}', path, operation, 'step_01', 'reason',
                          after_content=None if operation == 'delete' else content, before=before, before_hash=before_hash)


def _sha(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def test_write_changes_creates_modifies_and_deletes(tmp_path):
    (tmp_path / 'a.txt').write_text('old')
    (tmp_path / 'gone.txt').write_text('bye')
    paths = write_changes(tmp_path, _patch([
        _change('sub/new.txt'),
        _change('a.txt', operation='modify', before='observed', before_hash=_sha('old')),
        _change('gone.txt', operation='delete', before='observed', before_hash=_sha('bye'))]))
    assert set(paths) == {'sub/new.txt', 'a.txt', 'gone.txt'}
    assert (tmp_path / 'sub/new.txt').read_text() == 'new\n' and (tmp_path / 'a.txt').read_text() == 'new\n'
    assert not (tmp_path / 'gone.txt').exists()


def test_a_stale_patch_writes_nothing(tmp_path):
    (tmp_path / 'a.txt').write_text('someone changed this')
    with pytest.raises(RepoError) as caught:
        write_changes(tmp_path, _patch([_change('first.txt'),
                                        _change('a.txt', operation='modify', before='observed', before_hash=_sha('old'))]))
    assert caught.value.code == 'patch_stale' and not (tmp_path / 'first.txt').exists()


def test_a_create_over_an_existing_file_is_stale(tmp_path):
    (tmp_path / 'a.txt').write_text('exists')
    with pytest.raises(RepoError) as caught:
        write_changes(tmp_path, _patch([_change('a.txt')]))
    assert caught.value.code == 'patch_stale' and (tmp_path / 'a.txt').read_text() == 'exists'


def test_a_symlink_cannot_carry_a_write_out_of_the_clone(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'link').symlink_to(outside)
    with pytest.raises(RepoError) as caught:
        write_changes(root, _patch([_change('link/evil.txt')]))
    assert caught.value.code == 'patch_path_refused' and list(outside.iterdir()) == []


def test_branch_names_are_safe_for_any_text():
    assert branch_name('tsk_01ABCDEF', 'Buat Landing Page!! ../../etc') == 'siamang/buat-landing-page-etc-abcdef'
    assert branch_name('tsk_01ABCDEF', '日本語') == 'siamang/task-abcdef'


def _clone_of(world, tmp_path):
    root = tmp_path / 'managed'
    world.github.clone(REPOSITORY, root)
    return root


def _push_to_main(world, tmp_path, name, content):
    """Someone else adds work to the repository, as a merged pull request would."""
    other = tmp_path / f'other-{name}'
    git('clone', str(world.bare), str(other), cwd=tmp_path)
    (other / name).write_text(content)
    git('add', '-A', cwd=other)
    git('commit', '-m', 'upstream', cwd=other)
    git('push', 'origin', 'HEAD:main', cwd=other)
    return git('rev-parse', 'HEAD', cwd=other)


def test_latest_view_sees_new_commits_without_moving_the_checkout(world, tmp_path):
    root = _clone_of(world, tmp_path)
    world.github.prepare(root, 'siamang/old-task', 'main')
    (root / 'task-only.txt').write_text('dari task lama')
    git('add', '-A', cwd=root)
    git('commit', '-m', 'task', cwd=root)
    before = git('rev-parse', 'HEAD', cwd=root), git('status', '--porcelain', cwd=root), git('branch', '--show-current', cwd=root)
    commit = _push_to_main(world, tmp_path, 'README.md', '# fitur baru dari main\n')

    view = read_latest(world.github, root, 'main')

    assert view['fetched_latest'] is True and view['revision'] == commit[:12]
    assert 'fitur baru dari main' in view['file_samples']['README.md']
    assert 'task-only.txt' not in view['tracked_files']
    assert before == (git('rev-parse', 'HEAD', cwd=root), git('status', '--porcelain', cwd=root), git('branch', '--show-current', cwd=root))
    assert 'fitur baru' not in (root / 'README.md').read_text()


def test_latest_view_says_when_it_could_not_fetch(world, tmp_path):
    root = _clone_of(world, tmp_path)
    known = git('rev-parse', 'origin/main', cwd=root)
    git('remote', 'set-url', 'origin', f'file://{tmp_path}/hilang.git', cwd=root)
    view = read_latest(world.github, root, 'main')
    assert view['fetched_latest'] is False and view['revision'] == known[:12] and view['tracked_files']


def test_latest_view_redacts_and_does_not_follow_symlinks(world, tmp_path):
    other = tmp_path / 'other'
    git('clone', str(world.bare), str(other), cwd=tmp_path)
    (other / 'README.md').unlink(missing_ok=True)
    (other / 'README.md').symlink_to('/etc/hosts')
    (other / 'package.json').write_text('{"token": "ghp_' + 'a' * 36 + '"}')
    git('add', '-A', cwd=other)
    git('commit', '-m', 'hostile', cwd=other)
    git('push', 'origin', 'HEAD:main', cwd=other)
    view = read_latest(world.github, _clone_of(world, tmp_path), 'main')
    assert 'README.md' in view['tracked_files'] and 'README.md' not in view['file_samples']
    assert 'ghp_' + 'a' * 36 not in view['file_samples']['package.json']


def test_a_branch_view_falls_back_when_the_branch_is_gone(world, tmp_path):
    root = _clone_of(world, tmp_path)
    assert read_latest(world.github, root, 'main', 'siamang/sudah-dihapus')['revision'] == git('rev-parse', 'origin/main', cwd=root)[:12]


def test_fetches_on_one_clone_never_overlap(world, tmp_path):
    root = _clone_of(world, tmp_path)
    running, peak, guard = [0], [0], threading.Lock()
    real = world.github._git

    def counting(*args, **kwargs):
        if 'fetch' not in args:
            return real(*args, **kwargs)
        with guard:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(.02)
        try:
            return real(*args, **kwargs)
        finally:
            with guard:
                running[0] -= 1

    world.github._git = counting
    threads = [threading.Thread(target=world.github.refresh, args=(root,)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 1


def test_the_lead_reads_commits_pushed_after_the_clone(world, tmp_path):
    with TestClient(world.app()) as client:
        world.lead_replies[:0] = [QUESTIONS, READY]
        project_id = client.post('/v0/projects', headers=headers(), json={'name': 'Landing', 'repository': REPOSITORY}).json()['project_id']
        spec_state(client, project_id, 'gathering')
        assert '"fitur-baru.txt"' not in world.lead.requests[0].messages[-1].content
        commit = _push_to_main(world, tmp_path, 'fitur-baru.txt', 'baru')
        assert client.post(f'/v0/projects/{project_id}/intake/messages', headers=headers(), json={'content': 'Ya'}).status_code == 202
        spec_state(client, project_id, 'pending_approval')
        seen = world.lead.requests[1].messages[-1].content
        assert '"fitur-baru.txt"' in seen and commit[:12] in seen and '"fetched_latest": true' in seen


def _lead_view_of_revision(client, world, project_id, task_id):
    before = len(world.lead.requests)
    world.lead_replies.append(QUESTIONS)
    response = client.post(f'/v0/projects/{project_id}/drafts', headers=headers(),
                           json={'content': 'Perbaiki lagi', 'revises_task_id': task_id})
    assert response.status_code in (200, 201, 202), response.text
    until(lambda: len(world.lead.requests) > before, 'lead turn')
    return world.lead.requests[-1].messages[-1].content


def test_a_revision_reads_its_open_pull_request_then_main_after_it_closes(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        first = task(client, project_id)
        branch = deliver(client, world, first)
        commit = git('rev-parse', branch['branch'], cwd=world.bare)
        main = git('rev-parse', 'main', cwd=world.bare)
        assert commit != main

        assert commit[:12] in _lead_view_of_revision(client, world, project_id, first)

        pull = world.github.pulls[branch['branch']]
        world.github.pulls[branch['branch']] = PullRequest(pull.number, pull.url, 'CLOSED')
        assert main[:12] in _lead_view_of_revision(client, world, project_id, first)
