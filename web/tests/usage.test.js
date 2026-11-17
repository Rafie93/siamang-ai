import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentUsageTable from '../src/components/AgentUsageTable.vue'
import UsageTotals from '../src/components/UsageTotals.vue'
import {
  agentLabel,
  callsText,
  timeText,
  tokenCell,
  tokenCount,
  tokenExact,
  tokenTitle,
  tokensUnknown,
  totalsFor,
  usageAgentFor,
  usageNotes,
} from '../src/domain/usage.js'

// Compact numbers keep their unit on the same line with a no-break space; compare them as plain text.
const plain = s => s.replace(/\s+/g, ' ')

const row = over => ({
  agent: 'programmer',
  models: ['claude-sonnet'],
  input_tokens: 182340,
  output_tokens: 21480,
  total_tokens: 203820,
  cache_read_tokens: 150000,
  cache_write_tokens: 2000,
  calls: 9,
  failed_calls: 0,
  unreported_calls: 0,
  model_ms: 600000,
  active_ms: 720000,
  ...over,
})

describe('numbers', () => {
  it('shortens tokens the Indonesian way and keeps the exact count for a tooltip', () => {
    expect([950, 12431, 203820, 1234567].map(tokenCount).map(plain)).toEqual([
      '950',
      '12,4 rb',
      '203,8 rb',
      '1,2 jt',
    ])
    expect(tokenCount(undefined)).toBe('—')
    expect(tokenExact(12431)).toBe('12.431 token')
  })
  it('shows a dash, never a zero, when no call reported its tokens', () => {
    const timedOut = row({
      calls: 9,
      failed_calls: 9,
      unreported_calls: 9,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
    })
    expect(tokensUnknown(timedOut)).toBe(true)
    expect(tokenCell(timedOut, 'input_tokens')).toBe('—')
    expect(tokenTitle(timedOut)).toBe('Panggilan model ini tidak melaporkan jumlah token')
    expect(tokensUnknown(row({ unreported_calls: 1 }))).toBe(false) // partly known: the number, and a note beside it
    expect(plain(tokenCell(row({ unreported_calls: 1 }), 'input_tokens'))).toBe('182,3 rb')
    expect(tokenCell(row({ calls: 0, input_tokens: 0 }), 'input_tokens')).toBe('0') // no call at all: truly zero
  })
  it('says how much of the input came through a prompt cache', () => {
    expect(tokenTitle(row())).toBe(
      'Input 182.340 token · termasuk 152.000 token lewat cache · Output 21.480 token',
    )
    expect(tokenTitle(row({ cache_read_tokens: 0, cache_write_tokens: 0 }))).toBe(
      'Input 182.340 token · Output 21.480 token',
    )
    expect(tokenTitle(null)).toBe('')
  })
  it('names calls and failures, and an agent that worked without a model', () => {
    expect(callsText(row())).toBe('9 panggilan')
    expect(callsText(row({ failed_calls: 2 }))).toBe('9 panggilan · 2 gagal')
    expect(callsText(row({ calls: 0 }))).toBe('Tanpa model')
  })
  it('shows a dash for time nobody measured', () => {
    expect(timeText(720000)).toBe('12 mnt 0 dtk')
    expect(timeText(0)).toBe('—')
    expect(timeText(undefined)).toBe('—')
  })
  it('maps the backend agents to the names on the office desks', () => {
    expect(agentLabel('lead')).toBe('Amang')
    expect(agentLabel('analyst')).toBe('Arsitek')
    expect(agentLabel('unknown')).toBe('Tidak tercatat')
    expect(usageAgentFor('amang')).toBe('lead')
    expect(usageAgentFor('qa')).toBe('qa')
  })
})

describe('what a total leaves out', () => {
  const usage = over => ({ complete: true, totals: row(), ...over })
  it('says nothing when the record is whole', () => {
    expect(usageNotes(usage())).toEqual([])
  })
  it('names work from before recording, and calls without counts, instead of hiding them', () => {
    const notes = usageNotes(usage({ complete: false, totals: row({ unreported_calls: 2 }) }))
    expect(notes).toHaveLength(2)
    expect(notes[0]).toContain('sebelum pencatatan token aktif')
    expect(notes[1]).toContain('2 panggilan model tidak melaporkan jumlah token')
  })
  it('shows the owner wait only when there was one', () => {
    expect(totalsFor(usage({ totals: row({ work_ms: 60000, waiting_ms: 0 }) })).map(t => t.key)).toEqual([
      'input',
      'output',
      'total',
      'work',
    ])
    expect(totalsFor(usage({ totals: row({ work_ms: 60000, waiting_ms: 5000 }) })).at(-1).value).toBe('5 dtk')
  })
})

describe('components', () => {
  it('lists each agent with its tokens, time, and calls', () => {
    const wrapper = mount(AgentUsageTable, {
      props: {
        agents: [row({ agent: 'lead', failed_calls: 1 }), row({ agent: 'qa', models: [], calls: 0 })],
      },
    })
    const [lead, qa] = wrapper.findAll('tbody tr')
    const cells = lead.findAll('td').map(c => plain(c.text()))
    expect(cells.slice(1)).toEqual([
      '182,3 rb',
      '21,5 rb',
      '203,8 rb',
      '12 mnt 0 dtk',
      '9 panggilan · 1 gagal',
    ])
    expect([lead.find('strong').text(), lead.find('small').text()]).toEqual(['Amang', 'claude-sonnet'])
    expect(qa.find('small').text()).toBe('Pengujian') // no model: its role instead
    expect(qa.findAll('td').at(-1).text()).toBe('Tanpa model')
  })
  it('says so when nothing was recorded yet', () => {
    expect(mount(AgentUsageTable, { props: { agents: [] } }).text()).toContain('Belum ada pekerjaan agent')
  })
  it('totals carry their exact value and their caveats', () => {
    const wrapper = mount(UsageTotals, {
      props: { usage: { complete: false, totals: row({ work_ms: 90000, waiting_ms: 0 }) } },
    })
    expect(wrapper.findAll('dd').map(d => plain(d.text()))).toEqual([
      '182,3 rb',
      '21,5 rb',
      '203,8 rb',
      '1 mnt 30 dtk',
    ])
    expect(wrapper.find('dl > div').attributes('title')).toContain('Input 182.340 token')
    expect(wrapper.text()).toContain('sebelum pencatatan token aktif')
  })
})
