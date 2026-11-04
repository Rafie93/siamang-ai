"""Phase 29-30 in the runtime: what the gates read, and what the owner is told. The decisions are in ``siamang.autonomy``."""
from __future__ import annotations

import json

from siamang.autonomy import AUTONOMY_ACTOR, Risk, RiskLevel, assess_patch, assess_paths

from . import ci_repair

UNASSESSED = 'Salah satu perubahan di branch ini tidak punya penilaian risiko.'


def patch_risk(patch, root) -> Risk:
    risk = assess_patch(patch.changes, lambda path: ci_repair.current_text(root, path))
    silenced = ci_repair.silencing_reason(patch, root)  # a change that hides a failure is never low risk, wherever it is
    return risk.join(Risk(RiskLevel.HIGH, (f'Melemahkan pemeriksaan: {silenced}',))) if silenced else risk


def test_passed(verification) -> bool:
    """Real execution of the operator's test command. A green build alone is not a test."""
    return bool(verification and verification.passed
                and any(c.name == 'test' and c.status == 'passed' for c in verification.checks))


def compromises(recovered) -> tuple[str, ...]:
    """What an advisor or a replan gave up. A note from tier 0 says only that a step was not run by the Programmer."""
    return tuple(d.note for d in (recovered.debt if recovered else ()) if d.tier >= 2)


def sources(store, task) -> dict[str, str]:
    """Only what the owner said or approved. Design documents and repository files are not authority."""
    spec = store.spec(task.project_id) or {}
    owner = [m.get('message', '') for m in store.messages(task.project_id, task.task_id) if m['role'] == 'owner']
    return dict(specification=spec.get('content', '') if spec.get('state') == 'approved' else '',
                instruction=task.description, owner_answers='\n'.join(owner))


def as_questions(questions) -> list[dict]:
    return [dict(q) if isinstance(q, dict) else dict(question=str(q), options=[]) for q in questions]


def answered_message(done) -> str:
    lines = '\n'.join(f'- {r.question["question"]}\n  Jawaban: {r.answer}\n  Sumber ({r.source}): "{r.quote}"' for r in done)
    return f'Saya menjawab {len(done)} pertanyaan dari yang sudah Anda putuskan:\n{lines}'


def held_message(verdict) -> str:
    return f'Kebijakan otomatis menahan {verdict.noun} ini untuk Anda.\n' + '\n'.join(f'- {r}' for r in verdict.reasons[:8])


def branch_risk(store, tasks, pull) -> Risk:
    """The whole branch, as GitHub has it: the patches recorded for each task on it, and the files it holds now."""
    found = [assess_paths(pull.files, pull.lines)]
    decisions = {t: store.decisions(t) for t in tasks}
    for rows in decisions.values():
        latest = next((d for d in reversed(rows) if d['gate'] == 'publish'), None)
        found.append(Risk(RiskLevel(latest['risk']), tuple(latest['reasons'])) if latest else Risk(RiskLevel.HIGH, (UNASSESSED,)))
    return found[0].join(*found[1:])


def ci_passed(runs) -> bool:
    """Every run ended well and at least one really succeeded: a build that only skipped proves nothing."""
    conclusions = [r.get('conclusion') for r in runs]
    return bool(conclusions) and 'success' in conclusions and all(c in ('success', 'skipped', 'neutral') for c in conclusions)


def approver_note(actor, report) -> tuple[str, str]:
    """Who approved, and what really ran, for the pull request. It must not credit a person for a policy's decision."""
    verification = json.loads(report or '{}').get('verification') or {}
    ran = ('- Test di sandbox: ' + ('lulus' if verification.get('passed') else str(verification.get('status')))
           if verification else '- Siamang tidak menjalankan kode pada run ini. Hasil build dan test nyata ada di tab Checks kalau repo punya workflow.')
    if actor == AUTONOMY_ACTOR:
        return ran, f'Dibuat oleh Siamang dan disetujui otomatis oleh kebijakan `{actor}`, tanpa peninjauan manusia. Riwayat keputusannya tercatat.'
    if actor == ci_repair.CI_REPAIR_ACTOR:
        return ran, f'Dibuat oleh Siamang dan disetujui otomatis oleh `{actor}`. Merge tetap keputusan pemilik repository.'
    return ran, 'Dibuat oleh Siamang setelah disetujui operator. Merge tetap keputusan pemilik repository.'
