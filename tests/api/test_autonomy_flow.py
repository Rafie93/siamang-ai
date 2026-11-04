"""Phase 29-30 through the real runtime: real Git, real approval service, only models and GitHub's API faked."""
import json
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api import autonomy as glue
from siamang.autonomy import AutonomyPolicy, Risk, RiskLevel
from siamang.repos.client import PullDetails, RepoError
from siamang.storage.database import Database
from tests.api.test_ci_repair import Scripted
from tests.api.test_http import headers
from tests.api.test_repos import TASK_READY, World, git, new_project, snapshot, task, until, wait_execution
from tests.api.test_tools_recovery_flow import Engine, verifier
from tests.support.loop import CLEAN
from tests.support.programmer import change, make_model, reply
from tests.support.reviewer import TEST_VALIDATION, VALIDATION, clean_changes

BRIEF = 'Add validation to user'  # names a Python file of the sample project, and nothing sensitive
RISKY_BRIEF = 'Add login validation to auth'
CONCERN = reply([change('scripts/banana.py', 'create', 'x = 1\n')])
QUESTION = 'Halaman ini memakai HTML atau React?'
ASKS = reply([], status='waiting', summary='Butuh keputusan.', unknowns=[QUESTION])
QUOTE = 'Landing page HTML.'
GOOD_ANSWER = dict(index=0, action='answer', answer='HTML polos.', source='specification', quote=QUOTE, reason='')


@pytest.fixture
def world(tmp_path):
    world = World(tmp_path)
    world.answers, world.steps = [], []

    def lead(model, request):
        properties = request.json_schema['properties']
        if 'answers' in properties:
            return json.dumps(dict(answers=world.answers.pop(0)))
        if 'next_step' in properties:
            return json.dumps(world.steps.pop(0))
        return world._lead(model, request)
    world.lead = make_model(lead, structured=True)
    world.authors['lead'] = world.lead.router

    def analyst(model, request):
        if 'packages' in request.json_schema['properties']:  # one Programmer is enough: a division that failed would hold the gate
            return json.dumps(dict(stack=[], undecided=[], contract='', foundation=None, packages=[], single_programmer_reason='Satu file.'))
        return world._docs(model, request)
    world.authors['analyst'] = make_model(analyst, structured=True).router
    return world


def client_of(world, *, level=0, lead=False, engine=None, **policy):
    app = world.app()
    runtime = app.state.runtime
    runtime.ci_poll_secs = .01
    runtime._autonomy = AutonomyPolicy(level=level, lead=lead, **policy)
    runtime._verifier = verifier(engine) if engine else None
    return TestClient(app)


def begin(client, world, description=BRIEF):
    task_id = task(client, new_project(client, world), description=description)
    world.lead_replies.append(TASK_READY)
    assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
    return task_id


def approve_design(client, task_id):
    wait_execution(client, task_id, 'design_approval')
    assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'), json={'decision': 'approve'}).status_code == 200


def log(client, task_id, gate=None):
    items = client.get(f'/v0/tasks/{task_id}/autonomy', headers=headers()).json()['items']
    return [d for d in items if gate is None or d['gate'] == gate]


def messages(client, task_id):
    return client.get(f'/v0/tasks/{task_id}/messages', headers=headers()).json()['items']


def said(client, task_id):
    return ' '.join(m.get('message', '') for m in messages(client, task_id))


def stopped(client, task_id, reason):
    return until(lambda: snapshot(client, task_id)['execution']['wait_reason'] == reason and snapshot(client, task_id), reason)


def published(client, task_id):
    return until(lambda: snapshot(client, task_id)['branch']['publication'] in ('published', 'failed') and snapshot(client, task_id)['branch'], 'publication')


def run_row(world, task_id):
    from siamang.api.store import RunStore
    with Database(world.settings.database) as db:
        return RunStore(db).latest(task_id)


# --- Phase 29: the gates ------------------------------------------------------------------------------


def test_level_0_changes_nothing_but_still_records_the_risk(world):
    with client_of(world) as client:
        task_id = begin(client, world)
        approve_design(client, task_id)
        wait_execution(client, task_id, 'awaiting_approval')
        assert [(d['gate'], d['outcome'], d['risk'], d['level']) for d in log(client, task_id)] == [
            ('design', 'held', 'low', 0), ('publish', 'held', 'low', 0)]
        assert not said(client, task_id)  # the normal course of things needs no explanation
        assert not world.github.created


def test_level_1_passes_a_low_risk_design_under_its_own_name_and_stops_at_the_result(world):
    with client_of(world, level=1) as client:
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        assert log(client, task_id, 'design')[0]['outcome'] == 'auto'
        with Database(world.settings.database) as db:
            actor = db.conn.execute('SELECT actor FROM api_design_decisions').fetchone()['actor']
        assert actor == 'policy.autonomy' and not world.github.created


def test_a_high_risk_brief_waits_at_the_design_and_says_why(world):
    with client_of(world, level=3) as client:
        task_id = begin(client, world, RISKY_BRIEF)
        wait_execution(client, task_id, 'design_approval')
        assert log(client, task_id, 'design')[0]['risk'] == 'high'
        assert 'autentikasi' in said(client, task_id)
        assert world.programmer.requests == []


def test_rejecting_a_held_design_still_works(world):
    with client_of(world, level=1) as client:
        task_id = begin(client, world, RISKY_BRIEF)
        wait_execution(client, task_id, 'design_approval')
        assert client.post(f'/v0/tasks/{task_id}/design-decision', headers=headers('approver'),
                           json={'decision': 'reject', 'reason': 'Tidak jadi.'}).status_code == 200
        assert snapshot(client, task_id)['status'] == 'cancelled'


def test_level_2_reaches_a_pull_request_without_stopping_and_names_who_approved(world):
    with client_of(world, level=2, engine=Engine(0)) as client:
        task_id = begin(client, world)
        branch = published(client, task_id)
        assert branch['publication'] == 'published' and snapshot(client, task_id)['status'] == 'completed'
        assert run_row(world, task_id)['decision_actor'] == 'policy.autonomy'
        assert [(d['gate'], d['outcome']) for d in log(client, task_id)] == [('design', 'auto'), ('publish', 'auto')]
        assert all(d['seal_valid'] for d in log(client, task_id))
        body = world.github.created[0]['body']
        assert 'policy.autonomy' in body and 'setelah disetujui operator' not in body and 'Test di sandbox: lulus' in body
        assert git('rev-parse', 'main', cwd=world.bare) != branch['commit_sha']  # the default branch is untouched
        assert json.loads(run_row(world, task_id)['decision'])['approver_id'] == 'policy.autonomy'


def test_a_person_approving_is_still_credited_as_a_person(world):
    with client_of(world, engine=Engine(0)) as client:
        task_id = begin(client, world)
        approve_design(client, task_id)
        run_id = wait_execution(client, task_id, 'awaiting_approval')['execution']['run_id']
        assert client.post('/v0/approvals/' + run_id, headers=headers('approver'), json={'decision': 'approve'}).status_code == 200
        published(client, task_id)
        assert 'setelah disetujui operator' in world.github.created[0]['body']


def test_level_2_without_a_test_that_ran_waits_for_the_owner(world):
    with client_of(world, level=2) as client:
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        assert log(client, task_id, 'publish')[0]['outcome'] == 'held' and 'bukti eksekusi' in said(client, task_id)
        assert not world.github.created


def test_a_build_alone_is_not_a_test(world):
    from siamang.sandbox import SandboxPolicy, SandboxService
    from siamang.verification import Check, VerificationService
    with client_of(world, level=2) as client:
        client.app.state.runtime._verifier = VerificationService(SandboxService(Engine(0), SandboxPolicy('img')), (Check('build', ('make',)),))
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        assert log(client, task_id, 'publish')[0]['outcome'] == 'held'


@pytest.mark.parametrize('changes,reason', [
    ([change(VALIDATION, 'create', 'def valid(user):\n    return bool(user.password)\n'), clean_changes()[1]], 'autentikasi'),
    ([change(VALIDATION, 'create', 'import subprocess\n\n\ndef valid(user):\n    return bool(user)\n'), clean_changes()[1]], 'eksekusi'),
    ([change(VALIDATION, 'create', 'SQL = "ALTER TABLE users ADD x"\n\n\ndef valid(user):\n    return bool(user)\n'), clean_changes()[1]], 'skema'),
])
def test_a_low_risk_brief_cannot_carry_a_risky_patch_through(world, changes, reason):
    world.programmer = make_model(reply(changes))
    with client_of(world, level=2, engine=Engine(0)) as client:
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        held = log(client, task_id, 'publish')[0]
        assert (held['outcome'], held['risk']) == ('held', 'high') and reason in said(client, task_id)
        assert not world.github.created


def test_a_failing_test_opens_no_pull_request(world):
    with client_of(world, level=3, engine=Engine(1)) as client:
        task_id = begin(client, world)
        stopped(client, task_id, 'quality_gate')
        assert not world.github.created and not log(client, task_id, 'publish')


def test_an_advisors_compromise_holds_the_pull_request(world):
    from siamang.recovery import RecoveryPolicy
    world.advice = [{'action': 'change_approach', 'approach': 'lewat fungsi murni', 'subtasks': [], 'compromise': 'tanpa pesan error per field'}]
    lead = world.lead

    def advised(model, request):
        return json.dumps(world.advice.pop(0)) if 'action' in request.json_schema['properties'] else lead.provider.reply(model, request)
    world.authors['lead'] = make_model(advised, structured=True).router
    with client_of(world, level=2, engine=Engine(1, 0)) as client:
        client.app.state.runtime._recovery = RecoveryPolicy(retries=0, advisor_rounds=1, replans=0)
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        assert log(client, task_id, 'publish')[0]['outcome'] == 'held' and 'tanpa pesan error' in said(client, task_id)


@pytest.mark.parametrize('path,content,operation', [
    ('tests/test_a.py', 'import pytest\n\n@pytest.mark.skip\ndef test_a(): ...\n', 'create'), ('tests/test_a.py', None, 'delete')])
def test_a_patch_that_silences_a_test_is_high_risk_wherever_it_is(tmp_path, path, content, operation):
    (tmp_path / 'tests').mkdir()
    (tmp_path / path).write_text('def test_a():\n    assert 1\n')
    patch = SimpleNamespace(changes=(SimpleNamespace(path=path, operation=operation, after_content=content),))
    risk = glue.patch_risk(patch, tmp_path)
    assert risk.level is RiskLevel.HIGH and 'Melemahkan' in risk.reasons[0]


def test_an_assessment_that_crashes_holds_the_pull_request(world, monkeypatch):
    def broken(patch, root):
        raise OSError('disk')
    monkeypatch.setattr('siamang.api.autonomy.patch_risk', broken)
    with client_of(world, level=2, engine=Engine(0)) as client:
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        held = log(client, task_id, 'publish')[0]
        assert (held['outcome'], held['risk']) == ('held', 'high') and not world.github.created


def test_the_record_cannot_be_rewritten_and_a_forged_row_shows(world):
    with client_of(world) as client:
        task_id = begin(client, world)
        wait_execution(client, task_id, 'design_approval')
        assert log(client, task_id)[0]['seal_valid']
        with Database(world.settings.database) as db:
            for sql in ("UPDATE api_autonomy_decisions SET risk='low'", 'DELETE FROM api_autonomy_decisions'):
                with pytest.raises(Exception, match='append only'):
                    db.conn.execute(sql)
            db.conn.execute('DROP TRIGGER api_autonomy_no_update')
            db.conn.execute("UPDATE api_autonomy_decisions SET outcome='auto'")
        assert not log(client, task_id)[0]['seal_valid']


def test_the_api_reports_the_level_and_scopes_the_record(world):
    with client_of(world, level=1, lead=True) as client:
        caps = client.get('/v0/capabilities', headers=headers()).json()
        assert caps['autonomy'] == {'level': 1, 'lead': True} and 'deciding' in caps['execution_phases']
        task_id = begin(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        assert client.get(f'/v0/tasks/{task_id}/autonomy').status_code == 401
        assert client.get(f'/v0/tasks/{task_id}/autonomy', headers=headers('outsider')).status_code == 404


def test_a_request_cannot_set_the_level(world):
    with client_of(world) as client:
        project_id = new_project(client, world)
        refused = client.post('/v0/tasks', headers=headers(), json={'project_id': project_id, 'description': BRIEF, 'request_id': 'req_x', 'autonomy': 3})
        assert refused.status_code == 422


# --- Phase 29: the merge ------------------------------------------------------------------------------


class Merges:
    """GitHub's view of the pull request, and what it was asked to merge."""

    def __init__(self, world, conclusion='success', **details):
        self.calls, self.details, self.refuse = [], details, None
        github = world.github
        github.ci_runs = lambda repository, branch, sha: [dict(id=1, name='CI', status='completed', conclusion=conclusion, url='u', jobs=[], artifacts=[])]
        github.pull_request_details = self._details
        github.merge_pull_request = self._merge
        self.world = world

    def _details(self, repository, branch):
        pull = self.world.github.pulls[branch]
        sha = git('rev-parse', branch, cwd=self.world.bare)
        files = (VALIDATION, TEST_VALIDATION)
        return PullDetails(**(dict(number=pull.number, state='OPEN', head_sha=sha, base='main', draft=False, review='', files=files, lines=5) | self.details))

    def _merge(self, repository, number, *, head_sha, method):
        if self.refuse:
            raise RepoError(self.refuse)
        self.calls.append(dict(number=number, head_sha=head_sha, method=method))


def merge_outcome(client, task_id):
    return until(lambda: log(client, task_id, 'merge') and log(client, task_id, 'merge')[-1], 'a merge decision')


def test_level_3_merges_a_low_risk_change_exactly_at_the_tested_commit(world):
    merges = Merges(world)
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        branch = published(client, task_id)
        assert merge_outcome(client, task_id)['outcome'] == 'auto'
        until(lambda: 'di-merge otomatis' in said(client, task_id), 'the owner told')
        assert merges.calls == [dict(number=branch['pr_number'], head_sha=branch['commit_sha'], method='squash')]


def test_level_3_says_so_in_its_capabilities(world):
    with client_of(world, level=3) as client:
        assert client.get('/v0/capabilities', headers=headers()).json()['publishing'] == 'merge_by_policy'
    with client_of(world, level=2) as client:
        assert client.get('/v0/capabilities', headers=headers()).json()['publishing'] == 'pull_request_only'


def test_level_3_waits_for_a_repair_and_merges_the_commit_that_went_green(world):
    merges, builds, results = Merges(world), {}, ['failure']
    world.programmer = make_model(Scripted())

    def ci_runs(repository, branch, sha):
        builds.setdefault(sha, results.pop(0) if results else 'success')
        return [dict(id=1, name='CI', status='completed', conclusion=builds[sha], url='u', jobs=[], artifacts=[])]
    world.github.ci_runs = ci_runs
    world.github.ci_failed_log = lambda repository, run_id, limit=6_000: 'FAILED tests/test_a.py::test_x'
    with client_of(world, level=3, engine=Engine(0)) as client:
        first = begin(client, world)
        published(client, first)
        until(lambda: merges.calls, 'the merge', seconds=30)
        tasks = client.get(f"/v0/tasks?project_id={snapshot(client, first)['project_id']}", headers=headers()).json()['items']
        repair = next(t['task_id'] for t in tasks if t['task_id'] != first)
        branch = snapshot(client, repair)['branch']
        assert merges.calls == [dict(number=branch['pr_number'], head_sha=branch['commit_sha'], method='squash')]
        assert builds[branch['commit_sha']] == 'success' and not log(client, first, 'merge')
        assert log(client, repair, 'publish')[0]['actor'] == 'policy.ci-repair'
        until(lambda: 'di-merge otomatis' in said(client, first), 'the owner told on the first task')


def test_a_task_on_the_branch_with_no_assessment_holds_the_merge(world):
    pull = PullDetails(1, 'OPEN', 'a' * 40, 'main', False, '', ('a.py',), 1)
    with client_of(world) as client:
        task_id = begin(client, world)
        wait_execution(client, task_id, 'design_approval')
        from siamang.api.store import RunStore
        with Database(world.settings.database) as db:
            assert glue.branch_risk(RunStore(db), [task_id], pull).level is RiskLevel.HIGH  # only a design decision so far


def test_level_2_never_merges(world):
    merges = Merges(world)
    with client_of(world, level=2, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        until(lambda: snapshot(client, task_id)['ci'] and snapshot(client, task_id)['ci']['state'] == 'success', 'CI')
        assert not merges.calls and not log(client, task_id, 'merge')


@pytest.mark.parametrize('details', [
    dict(files=(VALIDATION, 'src/auth/session.py')), dict(lines=601), dict(head_sha='b' * 40), dict(draft=True), dict(base='develop'), dict(review='CHANGES_REQUESTED')])
def test_what_github_says_now_can_hold_the_merge(world, details):
    merges = Merges(world, **details)
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        assert merge_outcome(client, task_id)['outcome'] == 'held'
        assert not merges.calls


def test_a_build_that_only_skipped_proves_nothing(world):
    merges = Merges(world, conclusion='skipped')
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        assert merge_outcome(client, task_id)['outcome'] == 'held' and not merges.calls


def test_a_repository_with_no_workflow_is_not_merged(world, monkeypatch):
    monkeypatch.setattr('siamang.api.runtime.CI_NO_WORKFLOW_SECS', .05)
    merges = Merges(world)
    world.github.ci_runs = lambda repository, branch, sha: []
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        assert merge_outcome(client, task_id)['outcome'] == 'held' and not merges.calls


def test_a_medium_change_gets_its_pull_request_but_not_the_merge(world, monkeypatch):
    merges = Merges(world)  # GitHub's file list looks harmless: the recorded risk of the patch still counts
    monkeypatch.setattr('siamang.api.autonomy.patch_risk', lambda patch, root: Risk(RiskLevel.MEDIUM, ('dependensi atau build: x',)))
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        assert log(client, task_id, 'publish')[0]['risk'] == 'medium'
        held = merge_outcome(client, task_id)
        assert held['outcome'] == 'held' and held['risk'] == 'medium' and not merges.calls


def test_a_refused_merge_is_recorded_after_its_intent_and_the_owner_hears(world):
    merges = Merges(world)
    merges.refuse = 'pull_request_merge_failed'
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        until(lambda: 'menolak merge' in said(client, task_id), 'the owner told')
        assert [d['outcome'] for d in log(client, task_id, 'merge')] == ['auto', 'merge_failed']


def test_an_already_merged_pull_request_is_left_alone(world):
    merges = Merges(world, state='MERGED')
    with client_of(world, level=3, engine=Engine(0)) as client:
        task_id = begin(client, world)
        published(client, task_id)
        until(lambda: snapshot(client, task_id)['ci'] and snapshot(client, task_id)['ci']['state'] == 'success', 'CI')
        threading.Event().wait(.1)
        assert not merges.calls and not log(client, task_id, 'merge')


# --- Phase 30: the Lead -------------------------------------------------------------------------------


def to_coding(client, world, level=0):
    task_id = begin(client, world)
    if level == 0:
        approve_design(client, task_id)
    return task_id


def test_the_lead_answers_from_the_spec_and_the_owner_is_not_asked(world):
    world.programmer = make_model(lambda m, r: ASKS if len(world.programmer.requests) == 1 else CLEAN)
    world.answers.append([GOOD_ANSWER])
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        told = messages(client, task_id)
        assert not any(m.get('questions') for m in told)
        assert told[0]['resolved'][0]['quote'] == QUOTE and QUOTE in told[0]['message']
        assert 'HTML polos.' in world.programmer.requests[-1].messages[-1].content
        decision = log(client, task_id, 'question')[0]
        assert (decision['outcome'], decision['actor']) == ('answered', 'lead.autonomy') and decision['seal_valid']
        phases = [e['phase'] for e in client.get(f'/v0/tasks/{task_id}/execution-events', headers=headers()).json()['items']]
        assert 'deciding' in phases and phases.count('design_approval') == 1


def test_an_invented_quote_reaches_the_owner_with_the_reason(world):
    world.programmer = make_model(ASKS)
    world.answers.append([dict(GOOD_ANSWER, quote='Pemilik sudah memilih React sejak awal.')])
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'clarification_required')
        asked = messages(client, task_id)[-1]['questions'][0]
        assert asked['question'] == QUESTION and 'sumber' in asked['lead_reason']
        assert log(client, task_id, 'question')[0]['outcome'] == 'forwarded'


def test_the_owner_sees_only_what_the_lead_could_not_settle(world):
    other = 'Warna tombolnya apa?'
    world.programmer = make_model(reply([], status='waiting', summary='Dua hal.', unknowns=[QUESTION, other]))
    world.answers.append([GOOD_ANSWER, dict(index=1, action='owner', answer='', source='', quote='', reason='Selera pemilik.')])
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'clarification_required')
        told = messages(client, task_id)
        assert [q['question'] for q in told[-1]['questions']] == [other] and told[-1]['questions'][0]['lead_reason'] == 'Selera pemilik.'
        assert told[-2]['resolved'][0]['answer'] == 'HTML polos.'


def test_a_reserved_question_goes_straight_to_the_owner(world):
    world.programmer = make_model(reply([], status='waiting', summary='Keputusan.', unknowns=['Pakai JWT atau session?']))
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'clarification_required')
        assert 'pemilik' in messages(client, task_id)[-1]['questions'][0]['lead_reason']
        assert not any('answers' in r.json_schema['properties'] for r in world.lead.requests if r.json_schema)


def test_a_question_that_keeps_coming_back_ends_at_the_owner(world):
    world.programmer = make_model(ASKS)  # the Programmer never stops asking
    world.answers.append([GOOD_ANSWER])
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'clarification_required')
        assert [d['outcome'] for d in log(client, task_id, 'question')] == ['answered', 'forwarded']
        assert len(world.programmer.requests) == 2


def test_without_the_lead_switch_the_owner_is_asked_as_before(world):
    world.programmer = make_model(ASKS)
    with client_of(world) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'clarification_required')
        assert messages(client, task_id)[-1]['questions'] == [dict(question=QUESTION, options=[])]
        assert not log(client, task_id, 'question')


def test_the_lead_settles_its_own_question_before_any_design_is_written(world):
    asked = dict(status='need_answers', message='Satu hal.', spec='', questions=[dict(question=QUESTION, options=[])])
    world.answers.append([GOOD_ANSWER])
    with client_of(world, lead=True) as client:
        task_id = task(client, new_project(client, world), description=BRIEF)
        world.lead_replies[:0] = [asked, TASK_READY]
        assert client.post(f'/v0/tasks/{task_id}/run', headers=headers()).status_code == 202
        wait_execution(client, task_id, 'design_approval')
        assert not any(m.get('questions') for m in messages(client, task_id))


def test_answers_do_not_use_up_the_rounds_the_lead_has_for_asking(world):
    asked = dict(status='need_answers', message='Satu hal.', spec='', questions=[dict(question='Warna tombolnya apa?', options=[])])
    settled = [dict(asked, questions=[dict(question=q, options=[])]) for q in (QUESTION, 'Halamannya memakai template apa?', 'Berkas statisnya di mana?')]
    world.answers += [[GOOD_ANSWER], [GOOD_ANSWER], [GOOD_ANSWER], [dict(index=0, action='owner', answer='', source='', quote='', reason='Selera.')]]
    with client_of(world, lead=True) as client:
        task_id = task(client, new_project(client, world), description=BRIEF)
        world.lead_replies[:0] = [*settled, asked]  # three settled by the Lead: more than the two rounds it may ask
        client.post(f'/v0/tasks/{task_id}/run', headers=headers())
        stopped(client, task_id, 'clarification_required')
        assert messages(client, task_id)[-1]['questions'][0]['question'] == 'Warna tombolnya apa?'


def test_the_lead_sends_held_work_back_once_with_a_direction(world):
    calls = []

    def programmer(model, request):
        calls.append(request.messages[-1].content)
        return CLEAN if any('Pindahkan' in c for c in calls[-1:]) else CONCERN
    world.programmer = make_model(programmer)
    world.steps.append(dict(next_step='revise', direction='Pindahkan perubahan ke modul validasi.', reason='Temuannya konkret.'))
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        wait_execution(client, task_id, 'awaiting_approval')
        assert log(client, task_id, 'next_step')[0]['outcome'] == 'revise'
        assert 'Arahan Lead' in said(client, task_id)


def test_the_revision_budget_ends_at_the_owner(world):
    world.programmer = make_model(lambda m, r: reply([change(f'scripts/banana_{len(world.programmer.requests)}.py', 'create', 'x = 1\n')]))
    world.steps.append(dict(next_step='revise', direction='Fokus ke modul validasi.', reason=''))
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'quality_gate')
        decisions = log(client, task_id, 'next_step')
        assert [d['outcome'] for d in decisions] == ['revise', 'forwarded'] and 'sudah mengembalikan' in decisions[1]['reasons'][0]
        assert messages(client, task_id)[-1]['questions'] and 'Alasan Lead' in messages(client, task_id)[-1]['message']
        assert not world.steps


def test_the_same_blockers_after_a_revision_are_not_sent_back_again(world):
    world.programmer = make_model(CONCERN)
    world.steps.append(dict(next_step='revise', direction='Fokus ke modul validasi.', reason=''))
    with client_of(world, lead=True, lead_revisions=3) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'quality_gate')
        decisions = log(client, task_id, 'next_step')
        assert [d['outcome'] for d in decisions] == ['revise', 'forwarded'] and 'tidak mengubah temuannya' in decisions[1]['reasons'][0]


def test_a_direction_to_silence_a_check_is_not_followed(world):
    world.programmer = make_model(CONCERN)
    world.steps.append(dict(next_step='revise', direction='Hapus test yang gagal.', reason=''))
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        stopped(client, task_id, 'quality_gate')
        assert log(client, task_id, 'next_step')[0]['outcome'] == 'forwarded'
        assert 'Hapus test' not in world.programmer.requests[-1].messages[-1].content


def test_cancelling_while_the_lead_works_stops_the_run(world):
    gate, entered = threading.Event(), threading.Event()
    world.programmer = make_model(ASKS)

    def lead(model, request):
        if 'answers' in request.json_schema['properties']:
            entered.set()
            gate.wait(10)
            return json.dumps(dict(answers=[GOOD_ANSWER]))
        return world._lead(model, request)
    world.authors['lead'] = make_model(lead, structured=True).router
    with client_of(world, lead=True) as client:
        task_id = to_coding(client, world)
        until(entered.is_set, 'the Lead at work')
        assert client.post(f'/v0/tasks/{task_id}/cancel', headers=headers()).status_code == 200
        gate.set()
        until(lambda: snapshot(client, task_id)['execution']['state'] == 'cancelled', 'cancelled')
        assert len(world.programmer.requests) == 1


def test_the_worker_is_free_again_after_a_run_that_kept_going(world):
    with client_of(world, level=2, engine=Engine(0)) as client:
        first = begin(client, world)
        published(client, first)
        second = task(client, snapshot(client, first)['project_id'], request_id='req_second', description=BRIEF)
        world.lead_replies.append(TASK_READY)
        until(lambda: client.post(f'/v0/tasks/{second}/run', headers=headers()).status_code == 202, 'the worker')
