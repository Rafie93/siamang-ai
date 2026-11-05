import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  evidenceView,
  evidenceNote,
  approvalChips,
  verificationView,
  recoveryView,
  sandboxView,
} from '../src/domain/evidence.js'
import EvidencePanel from '../src/components/EvidencePanel.vue'

const verification = (status, over = {}) => ({
  status,
  passed: status === 'passed',
  note: '',
  checks: [
    {
      name: 'test',
      status: status === 'passed' ? 'passed' : 'failed',
      exit_code: status === 'passed' ? 0 : 1,
      output: 'FAILED test_login',
      seconds: 1.234,
    },
  ],
  criteria: [
    { criterion: 'Login ditolak tanpa password', status: 'not_verified', reason: 'x' },
    { criterion: 'Login berhasil', status: 'verified', check: 'test' },
  ],
  criteria_verified: 1,
  criteria_total: 2,
  ...over,
})
const recovery = {
  status: 'completed',
  completed: ['deliver'],
  resumed: [],
  stopped_at: '',
  reason: '',
  tiers_used: [1, 2],
  attempts: [
    { stage: 'deliver', tier: 1, attempt: 1, ok: false, failure: 'test gagal' },
    { stage: 'deliver', tier: 2, attempt: 2, ok: true },
  ],
  debt: [{ stage: 'deliver', tier: 2, note: 'tanpa pesan error per field' }],
}

describe('evidence', () => {
  it("a pass is the backend's word and a failure is never softened", () => {
    expect(verificationView(verification('passed'))).toMatchObject({
      passed: true,
      tone: 'success',
      text: 'Test dan build lulus di sandbox',
    })
    const failed = verificationView(verification('failed'))
    expect(failed).toMatchObject({ passed: false, tone: 'error' })
    expect(failed.checks[0]).toMatchObject({ text: 'Gagal', exitCode: 1, seconds: 1.2 })
  })
  it('no engine and no command are warnings that say the code did not run', () => {
    for (const s of ['unavailable', 'not_configured']) {
      const v = verificationView({ status: s, passed: false, checks: [], criteria: [] })
      expect(v.tone).toBe('warning')
      expect(v.text).toContain('tidak dijalankan')
    }
  })
  it('criteria are counted as proven only where a check is mapped', () => {
    const v = verificationView(verification('passed'))
    expect(v.criteria.text).toBe(
      '1 dari 2 kriteria punya pemeriksaan yang terbukti; sisanya belum dipetakan ke pemeriksaan apa pun',
    )
    expect(v.criteria.items.map(i => i.verified)).toEqual([false, true])
    expect(verificationView({ status: 'passed', passed: true, checks: [], criteria: [] }).criteria.text).toBe(
      'Tidak ada kriteria penerimaan tercatat',
    )
  })
  it('recovery names the tiers used, each attempt, and the debt', () => {
    const r = recoveryView(recovery)
    expect(r).toMatchObject({ ok: true, text: 'Pulih setelah 2 percobaan', tone: 'success' })
    expect(r.tiers.map(t => t.used)).toEqual([true, true, false])
    expect(r.attempts[0]).toMatchObject({ ok: false, failure: 'test gagal', tierText: 'Percobaan pertama' })
    expect(r.debt[0]).toMatchObject({
      note: 'tanpa pesan error per field',
      tierText: 'Penasihat mengganti pendekatan',
    })
    const stopped = recoveryView({
      ...recovery,
      status: 'stopped',
      stopped_at: 'deliver_replanned',
      reason: 'test gagal',
      attempts: [],
    })
    expect(stopped).toMatchObject({
      ok: false,
      tone: 'error',
      text: 'Berhenti di tahap deliver_replanned',
      reason: 'test gagal',
    })
    expect(recoveryView({ ...recovery, resumed: ['deliver'] }).resumed).toBe(true)
  })
  it('sandbox work says whether the last command followed the last edit', () => {
    expect(sandboxView({ mode: 'finished', turns: 5, runs: 2, verified: true })).toMatchObject({
      ran: true,
      tone: 'success',
      verifiedText: 'Perintah terakhir lulus setelah edit terakhir',
    })
    expect(sandboxView({ mode: 'finished', verified: false })).toMatchObject({
      tone: 'warning',
      verifiedText: 'Edit terakhir belum diikuti perintah yang lulus',
    })
    expect(sandboxView({ mode: 'unavailable' })).toMatchObject({
      ran: false,
      text: expect.stringContaining('tanpa menjalankan apa pun'),
    })
  })
  it('nothing is invented for a run that recorded nothing', () => {
    expect(evidenceView({ run_id: 'r' }).any).toBe(false)
    expect(evidenceView(null).any).toBe(false)
    expect(verificationView({})).toBeNull()
    expect(recoveryView(null)).toBeNull()
  })
  it('the note never claims code ran when it did not, nor that it did not when it did', () => {
    expect(evidenceNote({ verification: verification('passed') }).type).toBe('success')
    expect(evidenceNote({ verification: verification('failed') }).type).toBe('warning')
    expect(evidenceNote({ verification: { status: 'unavailable' } }).text).toContain('tidak dijalankan')
    expect(evidenceNote({}, { sandbox: false }).text).toContain('belum diaktifkan')
    expect(evidenceNote({}, { sandbox: true }).text).toContain('tidak mencatat eksekusi')
  })
  it('approval chips show verification and recovery, and a missing check is a warning', () => {
    expect(
      approvalChips({ verification: 'passed', recovery: 'completed' }).map(c => [c.key, c.tone]),
    ).toEqual([
      ['verification', 'success'],
      ['recovery', 'success'],
    ])
    expect(approvalChips({})).toEqual([
      { key: 'verification', text: 'Belum dijalankan di sandbox', tone: 'warning' },
    ])
    expect(approvalChips({ verification: 'failed', recovery: 'stopped' }).map(c => c.tone)).toEqual([
      'error',
      'error',
    ])
  })
})

describe('EvidencePanel', () => {
  const show = report =>
    mount(EvidencePanel, {
      props: { report },
    })
  it('renders sandbox work, checks with failing output, criteria, recovery attempts and debt', () => {
    const w = show({
      verification: verification('failed'),
      recovery,
      programmer_sandbox: { mode: 'finished', turns: 4, runs: 2, verified: false },
    })
    const text = w.text()
    expect(text).toContain('Programmer bekerja di dalam sandbox')
    expect(text).toContain('4 giliran')
    expect(text).toContain('Edit terakhir belum diikuti perintah yang lulus')
    expect(text).toContain('GAGAL')
    expect(text).toContain('Test atau build gagal di sandbox')
    expect(w.find('details pre').text()).toBe('FAILED test_login')
    expect(text).toContain('belum dibuktikan')
    expect(text).toContain('dibuktikan oleh test')
    expect(text).toContain('Percobaan 1 · Percobaan pertama')
    expect(text).toContain('Percobaan 2 · Penasihat mengganti pendekatan')
    expect(text).toContain('Utang teknis')
    expect(text).toContain('tanpa pesan error per field')
  })
  it('renders nothing for a run without execution evidence', () => {
    expect(show({ run_id: 'r' }).find('.evidence-panel').exists()).toBe(false)
  })
  it('a passing run shows no failing-output block', () => {
    expect(
      show({ verification: verification('passed') })
        .find('details')
        .exists(),
    ).toBe(false)
  })
})
