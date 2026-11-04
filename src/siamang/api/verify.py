"""Execution evidence at the end of a run: the configured checks run in the sandbox against the passed patch.

A failed check returns to the Programmer as input, up to ``verify_attempts`` times, like a failed build does. A run
whose checks could not run (no engine, no command) does not pass: it stops at the quality gate and says why.
"""
from __future__ import annotations

import re

from siamang.documents.catalog import DocumentStatus

DEFAULT_ATTEMPTS = 2
INPUT_KEY = 'verification_failure'
_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+?)\s*$')


def criteria_from(design) -> list[str]:
    """The checkable lines of the acceptance-criteria document, if the design has one."""
    for document in design:
        if document.status is DocumentStatus.GENERATED and document.spec.kind == 'acceptance_criteria':
            return [m.group(1) for line in document.content.splitlines() if (m := _ITEM.match(line))]
    return []


def failure_input(report) -> dict:
    return {INPUT_KEY: report.feedback()}


def failure_text(report) -> str:
    """Why a delivery attempt failed, for people and for the next attempt: the check, then the end of its output."""
    failing = next((c for c in report.checks if c.status not in ('passed', 'skipped')), None)
    tail = ' '.join(failing.output.split())[-300:] if failing and failing.output else ''
    return ('; '.join(report.blockers()) + (f' — {tail}' if tail else ''))[:700]


def blocked_message(report) -> str:
    if report.status == 'failed':
        return 'Test atau build gagal saat dijalankan di sandbox. Hasil ini tidak dikirim sebagai lulus.'
    return 'Kode belum dijalankan di sandbox, jadi hasil ini belum punya bukti eksekusi.'
