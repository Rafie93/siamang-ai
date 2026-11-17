// Tokens and time per agent, as the backend recorded them. Nothing here estimates: a count a provider never
// reported stays unknown, and the page says so instead of showing a smaller total as if it were whole.
import { DOCUMENT_AGENTS } from './documents.js'
import { duration } from './office.js'

const compact = new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 1 })
const exact = new Intl.NumberFormat('id-ID')

/** Short enough for a table cell: "950", "12,4 rb", "1,2 jt". */
export function tokenCount(n) {
  if (!Number.isFinite(n)) return '—'
  return n < 1000 ? exact.format(n) : compact.format(n)
}

/** What the short form stands for: "12.431 token". */
export function tokenExact(n) {
  return Number.isFinite(n) ? `${exact.format(n)} token` : '—'
}

/** No call of this row reported its tokens (they timed out): what it used is unknown, which is not zero. */
export const tokensUnknown = row => !!row?.calls && row.unreported_calls === row.calls

/** A row's token count for a cell: a dash when nothing about it is known. */
export function tokenCell(row, field) {
  return tokensUnknown(row) ? '—' : tokenCount(row?.[field])
}

/** Input, output, and the part of the input a prompt cache served, for a tooltip. */
export function tokenTitle(row) {
  if (!row) return ''
  if (tokensUnknown(row)) return 'Panggilan model ini tidak melaporkan jumlah token'
  const cached = (row.cache_read_tokens || 0) + (row.cache_write_tokens || 0)
  return [
    `Input ${tokenExact(row.input_tokens)}`,
    cached ? `termasuk ${tokenExact(cached)} lewat cache` : '',
    `Output ${tokenExact(row.output_tokens)}`,
  ]
    .filter(Boolean)
    .join(' · ')
}

export function agentLabel(agent) {
  if (agent === 'unknown') return 'Tidak tercatat'
  return DOCUMENT_AGENTS.find(a => a.id === agent)?.name || agent
}

export function agentRole(agent) {
  return DOCUMENT_AGENTS.find(a => a.id === agent)?.role || ''
}

/** The backend's agent for an office character: the Lead sits at Amang's desk. */
export const usageAgentFor = officeId => DOCUMENT_AGENTS.find(a => a.officeId === officeId)?.id || ''

/** "4 panggilan", "4 panggilan · 1 gagal"; an agent that never called a model worked without one. */
export function callsText(row) {
  if (!row?.calls) return 'Tanpa model'
  return `${row.calls} panggilan${row.failed_calls ? ` · ${row.failed_calls} gagal` : ''}`
}

/** A time the record holds, or a dash when nothing was measured. */
export function timeText(ms) {
  return Number.isFinite(ms) && ms > 0 ? duration(ms) : '—'
}

/** Why a total may be smaller than what was really used. Empty when it is whole. */
export function usageNotes(usage) {
  if (!usage) return []
  const notes = []
  if (usage.complete === false)
    notes.push('Sebagian pekerjaan ini terjadi sebelum pencatatan token aktif, jadi tokennya tidak tercatat.')
  const unknown = usage.totals?.unreported_calls || 0
  if (unknown)
    notes.push(
      `${unknown} panggilan model tidak melaporkan jumlah token (biasanya karena timeout), jadi tidak ikut dijumlahkan.`,
    )
  return notes
}

/** The totals a summary shows, in order. ``waiting`` only when the owner was waited for. */
export function totalsFor(usage) {
  const t = usage?.totals
  if (!t) return []
  return [
    { key: 'input', label: 'Input', value: tokenCell(t, 'input_tokens'), title: tokenTitle(t) },
    {
      key: 'output',
      label: 'Output',
      value: tokenCell(t, 'output_tokens'),
      title: tokenExact(t.output_tokens),
    },
    {
      key: 'total',
      label: 'Total token',
      value: tokenCell(t, 'total_tokens'),
      title: tokenExact(t.total_tokens),
    },
    {
      key: 'work',
      label: 'Durasi kerja',
      value: timeText(t.work_ms),
      title: 'Waktu run berjalan, tanpa antre dan menunggu Anda',
    },
    ...(t.waiting_ms > 0
      ? [{ key: 'waiting', label: 'Menunggu Anda', value: timeText(t.waiting_ms), title: '' }]
      : []),
  ]
}
