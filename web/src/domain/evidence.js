// What a run actually executed, in words for the owner. Every value comes from the stored report.
// Nothing here decides a pass: `passed` is the backend's, derived from container exit codes.
export const CHECK_TEXT = {
  passed: 'Lulus',
  failed: 'Gagal',
  timed_out: 'Melewati batas waktu',
  unavailable: 'Tidak bisa dijalankan',
  error: 'Error di sandbox',
  skipped: 'Dilewati',
}
export const VERIFY_TEXT = {
  passed: 'Test dan build lulus di sandbox',
  failed: 'Test atau build gagal di sandbox',
  unavailable: 'Sandbox tidak tersedia · kode tidak dijalankan',
  not_configured: 'Tidak ada perintah test yang dikonfigurasi · kode tidak dijalankan',
}
export const TIER_TEXT = {
  1: 'Ulangi dengan umpan balik',
  2: 'Penasihat mengganti pendekatan',
  3: 'Susun ulang rencana',
}
const tone = status =>
  ({
    passed: 'success',
    completed: 'success',
    failed: 'error',
    stopped: 'error',
    timed_out: 'error',
    error: 'error',
  })[status] || 'warning'

export function verificationView(v) {
  if (!v || typeof v !== 'object' || typeof v.status !== 'string') return null
  const checks = (v.checks || []).map(c => ({
    name: c.name,
    status: c.status,
    text: CHECK_TEXT[c.status] || c.status,
    tone: tone(c.status),
    exitCode: c.exit_code ?? null,
    seconds: Number.isFinite(c.seconds) ? Math.round(c.seconds * 10) / 10 : null,
    output: String(c.output || '').slice(-2000),
  }))
  const total = v.criteria_total ?? (v.criteria || []).length,
    verified = v.criteria_verified ?? 0
  return {
    status: v.status,
    passed: v.passed === true,
    text: VERIFY_TEXT[v.status] || v.status,
    tone: tone(v.status),
    note: v.note || '',
    checks,
    criteria: {
      total,
      verified,
      items: (v.criteria || []).map(c => ({
        text: c.criterion,
        verified: c.status === 'verified',
        check: c.check || null,
        reason: c.reason || '',
      })),
      text: total
        ? `${verified} dari ${total} kriteria punya pemeriksaan yang terbukti${verified < total ? '; sisanya belum dipetakan ke pemeriksaan apa pun' : ''}`
        : 'Tidak ada kriteria penerimaan tercatat',
    },
  }
}

export function recoveryView(r) {
  if (!r || typeof r !== 'object' || typeof r.status !== 'string') return null
  const attempts = (r.attempts || []).map(a => ({
    stage: a.stage,
    tier: a.tier,
    tierText: a.attempt === 1 ? 'Percobaan pertama' : TIER_TEXT[a.tier] || 'Percobaan lanjutan',
    attempt: a.attempt,
    ok: a.ok === true,
    failure: a.failure || '',
  }))
  const used = r.tiers_used || []
  return {
    status: r.status,
    ok: r.status === 'completed',
    tone: tone(r.status),
    text:
      r.status === 'completed'
        ? attempts.length > 1
          ? `Pulih setelah ${attempts.length} percobaan`
          : 'Selesai pada percobaan pertama'
        : `Berhenti di tahap ${r.stopped_at || '?'}`,
    reason: r.status === 'stopped' ? r.reason || '' : '',
    resumed: (r.resumed || []).length > 0,
    tiers: [1, 2, 3].map(n => ({ tier: n, text: TIER_TEXT[n], used: used.includes(n) })),
    attempts,
    debt: (r.debt || []).map(d => ({
      stage: d.stage,
      tier: d.tier,
      tierText: d.tier ? TIER_TEXT[d.tier] : 'Dicatat oleh tahapnya',
      note: d.note,
    })),
  }
}

export function sandboxView(s) {
  if (!s || typeof s !== 'object' || typeof s.mode !== 'string') return null
  const ran = s.mode === 'finished'
  return {
    ran,
    text: ran
      ? 'Programmer bekerja di dalam sandbox'
      : s.mode === 'unavailable'
        ? 'Sandbox tidak tersedia · usulan dibuat tanpa menjalankan apa pun'
        : 'Programmer tidak menyelesaikan kerja di sandbox',
    turns: s.turns ?? 0,
    runs: s.runs ?? 0,
    verified: s.verified === true,
    verifiedText:
      s.verified === true
        ? 'Perintah terakhir lulus setelah edit terakhir'
        : 'Edit terakhir belum diikuti perintah yang lulus',
    tone: ran && s.verified === true ? 'success' : 'warning',
  }
}

export function evidenceView(report) {
  if (!report) return { verification: null, recovery: null, sandbox: null, any: false }
  const verification = verificationView(report.verification),
    recovery = recoveryView(report.recovery),
    sandbox = sandboxView(report.programmer_sandbox)
  return { verification, recovery, sandbox, any: !!(verification || recovery || sandbox) }
}

/** The line above the report: it must not claim code was never run when it was, nor that it was when it was not. */
export function evidenceNote(report, execution) {
  const v = report?.verification
  if (v && ['passed', 'failed'].includes(v.status))
    return {
      type: v.status === 'passed' ? 'success' : 'warning',
      text: `${VERIFY_TEXT[v.status]}. QA dan Reviewer tetap pemeriksaan statis; hasil eksekusi ada di bagian Eksekusi.`,
    }
  if (v)
    return {
      type: 'warning',
      text: `${VERIFY_TEXT[v.status] || 'Kode tidak dijalankan'}. QA dan Reviewer adalah pemeriksaan statis.`,
    }
  if (execution && execution.sandbox === false)
    return {
      type: 'info',
      text: 'Sandbox belum diaktifkan di backend ini: QA dan Reviewer adalah pemeriksaan statis dan kode belum dijalankan.',
    }
  return {
    type: 'info',
    text: 'Run ini tidak mencatat eksekusi di sandbox. QA dan Reviewer adalah pemeriksaan statis; kode belum dijalankan.',
  }
}

/** Short chips for a list row (approvals): verification and recovery as the API reports them. */
export function approvalChips(item) {
  const out = []
  if (item?.verification)
    out.push({
      key: 'verification',
      text: VERIFY_TEXT[item.verification] || item.verification,
      tone: tone(item.verification),
    })
  else out.push({ key: 'verification', text: 'Belum dijalankan di sandbox', tone: 'warning' })
  if (item?.recovery)
    out.push({
      key: 'recovery',
      text: item.recovery === 'completed' ? 'Melewati recovery' : 'Recovery berhenti',
      tone: tone(item.recovery),
    })
  return out
}
