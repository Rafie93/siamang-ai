"""Several Programmers on one approved task: real threads, real Git for the result, GitHub's API faked."""
import hashlib
import json
import threading
import time

import pytest

pytest.importorskip('fastapi')
pytestmark = pytest.mark.usefixtures('every_document')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.parallel import commit_groups, merge_patches
from siamang.config import ConfigError, load_config
from siamang.documents.packages import divide, owned, package_limit, parse_plan, plan_work, render, split_owns
from siamang.models.errors import ModelTimeoutError
from siamang.programmer.change import BeforeState, Operation, ProposedChange
from siamang.programmer.patch import Patch
from siamang.api.store import RunStore
from siamang.projects import ProjectRegistry
from siamang.storage import Database
from siamang.tasks import TaskEngine, TaskFailure
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
    def stopped():
        # One snapshot per look: three reads could pair a queued task's status with the phase of the run that started.
        s = snapshot(client, task_id)
        return s['status'] == 'waiting' and s['execution']['phase'] != 'queued' and s
    return until(stopped, 'a stop', seconds=30)


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
        keys = {p['key'] for p in current['packages']}
        with Database(world.settings.database) as db:
            calls = [dict(r) for r in db.conn.execute('SELECT * FROM api_model_calls WHERE task_id=?', (task_id,))]
            spans = [dict(r) for r in db.conn.execute('SELECT * FROM api_agent_activity WHERE task_id=?', (task_id,))]
        # Each Programmer's call, in its own thread, is counted toward the run and its own package.
        coding = [c for c in calls if c['purpose'] in ('programming', 'programming_tools')]
        assert len(coding) == 3 and {c['package'] for c in coding} == keys and all(c['run_id'] == run_id for c in coding)
        assert all(c['agent'] for c in calls)
        assert {(s['agent'], s['package']) for s in spans if s['package']} == {
            (agent, key) for agent in ('programmer', 'qa', 'reviewer') for key in keys}

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
    assert parse_plan(dict(DIVIDED, packages=[PKG(str(i), [f'p{i}/']) for i in range(11)])).packages == ()
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


WAVED = dict(stack={'backend': 'Laravel'}, undecided=[], contract='Kelas User tetap bernama User; semua rute tetap di bawah /api.',
             foundation=None,
             packages=[dict(PKG('Deps', ['deps.txt']), wave=1), dict(PKG('Models', ['models/']), wave=2),
                       dict(PKG('Routes', ['routes/']), wave=2), dict(PKG('Checks', ['checks/']), wave=3)])


def test_waves_run_in_order_each_wave_side_by_side_and_each_sees_the_one_before(tmp_path):
    world = ParallelWorld(tmp_path, WAVED)
    order, seen, work, lock = [], {}, {}, threading.Lock()
    side_by_side = threading.Barrier(2)  # Models and Routes only pass together: they are one wave

    def watch(title, wait=False):
        def hook(request):
            files = json.loads(request.messages[-1].content.split('\n', 1)[1])['files']
            with lock:
                order.append(title)
                seen[title] = [f['path'] for f in files]
                work[title] = owning(request)
            if wait:
                side_by_side.wait(10)
        return hook

    world.programmer = make_model(Team(script={'Deps': watch('Deps'), 'Models': watch('Models', True),
                                               'Routes': watch('Routes', True), 'Checks': watch('Checks')}))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        wait_execution(client, task_id, 'awaiting_approval')
        packages = snapshot(client, task_id)['packages']
    assert order[0] == 'Deps' and order[-1] == 'Checks' and set(order[1:3]) == {'Models', 'Routes'}
    assert [p['wave'] for p in packages] == [1, 2, 2, 3] and {p['state'] for p in packages} == {'passed'}
    assert 'deps.txt' in seen['Models'] and 'deps.txt' in seen['Routes']  # a later wave builds on the earlier result
    assert 'models/main.py' in seen['Checks'] and 'routes/main.py' in seen['Checks']
    assert 'models/main.py' not in seen['Routes']  # a neighbour of the same wave is not seen
    assert work['Models']['shared_contract'].startswith('Kelas User') and work['Models']['wave'] == 2
    assert any(line.startswith('Routes') for line in work['Models']['team'])
    assert any(line.startswith('Deps') for line in work['Checks']['finished_earlier'])


def test_a_package_that_times_out_twice_is_cut_in_two_and_the_halves_finish(tmp_path):
    plan = dict(DIVIDED, foundation=None, packages=[PKG('Backend', ['api/', 'web/']), PKG('Frontend', ['frontend/'])])
    world = ParallelWorld(tmp_path, plan)
    tries = []

    def too_big(request):
        tries.append(1)
        raise ModelTimeoutError('too slow')

    world.programmer = make_model(Team(script={'Backend': too_big}))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        wait_execution(client, task_id, 'awaiting_approval')
        packages = snapshot(client, task_id)['packages']
    assert len(tries) >= 2  # tried again before it was cut
    assert sorted(p['title'] for p in packages) == ['Backend (a)', 'Backend (b)', 'Frontend']
    assert {p['state'] for p in packages} == {'passed'}
    assert sorted(tuple(p['owns']) for p in packages if p['title'].startswith('Backend')) == [('api/',), ('web/',)]


def test_a_package_that_cannot_be_cut_and_keeps_timing_out_stops_for_the_owner(tmp_path):
    plan = dict(DIVIDED, foundation=None, packages=[PKG('Backend', ['one.py']), PKG('Frontend', ['frontend/'])])
    world = ParallelWorld(tmp_path, plan)

    def too_big(request):
        raise ModelTimeoutError('too slow')

    world.programmer = make_model(Team(script={'Backend': too_big}))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        stopped = waited(client, task_id)
    backend = next(p for p in stopped['packages'] if p['title'] == 'Backend')
    assert (backend['state'], backend['error'], backend['attempt']) == ('failed', 'programmer_model_timeout', 2)
    assert next(p for p in stopped['packages'] if p['title'] == 'Frontend')['state'] == 'passed'


def test_a_wave_waits_for_the_one_before_and_a_failed_wave_stops_the_next(tmp_path):
    world = ParallelWorld(tmp_path, WAVED)
    seen = []

    def broken(request):
        seen.append('Deps')
        raise ModelTimeoutError('too slow')

    def later(title):
        def hook(request):
            seen.append(title)
        return hook

    world.programmer = make_model(Team(script={'Deps': broken, 'Models': later('Models'), 'Routes': later('Routes'), 'Checks': later('Checks')}))
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        stopped = waited(client, task_id)
    assert set(seen) == {'Deps'}  # nothing was built on work that did not pass
    assert {p['title']: p['state'] for p in stopped['packages']}['Deps'] == 'failed'


def test_waves_may_share_a_file_but_one_wave_may_not():
    shared = dict(DIVIDED, foundation=None, packages=[dict(PKG('A', ['composer.json']), wave=1), dict(PKG('B', ['composer.json', 'app/']), wave=2)])
    plan = parse_plan(shared)
    assert [p.wave for p in plan.packages] == [1, 2]
    assert parse_plan(dict(shared, packages=[dict(PKG('A', ['composer.json']), wave=1), dict(PKG('B', ['composer.json']), wave=1)])) is None
    gaps = parse_plan(dict(shared, packages=[dict(PKG('A', ['a/']), wave=7), dict(PKG('B', ['b/']), wave=2)]))
    assert [(p.title, p.wave) for p in gaps.packages] == [('B', 1), ('A', 2)]  # sorted, renumbered without gaps
    assert 'wave' not in dict(DIVIDED, foundation=None)['packages'][0]
    assert parse_plan(DIVIDED).packages[0].wave == 0 and parse_plan(DIVIDED).packages[1].wave == 1


def test_the_contract_reaches_every_package_and_is_bounded():
    plan = parse_plan(dict(WAVED, contract='x' * 10_000))
    assert {len(p.contract) for p in plan.packages} == {1_800} and len(plan.contract) == 1_800
    assert 'Kontrak bersama' in render(parse_plan(WAVED))


def test_the_package_limit_grows_with_the_codebase_inside_a_ceiling():
    assert package_limit(0) == 4 and package_limit(150) == 7 and package_limit(10_000) == 10
    wide = dict(DIVIDED, foundation=None, packages=[PKG(str(i), [f'p{i}/']) for i in range(6)])
    assert parse_plan(wide, limit=4).packages == () and len(parse_plan(wide, limit=7).packages) == 6


def test_a_directory_is_cut_along_its_children_and_a_file_is_not_cut():
    names = [f'app/Models/m{i}.php' for i in range(6)] + [f'app/Http/h{i}.php' for i in range(5)] + ['app/Kernel.php', 'app/Console/c.php']
    first, second = split_owns(('app/',), names)
    assert set(first) | set(second) == {'app/Models/', 'app/Http/', 'app/Kernel.php', 'app/Console/'} and not set(first) & set(second)
    assert split_owns(('a/', 'b/', 'c/'), []) == (('a/', 'b/'), ('c/',))
    assert split_owns(('one.php',), names) is None and split_owns(('empty/',), names) is None and split_owns((), names) is None


def test_a_database_from_before_waves_gets_them_without_losing_a_package(tmp_path):
    import sqlite3

    from siamang.api.store import RunStore
    from siamang.storage import Database
    path = tmp_path / 'old.db'
    old = sqlite3.connect(path)
    old.execute('''CREATE TABLE api_work_packages (
        run_id TEXT NOT NULL, key TEXT NOT NULL, position INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
        specialty TEXT NOT NULL, instructions TEXT NOT NULL, owns TEXT NOT NULL, state TEXT NOT NULL, phase TEXT,
        iteration INTEGER, summary TEXT, questions TEXT, error TEXT, patch TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, key)) STRICT''')
    for position, (key, kind) in enumerate([('foundation', 'foundation'), ('pkg-1', 'package')]):
        old.execute("INSERT INTO api_work_packages VALUES ('r',?,?,?,?,'','x','[]','passed',NULL,NULL,NULL,NULL,NULL,NULL,'t')", (key, position, kind, key))
    old.commit()
    old.close()
    with Database(path) as db:
        store = RunStore(db)
        store.initialize()
        store.initialize()  # again: migrating twice is harmless
        assert [(r['kind'], r['wave'], r['attempt'], r['contract']) for r in store.work_packages('r')] == [('foundation', 0, 1, ''), ('package', 1, 1, '')]


def division_doc(client, task_id):
    return {d['kind']: d for d in client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']}['work_packages']


class Refusing(ParallelWorld):
    """The first ``bad`` answers to the division are ones the code refuses."""

    def __init__(self, tmp_path, answers):
        super().__init__(tmp_path)
        self.answers, self.asked = answers, []

    def _analyst(self, model, request):
        if request.metadata.get('purpose') == 'work_packages':
            self.asked.append(json.loads(request.messages[-1].content.split('\n', 1)[1]))
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            if isinstance(answer, Exception):
                raise answer
            return json.dumps(answer)
        return self._docs(model, request)


def test_a_refused_division_is_asked_again_and_told_why(tmp_path):
    unsafe = dict(DIVIDED, foundation=None, packages=[PKG('A', ['.env.production']), PKG('B', ['b/'])])
    world = Refusing(tmp_path, [unsafe, DIVIDED])
    with TestClient(world.app()) as client:
        task_id = to_design(client, world, new_project(client, world))
        doc = division_doc(client, task_id)
        parts = [p for p in snapshot(client, task_id)['design_parts'] if p['stage'] == 'division']
    assert doc['status'] == 'generated'
    assert '.env.production' in world.asked[1]['rejected_division'] and 'rejected_division' not in world.asked[0]
    assert [(p['attempt'], p['state']) for p in parts] == [(1, 'failed'), (2, 'done')] and '.env.production' in parts[0]['reason']


def test_a_division_that_keeps_failing_says_so_and_never_claims_one_programmer_is_enough(tmp_path):
    world = Refusing(tmp_path, [dict(DIVIDED, foundation=None, packages=[PKG('A', ['x/']), PKG('B', ['x/'])])])
    with TestClient(world.app()) as client:
        task_id = to_design(client, world, new_project(client, world))
        doc = division_doc(client, task_id)
    assert doc['status'] == 'failed' and 'gagal setelah 2 percobaan' in doc['reason'] and 'x/' in doc['reason']
    assert 'menilai cukup' not in doc['reason']


def test_one_programmer_is_reported_as_the_analysts_own_judgement(tmp_path):
    world = Refusing(tmp_path, [dict(stack={}, undecided=[], contract='', foundation=None, packages=[],
                                     single_programmer_reason='Hanya 2 file berubah.')])
    with TestClient(world.app()) as client:
        task_id = to_design(client, world, new_project(client, world))
        doc = division_doc(client, task_id)
    assert doc['status'] == 'not_applicable' and doc['reason'] == 'Analyst menilai cukup satu Programmer: Hanya 2 file berubah.'
    assert len(world.asked) == 1


def test_only_the_division_is_asked_again_at_the_design_gate(tmp_path):
    world = Refusing(tmp_path, [TimeoutError('x'), TimeoutError('x'), DIVIDED])
    with TestClient(world.app()) as client:
        task_id = to_design(client, world, new_project(client, world))
        assert division_doc(client, task_id)['status'] == 'failed'
        written = len(world.analyst.requests)
        design = client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']
        srs = next(d for d in design if d['kind'] == 'srs')

        assert client.post(f'/v0/tasks/{task_id}/work-division', headers=headers()).status_code == 202
        wait_execution(client, task_id, 'design_approval')
        until(lambda: snapshot(client, task_id)['packages'], 'packages')
        doc = division_doc(client, task_id)
        after = client.get(f'/v0/tasks/{task_id}/documents', headers=headers()).json()['items']
        parts = [p for p in snapshot(client, task_id)['design_parts'] if p['stage'] == 'division']
    assert doc['status'] == 'generated' and len(world.analyst.requests) == written + 1  # one call, not the whole design
    assert next(d for d in after if d['kind'] == 'srs')['content_sha256'] == srs['content_sha256']
    assert [p['attempt'] for p in parts] == [1, 2, 3]


def test_redividing_is_refused_when_no_design_is_waiting(world):
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = task(client, project_id)
        assert client.post(f'/v0/tasks/{task_id}/work-division', headers=headers()).status_code == 409


def test_a_retry_keeps_the_division_without_asking_for_it_again(tmp_path):
    world = Refusing(tmp_path, [DIVIDED])
    with TestClient(world.app()) as client:
        task_id = to_design(client, world, new_project(client, world))
        assert len(world.asked) == 1
        with Database(world.settings.database) as db:
            store, engine = RunStore(db), TaskEngine(db, ProjectRegistry(db))
            row = store.latest(task_id)
            with db.transaction():
                engine.start(task_id)
                engine.fail(task_id, TaskFailure('X', 'x', retryable=True))
                store.update(row['run_id'], state='failed', phase='failed')
        client.post(f'/v0/tasks/{task_id}/retry', headers=headers())
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        until(lambda: snapshot(client, task_id)['execution']['run_id'] != row['run_id']
              and snapshot(client, task_id)['execution']['phase'] == 'design_approval', 'the second gate')
        packages = snapshot(client, task_id)['packages']
    assert len(world.asked) == 1 and [p['title'] for p in packages] == ['Fondasi', 'Backend', 'Frontend']
    assert {p['state'] for p in packages} == {'pending'}


def test_sending_a_changed_diagram_works_every_package_again_since_what_passed_used_the_old_one(world):
    attempts = []
    outside = reply([dict(path='frontend/stolen.py', operation='create', step_id='step_03', reason='x', content='x = 1\n')])

    def trespass(request):
        attempts.append(request)
        return outside if len(attempts) <= 2 else None

    team = Team(script={'Backend': trespass})
    world.programmer = make_model(team)
    with TestClient(world.app()) as client:
        project_id = new_project(client, world)
        task_id = to_design(client, world, project_id)
        approve_design(client, task_id)
        assert waited(client, task_id)['execution']['wait_reason'] == 'quality_gate'
        content = '```mermaid\nclassDiagram\n  class Lama\n```'
        sha = hashlib.sha256(content.encode()).hexdigest()
        with Database(world.settings.database) as db:
            db.conn.execute("UPDATE api_documents SET content=?, content_sha256=? WHERE task_id=? AND kind='class_diagram'", (content, sha, task_id))
        assert client.post(f'/v0/tasks/{task_id}/documents/class_diagram/diagrams/0', headers=headers('approver'),
                           json={'source': 'classDiagram\n  class Baru', 'base_sha256': sha}).status_code == 200
        before = list(team.seen)
        assert client.post(f'/v0/tasks/{task_id}/design-edits/send', headers=headers('approver'), json={}).status_code == 202
        done = wait_execution(client, task_id, 'awaiting_approval')
        assert {p['state'] for p in done['packages']} == {'passed'}
        assert sorted(team.seen[len(before):]) == ['Backend', 'Fondasi', 'Frontend']
