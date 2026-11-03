"""Several Programmers on one approved task: real threads, real Git for the result, GitHub's API faked."""
import json
import threading
import time

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.parallel import commit_groups, merge_patches
from siamang.config import ConfigError, load_config
from siamang.documents.packages import owned, parse_plan, plan_work, render
from siamang.programmer.change import BeforeState, Operation, ProposedChange
from siamang.programmer.patch import Patch
from tests.api.test_http import headers
from tests.api.test_repos import World, git, new_project, snapshot, task, until, wait_execution
from tests.support.programmer import make_model, reply

TASK_READY = {'status': 'ready', 'message': 'Jelas.', 'questions': [], 'spec': ''}
PKG = lambda title, owns, instructions='kerjakan': dict(title=title, specialty=title.lower(), instructions=instructions, owns=owns)  # noqa: E731
DIVIDED = dict(stack={'backend': 'Express', 'frontend': 'Vue', 'database': 'PostgreSQL'}, undecided=[],
               foundation=PKG('Fondasi', ['README-mono.md', '.github/workflows/', 'contract/']),
               packages=[PKG('Backend', ['backend/']), PKG('Frontend', ['frontend/'])])


def owning(request):
    return json.loads(request.messages[-1].content.split('\n', 1)[1])['request']['input']['work_package']


class Team:
    """A Programmer that writes a file inside what the work package owns, and shows how many run at once."""

    def __init__(self, barrier=None, script=None):
        self.running = self.peak = 0
        self.lock, self.barrier, self.script, self.seen = threading.Lock(), barrier, script or {}, []

    def __call__(self, model, request):
        package = owning(request)
        self.seen.append(package['title'])
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            if self.barrier and package['title'] != 'Fondasi':
                self.barrier.wait(10)
            answer = self.script.get(package['title'])
            if callable(answer):
                answer = answer(request)
            if answer is not None:
                return answer
            own = package['owned_paths'][0]
            names = [own] if not own.endswith('/') else [own + 'main.py', own + 'test_main.py']
            return reply([dict(path=path, operation='create', step_id='step_03', reason=package['title'],
                               content=f'def run():\n    return {path!r}\n') for path in names], summary=package['title'])
        finally:
            with self.lock:
                self.running -= 1


class ParallelWorld(World):
    def __init__(self, tmp_path, plan=DIVIDED):
        super().__init__(tmp_path)
        self.plan = plan
        self.analyst = make_model(self._analyst, structured=True)
        self.authors['analyst'] = self.analyst.router

    def _analyst(self, model, request):
        if request.metadata.get('purpose') == 'work_packages':
            return json.dumps(self.plan)
        return self._docs(model, request)


@pytest.fixture
def world(tmp_path):
    return ParallelWorld(tmp_path)


def to_design(client, world, project_id):
    task_id = task(client, project_id)
    world.lead_replies.append(TASK_READY)
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    wait_execution(client, task_id, 'design_approval')
    return task_id


def approve_design(client, task_id):
    assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'}).status_code == 200


def waited(client, task_id):
    return until(lambda: snapshot(client, task_id)['status'] == 'waiting' and snapshot(client, task_id)['execution']['phase'] != 'queued'
                 and snapshot(client, task_id), 'a stop', seconds=30)


def test_packages_run_at_the_same_time_after_the_foundation_and_reach_one_pull_request(world):
    team = Team(barrier=threading.Barrier(2))  # only passes if both Programmers are inside the model at once
    world.programmer = make_model(team)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        documents = {d['kind']: d for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}
        assert 'backend/' in documents['work_packages']['content'] and 'PostgreSQL' in documents['work_packages']['content']
        assert world.programmer.requests == []
        assert [p['state'] for p in snapshot(client, task_id)['packages']] == ['pending'] * 3

        approve_design(client, task_id)
        run_id = wait_execution(client, task_id, 'awaiting_approval')['execution']['run_id']
        assert team.seen[0] == 'Fondasi' and team.peak == 2 and set(team.seen[1:]) == {'Backend', 'Frontend'}
        current = snapshot(client, task_id)
        assert {p['state'] for p in current['packages']} == {'passed'} and sum(p['files'] for p in current['packages']) == 5

        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'approve'}).status_code == 200
        branch = until(lambda: snapshot(client, task_id)['branch']['publication'] == 'published' and snapshot(client, task_id)['branch'], 'published')
        name = branch['branch']
        assert sorted(git('diff', '--name-only', 'main', name, cwd=world.bare).split()) == ['README-mono.md', 'backend/main.py', 'backend/test_main.py', 'frontend/main.py', 'frontend/test_main.py']
        subjects = git('log', '--format=%s', 'main..' + name, cwd=world.bare).split('\n')
        assert subjects == ['Frontend', 'Backend', 'Fondasi']
        assert git('rev-parse', 'main', cwd=world.bare) != branch['commit_sha']
        assert len(world.github.created) == 1


def test_a_package_sees_the_foundation_but_not_its_neighbour(world):
    seen = {}

    def backend(request):
        seen.update(paths=[f['path'] for f in json.loads(request.messages[-1].content.split('\n', 1)[1])['files']])
        return None

    world.programmer = make_model(Team(script={'Backend': backend}))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        wait_execution(client, task_id, 'awaiting_approval')
    assert 'README-mono.md' in seen['paths'] and 'frontend/main.py' not in seen['paths']


def test_a_package_outside_its_paths_is_told_once_then_stopped_and_only_it_runs_again(world):
    outside = reply([dict(path='frontend/stolen.py', operation='create', step_id='step_03', reason='x', content='x = 1\n')])
    attempts = []

    def trespass(request):
        attempts.append(request)
        return outside if len(attempts) <= 2 else None

    team = Team(script={'Backend': trespass})
    world.programmer = make_model(team)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        stopped = waited(client, task_id)
        assert stopped['execution']['wait_reason'] == 'quality_gate'
        states = {p['title']: p['state'] for p in stopped['packages']}
        assert states == {'Fondasi': 'passed', 'Backend': 'failed', 'Frontend': 'passed'}
        assert 'allowed_paths' in attempts[1].messages[-1].content
        told = client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'][-1]
        assert 'Backend' in json.dumps(told)

        before = list(team.seen)
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Tetap di backend/.'}).status_code == 202
        wait_execution(client, task_id, 'awaiting_approval')
        assert team.seen[len(before):] == ['Backend']  # work that passed is kept
        assert 'Tetap di backend/.' in world.programmer.requests[-1].messages[-1].content


def test_a_package_question_reaches_the_owner(world):
    asks = reply([], status='waiting', summary='Perlu keputusan.', unknowns=['Pakai JWT atau session?'])
    answers = [asks]
    world.programmer = make_model(Team(script={'Backend': lambda r: answers.pop() if answers else None}))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        stopped = waited(client, task_id)
        assert stopped['execution']['wait_reason'] == 'clarification_required'
        asked = client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'][-1]
        assert asked['questions'][0]['question'] == 'Backend: Pakai JWT atau session?'
        assert client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'JWT'}).status_code == 202
        wait_execution(client, task_id, 'awaiting_approval')


def test_undecided_stack_stops_before_the_design_can_be_approved(tmp_path):
    world = ParallelWorld(tmp_path, plan=dict(stack={}, undecided=['Backend memakai apa?'], foundation=None, packages=[]))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        world.lead_replies.append(TASK_READY)
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        stopped = waited(client, task_id)
        assert stopped['execution']['wait_reason'] == 'clarification_required'
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'}).status_code == 409
        assert client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'][-1]['questions'][0]['question'] == 'Backend memakai apa?'


def test_small_work_stays_with_one_programmer(tmp_path):
    world = ParallelWorld(tmp_path, plan=dict(stack={}, undecided=[], foundation=None, packages=[]))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        assert snapshot(client, task_id)['packages'] == []
        documents = {d['kind']: d for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}
        assert documents['work_packages']['status'] == 'not_applicable'
        approve_design(client, task_id)
        wait_execution(client, task_id, 'awaiting_approval')


# -- what a model proposes is never trusted ---------------------------------------------------


@pytest.mark.parametrize('bad', [['../etc/'], ['/etc/passwd'], ['.git/'], ['.env'], ['a\\b'], [], ['x'] * 0, ['./../x']])
def test_unsafe_owned_paths_refuse_the_whole_division(bad):
    assert parse_plan(dict(DIVIDED, packages=[PKG('A', bad), PKG('B', ['b/'])])) is None


@pytest.mark.parametrize('a, b', [(['app/'], ['app/']), (['app/'], ['app/sub/']), (['app/x.py'], ['app/'])])
def test_two_packages_can_never_own_the_same_file(a, b):
    assert parse_plan(dict(DIVIDED, packages=[PKG('A', a), PKG('B', b)])) is None


def test_division_limits_and_failure_fall_back_to_one_programmer():
    assert parse_plan(dict(DIVIDED, packages=[PKG(str(i), [f'p{i}/']) for i in range(7)])).packages == ()
    assert parse_plan(dict(DIVIDED, foundation=None, packages=[PKG('A', ['a/'])])).packages == ()
    assert parse_plan('rubbish') is None and plan_work(None, {}) is None
    assert plan_work(make_model('bukan json', structured=True).router, {}) is None
    assert parse_plan(DIVIDED).packages[0].kind == 'foundation' and 'Express' in render(parse_plan(DIVIDED))
    assert owned('backend/a.py', ('backend/',)) and not owned('backend2/a.py', ('backend/',)) and owned('x.md', ('x.md',))


def change(position, path, operation, content, before=BeforeState.ABSENT, before_hash=None):
    return ProposedChange(f'change_{position:02d}', path, operation, 'step_03', 'r', after_content=content, before=before, before_hash=before_hash)


def test_merging_keeps_the_real_before_state_and_the_last_content():
    H = 'sha256:' + 'a' * 64
    first = Patch('pch_01M6DWXQ8MZ97TX1YYDJFSYM86', 'tsk_a', 'prj_a', 's', (change(1, 'a.py', Operation.CREATE, 'one'), change(2, 'gone.py', Operation.CREATE, 'x')))
    second = Patch('pch_01M6DWXQ8MZ97TX1YYDJFSYM87', 'tsk_a', 'prj_a', 's', (
        change(1, 'a.py', Operation.MODIFY, 'two', BeforeState.OBSERVED, H), change(2, 'gone.py', Operation.DELETE, None, BeforeState.OBSERVED, H),
        change(3, 'old.py', Operation.MODIFY, 'new', BeforeState.OBSERVED, H)))
    merged = merge_patches('tsk_a', 'prj_a', [first, second], 'gabungan')
    rows = {c.path: c for c in merged.changes}
    assert set(rows) == {'a.py', 'old.py'}
    assert rows['a.py'].operation is Operation.CREATE and rows['a.py'].before is BeforeState.ABSENT and rows['a.py'].after_content == 'two'
    assert rows['old.py'].operation is Operation.MODIFY and rows['old.py'].before_hash == H


# -- an empty repository and CI --------------------------------------------------------------


def test_an_empty_repository_gets_only_a_first_commit_to_build_on(tmp_path):
    world = ParallelWorld(tmp_path)
    git('init', '--bare', '-b', 'main', 'kosong.git', cwd=world.bare.parent)
    world.github.empty = True
    world.lead_replies[:0] = [dict(status='ready', message='ok', questions=[], spec='# S')]
    with TestClient(world.app()) as client:
        project_id = client.post('/v0/projects', headers=headers(), json={'name': 'Presensi', 'repository': 'rafie/kosong'}).json()['project_id']
        state = until(lambda: (r := client.get(f'/v0/projects/{project_id}/intake', headers=headers()).json())['spec']['state'] == 'pending_approval' and r, 'spec')
        assert state['repository']['default_branch'] == 'main'
    bare = world.bare.parent / 'kosong.git'
    assert git('log', '--format=%s', 'main', cwd=bare) == 'Initial commit' and git('ls-tree', '--name-only', 'main', cwd=bare) == 'README.md'


def test_seeding_refuses_a_repository_that_gained_a_branch(world):
    from siamang.repos.client import RepoError
    root = world.bare.parent.parent / 'c'
    world.github.clone('rafie/landing', root)
    with pytest.raises(RepoError) as refused:
        world.github.seed_empty(root, 'x', author_name='a', author_email='a@a')
    assert refused.value.code == 'repository_not_empty'


def publish(client, world, task_id):
    run_id = wait_execution(client, task_id, 'awaiting_approval')['execution']['run_id']
    client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'approve'})
    return until(lambda: snapshot(client, task_id)['branch']['publication'] == 'published' and snapshot(client, task_id)['branch'], 'published')


RUN = dict(id=7, name='CI', status='completed', conclusion='success', url='https://github.com/rafie/landing/actions/runs/7',
           jobs=[dict(name='backend', status='completed', conclusion='success')], artifacts=[dict(name='app-release.apk', size=1234, expired=False)])


def test_ci_results_and_artifacts_fill_the_measured_documents(world):
    world.programmer = make_model(Team())
    app = world.app()
    app.state.runtime.ci_poll_secs = .02
    with TestClient(app) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        branch = publish(client, world, task_id)
        world.github.workflow_runs[branch['commit_sha']] = [dict(RUN, status='in_progress', conclusion=None, artifacts=[])]
        until(lambda: (snapshot(client, task_id)['ci'] or {}).get('runs'), 'a running build')
        assert snapshot(client, task_id)['ci']['state'] == 'watching'
        world.github.workflow_runs[branch['commit_sha']] = [RUN]
        ci = until(lambda: snapshot(client, task_id)['ci']['state'] == 'success' and snapshot(client, task_id)['ci'], 'success')
        assert ci['runs'][0]['artifacts'][0]['name'] == 'app-release.apk'
        documents = {d['kind']: d for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}
        assert documents['ci_results']['status'] == 'generated' and documents['ci_results']['source'] == 'measured' and 'backend' in documents['ci_results']['content']
        assert 'app-release.apk' in documents['build_artifacts']['content'] and documents['build_artifacts']['model_id'] is None


def test_a_failed_build_keeps_its_log_without_credentials(world):
    world.programmer = make_model(Team())
    app = world.app()
    app.state.runtime.ci_poll_secs = .02
    with TestClient(app) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        branch = publish(client, world, task_id)
        world.github.workflow_runs[branch['commit_sha']] = [dict(RUN, conclusion='failure', artifacts=[])]
        ci = until(lambda: snapshot(client, task_id)['ci']['state'] == 'failed' and snapshot(client, task_id)['ci'], 'failed')
        assert 'langkah build gagal' in ci['log'] and 'ghp_RAHASIA' not in json.dumps(ci)
        documents = {d['kind']: d for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}
        assert documents['build_artifacts']['status'] == 'not_run'


def test_a_repository_without_a_workflow_says_so(world, monkeypatch):
    monkeypatch.setattr('siamang.api.runtime.CI_NO_WORKFLOW_SECS', .05)
    world.programmer = make_model(Team())
    app = world.app()
    app.state.runtime.ci_poll_secs = .02
    with TestClient(app) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        publish(client, world, task_id)
        until(lambda: snapshot(client, task_id)['ci']['state'] == 'none', 'none')


def test_parallel_setting_is_validated(tmp_path):
    config = tmp_path / 's.toml'
    base = '[providers.p]\ntype="ollama"\nbase_url="http://127.0.0.1:1"\n[models.m]\nprovider="p"\n'
    config.write_text(base + '[agents.programmer]\nmodel="m"\nparallel=2\n')
    assert load_config(config).agents['programmer'].parallel == 2
    config.write_text(base + '[agents.programmer]\nmodel="m"\nparallel=9\n')
    with pytest.raises(ConfigError):
        load_config(config)


def test_security_findings_can_never_be_turned_into_notes():
    from siamang.reviewer.policy import ReviewerPolicy
    assert ReviewerPolicy(advisory_codes=frozenset({'risk.dependency_change'}))
    for code in ('security.sensitive_area', 'qa.failed', 'alignment.unrelated_patch', 'maintainability.large_file'):
        with pytest.raises(ValueError):
            ReviewerPolicy(advisory_codes=frozenset({code}))


def test_a_subscription_limit_says_so_and_keeps_finished_work(tmp_path):
    from siamang.models.errors import RateLimitedError
    world = ParallelWorld(tmp_path)
    limited = [True]

    def backend(request):
        if limited[0]:
            raise RateLimitedError('limit reached')
        return None

    team = Team(script={'Backend': backend, 'Frontend': backend})
    world.programmer = make_model(team)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        stopped = waited(client, task_id)
        assert {p['title']: p['error'] for p in stopped['packages']} == {'Fondasi': None, 'Backend': 'rate_limited', 'Frontend': 'rate_limited'}
        told = client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items'][-1]
        assert 'Batas langganan' in told['message']
        limited[0] = False
        client.post(f'/v0/tasks/{task_id}/messages', headers=headers(), json={'content': 'Lanjutkan.'})
        wait_execution(client, task_id, 'awaiting_approval')
        assert team.seen.count('Fondasi') == 1
