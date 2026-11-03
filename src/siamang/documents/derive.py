"""Documents computed from deterministic pipeline results. No model is involved."""
from __future__ import annotations

from siamang.documents.author import Document
from siamang.documents.catalog import DocumentStatus, Source, specs_for

NOT_RUN_REASON = 'Belum ada eksekusi test: QA saat ini hanya pemeriksaan statis. Hasil ini hanya boleh berasal dari CI.'
QA_VERDICTS = {'pass': 'PASS', 'fail': 'FAIL', 'inconclusive': 'INCONCLUSIVE'}
REVIEW_VERDICTS = {'pass': 'APPROVE', 'concern': 'REQUEST CHANGES', 'fail': 'REJECT', 'inconclusive': 'INCONCLUSIVE'}


def _generated(spec, content):
    return Document(spec, DocumentStatus.GENERATED, content)


def _absent(spec, reason):
    return Document(spec, DocumentStatus.NOT_APPLICABLE, reason=reason)


def _files(changes):
    return '\n\n'.join(f'### `{c.path}` ({c.operation})\n\n```\n{c.after_content or ""}\n```' for c in changes)


def _is_test(path):
    name = path.rsplit('/', 1)[-1]
    return '/tests/' in f'/{path}' or name.startswith('test_') or '.test.' in name or '.spec.' in name


def programmer_documents(patch) -> tuple[Document, ...]:
    changes = patch.changes if patch else ()
    groups = {
        'source_code': _files([c for c in changes if c.after_content is not None]),
        'file_structure': '\n'.join(f'- `{c.path}` ({c.operation})' for c in changes),
        'migration': _files([c for c in changes if 'migration' in c.path.lower()]),
        'unit_tests': _files([c for c in changes if _is_test(c.path)]),
        'patch': '\n'.join(f'- {c.operation} `{c.path}` · after_hash `{c.after_hash}`' for c in changes),
    }
    missing = {'migration': 'Patch tidak berisi file migrasi.', 'unit_tests': 'Patch tidak berisi file test.'}
    default = 'Programmer tidak menghasilkan patch.'
    return tuple(_generated(s, groups[s.kind]) if groups[s.kind] else _absent(s, missing.get(s.kind, default))
                 for s in specs_for('programming', Source.DERIVED))


def _findings(findings):
    return '\n'.join(f'- **{f.severity}** `{f.code}`{" · " + f.path if f.path else ""}: {f.message}' for f in findings)


def qa_documents(qa) -> tuple[Document, ...]:
    out = [Document(s, DocumentStatus.NOT_RUN, reason=NOT_RUN_REASON) for s in specs_for('qa', Source.MEASURED)]
    for spec in specs_for('qa', Source.DERIVED):
        if qa is None:
            out.append(_absent(spec, 'QA tidak berjalan karena tidak ada patch.'))
        elif spec.kind == 'bug_report':
            out.append(_generated(spec, _findings(qa.findings)) if qa.findings else _absent(spec, 'QA statis tidak menemukan masalah.'))
        else:
            status = str(qa.status)
            unverified = '\n'.join(f'- {item}' for item in qa.not_verified)
            out.append(_generated(spec, f'**Verdict: {QA_VERDICTS[status]}** (pemeriksaan statis)\n\n'
                                        f'Belum terverifikasi:\n{unverified}'))
    return tuple(out)


def review_documents(review) -> tuple[Document, ...]:
    out = []
    for spec in specs_for('reviewing', Source.DERIVED):
        if review is None:
            out.append(_absent(spec, 'Reviewer tidak berjalan.'))
        elif spec.kind == 'findings_summary':
            out.append(_generated(spec, _findings(review.findings)) if review.findings else _absent(spec, 'Tidak ada temuan.'))
        else:
            status = str(review.status)
            out.append(_generated(spec, f'**Verdict: {REVIEW_VERDICTS[status]}** '
                                        '(pemeriksaan deterministik; penilaian model tidak dapat mengubahnya)'))
    return tuple(out)
