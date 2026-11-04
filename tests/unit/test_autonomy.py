"""Phase 29-30 without a runtime: the risk rules, the level table, and what the Lead's answers must prove."""
import json
from types import SimpleNamespace

import pytest

from siamang.approval.decision import APPROVER_ID_RE
from siamang.api.settings import Principal
from siamang.autonomy import (
    AUTONOMY_ACTOR,
    AutonomyPolicy,
    Gate,
    Risk,
    RiskLevel,
    assess_brief,
    assess_patch,
    assess_paths,
    blockers_key,
    next_step,
    settle,
)
from siamang.autonomy.lead import question_key
from siamang.config import ConfigError, load_config
from siamang.models.errors import GenerationError
from siamang.repos.client import RepoClient, RepoError
from tests.support.programmer import make_model

LOW, MEDIUM, HIGH = Risk(RiskLevel.LOW), Risk(RiskLevel.MEDIUM), Risk(RiskLevel.HIGH)


def change(path, content='x = 1\n', operation='modify'):
    return SimpleNamespace(path=path, after_content=content, operation=operation)


# --- risk ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize('path', [
    'src/auth/service.py', 'src/UserAuth.ts', 'app/login.vue', 'db/migrations/0001.py', 'database/schema.sql', 'prisma/schema.prisma',
    '.github/workflows/ci.yml', '.gitlab-ci.yml', 'CODEOWNERS', '.env', '.env.example', 'deploy/app.yaml', 'infra/main.tf',
    'src/payments/refund.py', 'config/secrets.yml', 'src/permissions.py', 'tests/test_auth.py'])
def test_a_path_that_touches_something_sensitive_is_high(path):
    assert assess_paths([path]).level is RiskLevel.HIGH


@pytest.mark.parametrize('path', ['package.json', 'poetry.lock', 'requirements-dev.txt', 'Dockerfile', 'go.mod', 'web/Makefile'])
def test_what_changes_the_build_is_medium(path):
    assert assess_paths([path]).level is RiskLevel.MEDIUM


@pytest.mark.parametrize('path', ['src/authors.py', 'docs/authoring.md', 'src/tokenizer.py', 'src/keyboard.ts', 'src/landing.html',
                                  'src/sessionless.py', 'README.md'])
def test_a_word_inside_another_word_does_not_count(path):
    assert assess_paths([path]).level is RiskLevel.LOW


@pytest.mark.parametrize('line', ['CREATE TABLE users (id int)', 'alter  table x add y', 'password = form.get("p")', 'import jwt',
                                  'API_KEY = load()', 'subprocess.run(cmd)', 'eval(text)', 'digest = hmac.new(k)'])
def test_added_lines_can_make_an_innocent_path_high(line):
    assert assess_patch([change('src/util.py', line + '\n')]).level is RiskLevel.HIGH


def test_only_lines_the_patch_adds_are_read():
    before = 'password = load()\nx = 1\n'
    assert assess_patch([change('src/util.py', before + 'y = 2\n')], lambda path: before).level is RiskLevel.LOW
    assert assess_patch([change('src/util.py', '', 'delete')], lambda path: before).level is RiskLevel.LOW


def test_size_makes_a_change_medium():
    assert assess_patch([change(f'src/f{i}.py') for i in range(21)]).level is RiskLevel.MEDIUM
    assert assess_patch([change('src/big.py', 'x\n' * 601)]).level is RiskLevel.MEDIUM
    assert assess_patch([change(f'src/f{i}.py', '', 'delete') for i in range(6)]).level is RiskLevel.MEDIUM
    assert assess_patch([change('src/ok.py', 'x\n' * 600)]).level is RiskLevel.LOW


@pytest.mark.parametrize('text,level', [
    ('Tambah tombol kontak di landing page', RiskLevel.LOW), ('Perbaiki halaman login', RiskLevel.HIGH),
    ('Tambah kolom baru untuk alamat', RiskLevel.HIGH), ('Perbaiki kerentanan XSS', RiskLevel.HIGH), ('Deploy ke produksi', RiskLevel.HIGH),
    ('Upgrade framework ke versi 11', RiskLevel.MEDIUM), ('Tulis daftar authors', RiskLevel.LOW)])
def test_the_brief_is_read_in_both_languages(text, level):
    assert assess_brief(text).level is level


def test_a_brief_cannot_lower_what_its_paths_say():
    assert assess_brief('Tambah tombol kecil', ['src/auth/']).level is RiskLevel.HIGH
    assert assess_brief('Tambah tombol kecil', packages=5).level is RiskLevel.MEDIUM


def test_join_keeps_the_worst_and_every_reason():
    joined = Risk(RiskLevel.LOW, ('a',)).join(Risk(RiskLevel.HIGH, ('b',)), Risk(RiskLevel.MEDIUM, ('a',)))
    assert joined.level is RiskLevel.HIGH and joined.reasons == ('a', 'b')


# --- the level table ----------------------------------------------------------------------------------


@pytest.mark.parametrize('level,risk,allowed', [
    (0, LOW, False), (1, LOW, True), (1, MEDIUM, False), (1, HIGH, False), (2, LOW, True), (2, MEDIUM, True), (2, HIGH, False),
    (3, MEDIUM, True), (3, HIGH, False)])
def test_design_gate(level, risk, allowed):
    assert AutonomyPolicy(level=level).design(risk).allowed is allowed


@pytest.mark.parametrize('level,risk,allowed', [
    (0, LOW, False), (1, LOW, False), (2, LOW, True), (2, MEDIUM, True), (2, HIGH, False), (3, LOW, True), (3, HIGH, False)])
def test_publish_gate(level, risk, allowed):
    assert AutonomyPolicy(level=level).publish(risk, test_passed=True, has_branch=True).allowed is allowed


GOOD_MERGE = dict(ci_state='success', ci_passed=True, open_at_tested_commit=True, base_is_default=True, reviewers_object=False)


@pytest.mark.parametrize('level,risk,allowed', [(0, LOW, False), (2, LOW, False), (3, LOW, True), (3, MEDIUM, False), (3, HIGH, False)])
def test_merge_gate(level, risk, allowed):
    assert AutonomyPolicy(level=level).merge(risk, **GOOD_MERGE).allowed is allowed


@pytest.mark.parametrize('change_', [dict(ci_passed=False), dict(open_at_tested_commit=False), dict(base_is_default=False), dict(reviewers_object=True)])
def test_every_merge_condition_is_needed(change_):
    verdict = AutonomyPolicy(level=3).merge(LOW, **(GOOD_MERGE | change_))
    assert not verdict.allowed and verdict.reasons


@pytest.mark.parametrize('kwargs', [dict(test_passed=False, has_branch=True), dict(test_passed=True, has_branch=False),
                                    dict(test_passed=True, has_branch=True, compromises=('tanpa validasi',))])
def test_no_level_passes_a_pull_request_without_its_evidence(kwargs):
    assert not AutonomyPolicy(level=3).publish(LOW, **kwargs).allowed


def test_a_failed_division_holds_the_design():
    assert not AutonomyPolicy(level=3).design(LOW, division_failed=True).allowed


def test_the_default_is_every_gate_human():
    policy = AutonomyPolicy()
    assert policy.level == 0 and not policy.lead and not any(policy.gate_open(g) for g in Gate)


@pytest.mark.parametrize('kwargs', [dict(level=4), dict(level=-1), dict(level=True), dict(lead_answers=21), dict(lead_revisions=4), dict(merge_method='force')])
def test_a_policy_outside_its_bounds_is_refused(kwargs):
    with pytest.raises(ValueError):
        AutonomyPolicy(**kwargs)


def test_no_api_token_can_act_under_the_policys_name_but_the_approval_record_accepts_it():
    with pytest.raises(ValueError):
        Principal(AUTONOMY_ACTOR, 'a' * 64)
    assert APPROVER_ID_RE.fullmatch(AUTONOMY_ACTOR)


# --- configuration ------------------------------------------------------------------------------------


def config(tmp_path, text):
    path = tmp_path / 'siamang.toml'
    path.write_text(text)
    return load_config(path, env={})


SANDBOX = '[sandbox]\nenabled = true\nimage = "python:3.12-slim"\ntest_command = "pytest"\n'


def test_the_file_is_off_unless_it_says_otherwise(tmp_path):
    assert config(tmp_path, '').autonomy.level == 0 and not config(tmp_path, '').autonomy.lead
    on = config(tmp_path, SANDBOX + '[autonomy]\nlevel = 2\nlead = true\nlead_answers = 3\n').autonomy
    assert (on.level, on.lead, on.lead_answers) == (2, True, 3)


@pytest.mark.parametrize('text', [
    '[autonomy]\nlevel = 4\n', '[autonomy]\nlead_answers = 21\n', '[autonomy]\nlead_revisions = 4\n', '[autonomy]\nmerge_method = "force"\n',
    '[autonomy]\nlevel = 2\n', '[autonomy]\nunknown = 1\n', '[sandbox]\nenabled = true\nimage = "x"\n[autonomy]\nlevel = 3\n'])
def test_a_bad_file_is_refused_and_a_level_that_could_never_show_a_test_too(tmp_path, text):
    with pytest.raises(ConfigError):
        config(tmp_path, text)


# --- the Lead's answers -------------------------------------------------------------------------------

SPEC = 'Halaman ini memakai HTML polos tanpa framework apa pun.'
SOURCES = dict(specification=SPEC, instruction='Tambah tombol kontak', owner_answers='')
QUESTION = dict(question='Pakai React atau HTML?', options=[])


def lead(*answers, **kwargs):
    return make_model(lambda m, r: json.dumps(dict(answers=list(answers))), structured=True, **kwargs)


def answer(**extra):
    return dict(index=0, action='answer', answer='HTML polos.', source='specification', quote='memakai HTML polos tanpa framework', reason='') | extra


def test_an_answer_with_a_real_quote_is_accepted_whatever_its_spacing():
    done = settle(lead(answer(quote='MEMAKAI  html\npolos tanpa framework')).router, [QUESTION], SOURCES, 6)[0]
    assert done.answered and done.answer == 'HTML polos.' and done.source == 'specification'


@pytest.mark.parametrize('bad', [dict(quote='memakai React dengan Next.js sekarang'), dict(quote='HTML polos'), dict(source='design'),
                                 dict(source='instruction'), dict(answer=''), dict(quote='')])
def test_an_answer_that_cannot_show_its_source_goes_to_the_owner(bad):
    done = settle(lead(answer(**bad)).router, [QUESTION], SOURCES, 6)[0]
    assert not done.answered and 'sumber' in done.reason


def test_the_lead_may_leave_a_question_to_the_owner():
    done = settle(lead(dict(index=0, action='owner', answer='', source='', quote='', reason='Ini pilihan bisnis.')).router, [QUESTION], SOURCES, 6)[0]
    assert not done.answered and done.reason == 'Ini pilihan bisnis.'


@pytest.mark.parametrize('question', ['Pakai JWT atau session?', 'Berapa anggaran hosting?', 'Boleh hapus halaman lama?', 'Tabel baru atau kolom?'])
def test_some_decisions_never_reach_the_model_even_with_a_quote_in_the_spec(question):
    model = lead(answer())
    done = settle(model.router, [dict(question=question, options=[])], dict(SOURCES, specification=question * 3), 6)[0]
    assert not done.answered and 'pemilik' in done.reason and not model.requests


def test_a_question_answered_before_returns_to_the_owner():
    model = lead(answer())
    done = settle(model.router, [QUESTION], SOURCES, 6, frozenset({question_key('  pakai react   atau html? ')}))[0]
    assert not done.answered and not model.requests


def test_the_budget_ends_the_answers():
    questions = [QUESTION, dict(question='Warna tombolnya?', options=[])]
    model = lead(answer(), answer(index=1))
    assert [r.answered for r in settle(model.router, questions, SOURCES, 1)] == [True, False]
    model = lead(answer())
    assert not settle(model.router, [QUESTION], SOURCES, 0)[0].answered and not model.requests


def test_no_sources_means_no_model_call():
    model = lead(answer())
    assert not settle(model.router, [QUESTION], dict(specification='', instruction='', owner_answers=''), 6)[0].answered and not model.requests


@pytest.mark.parametrize('model', [make_model(structured=True, fail_with=GenerationError('down')), make_model(lambda m, r: '[1,2]', structured=True),
                                   make_model(lambda m, r: json.dumps({'answers': 'x'}), structured=True),
                                   make_model(lambda m, r: json.dumps({'answers': [{'index': 'x'}, 'y']}), structured=True)])
def test_a_failing_or_useless_model_forwards_everything(model):
    assert not settle(model.router, [QUESTION], SOURCES, 6)[0].answered


def test_a_secret_in_an_answer_is_withheld():
    token = 'ghp_RAHASIA0123456789012345678901234567'
    done = settle(lead(answer(answer=f'Pakai {token}')).router, [QUESTION], SOURCES, 6)[0]
    assert done.answered and token not in done.answer


# --- the Lead's next step -----------------------------------------------------------------------------


def step(**reply):
    return next_step(make_model(lambda m, r: json.dumps(reply), structured=True).router, 'tugas', ['temuan'])


def test_a_concrete_direction_is_passed_on():
    done = step(next_step='revise', direction='Pindahkan validasi ke fungsi sendiri.', reason='jelas')
    assert done.action == 'revise' and 'validasi' in done.direction


@pytest.mark.parametrize('direction', ['Skip the failing test.', 'Hapus test yang gagal.', 'Lewati semua pemeriksaan QA dulu', 'disable CI for now',
                                       'Longgarkan assertion di test_a'])
def test_a_direction_that_silences_a_check_is_never_passed_on(direction):
    done = step(next_step='revise', direction=direction, reason='')
    assert done.action == 'ask_owner' and not done.direction


@pytest.mark.parametrize('reply', [dict(next_step='revise', direction=' ', reason=''), dict(next_step='merge', direction='x', reason=''), dict(next_step='ask_owner', direction='', reason='')])
def test_anything_else_goes_to_the_owner(reply):
    assert step(**reply).action == 'ask_owner'


def test_a_model_failure_goes_to_the_owner():
    assert next_step(make_model(structured=True, fail_with=GenerationError('down')).router, 't', ['b']).action == 'ask_owner'


def test_the_same_blockers_in_any_order_are_the_same():
    assert blockers_key(['a', 'B  c']) == blockers_key(['b c', 'A']) != blockers_key(['a'])


# --- the merge call -----------------------------------------------------------------------------------

SHA = 'a' * 40


class Recording(RepoClient):
    def __init__(self, output=''):
        super().__init__()
        self.argv, self.output = None, output

    def _run(self, argv, **kwargs):
        self.argv = argv
        return self.output


def test_the_merge_is_pinned_to_the_tested_commit():
    client = Recording()
    client.merge_pull_request('rafie/landing', 7, head_sha=SHA, method='squash')
    assert client.argv == ['gh', 'pr', 'merge', '7', '--repo', 'rafie/landing', '--squash', '--match-head-commit', SHA]
    assert '--admin' not in client.argv and '--delete-branch' not in client.argv


@pytest.mark.parametrize('kwargs', [dict(head_sha='main'), dict(head_sha=''), dict(head_sha=SHA, method='admin'), dict(head_sha='--admin')])
def test_a_merge_call_outside_its_shape_is_refused(kwargs):
    client = Recording()
    with pytest.raises(RepoError):
        client.merge_pull_request('rafie/landing', 7, **kwargs)
    assert client.argv is None


def test_pull_request_details_are_read_from_github():
    client = Recording(json.dumps([dict(number=7, state='OPEN', headRefOid=SHA, baseRefName='main', isDraft=False, reviewDecision=None,
                                        files=[dict(path='a.py', additions=2, deletions=1), dict(path='b.py', additions=1, deletions=0)])]))
    found = client.pull_request_details('rafie/landing', 'siamang/x')
    assert (found.number, found.head_sha, found.files, found.lines, found.review) == (7, SHA, ('a.py', 'b.py'), 4, '')
    assert Recording('[]').pull_request_details('rafie/landing', 'siamang/x') is None
    with pytest.raises(RepoError):
        Recording('[{"number": 1}]').pull_request_details('rafie/landing', 'siamang/x')
