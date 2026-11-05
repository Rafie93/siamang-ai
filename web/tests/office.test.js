import { describe, it, expect } from 'vitest'
import * as THREE from 'three'
import { createKit } from '../src/three/kit.js'
import { createCharacter } from '../src/three/character.js'
import { AGENTS, teamState } from '../src/domain/team.js'
import {
  agentDetail,
  attention,
  bubbleLines,
  designWork,
  duration,
  peakTogether,
  stageFallback,
  withPortfolio,
} from '../src/domain/office.js'
import { fileSize } from '../src/domain/labels.js'
import { permitted } from '../scripts/gateway.mjs'
import { validResponse } from '../src/services/contract.js'

describe('character', () => {
  it('has nothing sticking out of its face, whatever it is doing', () => {
    const kit = createKit()
    for (const a of AGENTS) {
      const c = createCharacter(a.id, kit, { color: a.color })
      for (const gesture of ['idle', 'talk', 'present']) {
        c.gesture(gesture)
        c.update(0.05, 0.1)
        c.root.updateMatrixWorld(true)
        c.root.traverse(o => {
          if (!o.isMesh || !o.visible || o.material.color?.getHex() !== 0x5a2f2a) return
          const h = new THREE.Box3().setFromObject(o).getSize(new THREE.Vector3()).y
          expect(h).toBeLessThan(0.06)
        })
        expect(new THREE.Box3().setFromObject(c.root).max.y).toBeLessThan(2.1)
      }
    }
  })
})

const NOW = Date.parse('2026-11-02T12:00:00Z')
const ev = (position, phase, ago, iteration = null) => ({
  position,
  run_id: 'r',
  task_id: 't',
  phase,
  iteration,
  state: 'running',
  wait_reason: null,
  timestamp: new Date(NOW - ago * 1000).toISOString(),
})
const running = (phase, iteration = null) => ({
  task_id: 't',
  title: 'Tombol kontak',
  description: 'x',
  status: 'running',
  project_id: 'p',
  execution: { phase, iteration, activity_known: true, heartbeat_at: new Date(NOW - 1000).toISOString() },
})
const CATALOG = [
  { agent: 'qa', stage: 'qa', kind: 'test_plan', title: 'Test Plan', source: 'authored' },
  { agent: 'qa', stage: 'qa', kind: 'qa_verdict', title: 'Verdict', source: 'derived' },
  { agent: 'lead', stage: 'briefing', kind: 'project_brief', title: 'Brief' },
  { agent: 'planner', stage: 'planning', kind: 'prd', title: 'PRD' },
]
const detail = (id, task, events, extra = {}) =>
  agentDetail(id, {
    team: teamState({ task, events, now: NOW }),
    task,
    events,
    catalog: CATALOG,
    projectName: 'Proyek A',
    now: NOW,
    ...extra,
  })

describe('agent detail', () => {
  it('names what the working agent does, on which task, and for how long', () => {
    const d = detail('qa', running('qa', 2), [ev(1, 'programming', 90, 2), ev(2, 'qa', 75, 2)])
    expect(d).toMatchObject({
      working: true,
      doing: 'Memeriksa patch secara statis, iterasi 2',
      project: 'Proyek A',
      task: 'Tombol kontak',
      since: '1 mnt 15 dtk',
    })
    expect(d.steps.map(s => s.text)).toEqual(['QA memeriksa'])
  })
  it('tells a document stage from checking the patch', () => {
    const d = detail('qa', running('qa'), [
      ev(1, 'programming', 90, 1),
      ev(2, 'qa', 80, 1),
      ev(3, 'reviewing', 70, 1),
      ev(4, 'programming', 60),
      ev(5, 'qa', 5),
    ])
    expect(d.doing).toBe('Menulis rencana dan kasus test')
    expect(d.pending).toEqual(['Test Plan'])
    const coding = detail('programmer', running('programming', 1), [ev(1, 'programming', 5, 1)])
    expect(coding.doing).toBe('Menulis patch, iterasi 1')
    expect(coding.pending).toEqual([])
    expect(detail('programmer', running('programming'), [ev(1, 'programming', 5)]).doing).toBe(
      'Menulis patch',
    )
  })
  it('never claims work for an agent the backend does not report', () => {
    const d = detail('planner', running('qa', 1), [ev(1, 'programming', 20, 1), ev(2, 'qa', 5, 1)])
    expect(d.working).toBe(false)
    expect(d.since).toBeNull()
    expect(d.pending).toEqual([])
    expect(d.doing).toBe('Belum mendapat giliran pada task ini')
    const stale = {
      ...running('qa', 1),
      execution: { phase: 'qa', activity_known: true, heartbeat_at: new Date(NOW - 60000).toISOString() },
    }
    expect(detail('qa', stale, [ev(1, 'qa', 60, 1)]).working).toBe(false)
    const off = agentDetail('qa', { team: teamState({ connected: false }), catalog: CATALOG, now: NOW })
    expect(off.working).toBe(false)
    expect(off.status).toBe('offline')
  })
  it('counts only documents the run recorded', () => {
    const d = detail('qa', running('reviewing', 1), [ev(1, 'reviewing', 5, 1)], {
      documents: [
        { kind: 'test_plan', status: 'generated' },
        { kind: 'qa_verdict', status: 'failed', reason: 'x' },
      ],
    })
    expect(d.generated).toBe(1)
    expect(d.documents.find(x => x.kind === 'qa_verdict').status).toBe('failed')
  })
  it('gives the Lead every project and what each one waits for', () => {
    const feed = [
      {
        project_id: 'a',
        name: 'A',
        repository: { state: 'ready' },
        spec_state: 'approved',
        tasks: [
          { task_id: '1', title: 'Satu', status: 'waiting', execution: { wait_reason: 'design_approval' } },
        ],
      },
      {
        project_id: 'b',
        name: 'B',
        repository: { state: 'ready' },
        spec_state: 'gathering',
        tasks: [{ task_id: '2', title: 'Dua', status: 'running', execution: {} }],
      },
      { project_id: 'c', name: 'C', repository: { state: 'ready' }, spec_state: 'approved', tasks: [] },
    ]
    const d = detail('amang', null, [], { feed })
    expect(d.projects.map(p => p.name)).toEqual(['A', 'B', 'C'])
    expect(d.projects[0].open).toEqual(['Satu: Setujui desain'])
    expect(d.projects[1].running).toBe('Dua')
    expect(d.projects[2].open).toEqual([])
    expect(attention(feed).map(a => a.text)).toEqual(['Setujui desain', 'Amang menunggu jawaban Anda'])
    expect(bubbleLines(d).lines.join(' ')).toContain('Memegang 3 proyek · 2 menunggu Anda')
  })
  it('marks a simulated detail as such', () => {
    const task = running('qa', 1),
      team = teamState({ task, events: [ev(1, 'qa', 5, 1)], simulated: true, now: NOW })
    expect(
      bubbleLines(agentDetail('qa', { team, task, events: [ev(1, 'qa', 5, 1)], now: NOW })).lines.at(-1),
    ).toContain('Simulasi')
  })
})

describe('portfolio on the stage', () => {
  const feed = [
    {
      project_id: 'a',
      name: 'A',
      tasks: [
        { task_id: '1', title: 'x', status: 'waiting', execution: { wait_reason: 'approval_required' } },
      ],
    },
  ]
  it('keeps the Lead waiting for another project while the stage is idle', () => {
    expect(withPortfolio(teamState({ now: NOW }), feed).agents.amang).toEqual({
      status: 'waiting',
      label: '1 hal menunggu Anda',
    })
  })
  it('does not override real work, offline or a simulation', () => {
    const working = teamState({ task: { ...running('qa', 1), status: 'running' }, events: [], now: NOW })
    expect(withPortfolio(working, feed).agents.qa.status).toBe('working')
    expect(withPortfolio(teamState({ connected: false }), feed).agents.amang.status).toBe('offline')
    expect(withPortfolio(teamState({ simulated: true, now: NOW }), feed).agents.amang.status).toBe('standby')
  })
})
it('formats durations', () => {
  expect(duration(5000)).toBe('5 dtk')
  expect(duration(75000)).toBe('1 mnt 15 dtk')
  expect(duration(3700000)).toBe('1 jam 1 mnt')
  expect(duration(-1)).toBe('—')
})
it('opens only the office read to the gateway, and checks its shape', () => {
  expect(permitted('GET', '/v0/office')).toBe(true)
  expect(permitted('POST', '/v0/office')).toBe(false)
  expect(
    validResponse('/office', 'GET', {
      items: [{ project_id: 'p', tasks: [], task_counts: {}, waiting: {} }],
      current: null,
    }),
  ).toBe(true)
  expect(validResponse('/office', 'GET', { items: [{ project_id: 'p' }] })).toBe(false)
})
it('shows no document results when no task is on stage', () => {
  const d = agentDetail('qa', { team: teamState({ now: NOW }), catalog: CATALOG, now: NOW })
  expect(d.documents).toEqual([])
  expect(d.doing).toBe('Tidak ada pekerjaan saat ini')
})
it('puts what needs the owner on stage first, then the latest task', () => {
  const feed = [
    {
      tasks: [
        { task_id: 'a', status: 'completed', updated_at: '2026-11-02T10:00:00Z' },
        { task_id: 'b', status: 'waiting', updated_at: '2026-11-01T10:00:00Z' },
      ],
    },
    { tasks: [{ task_id: 'c', status: 'completed', updated_at: '2026-11-02T11:00:00Z' }] },
  ]
  expect(stageFallback(feed)).toBe('b')
  expect(stageFallback([feed[1]])).toBe('c')
  expect(stageFallback([])).toBe('')
})
describe('several programmers', () => {
  const pkgs = [
    {
      key: 'a',
      title: 'Backend',
      owns: ['backend/'],
      state: 'running',
      phase: 'programming',
      iteration: 1,
      files: 0,
    },
    {
      key: 'b',
      title: 'Frontend',
      owns: ['frontend/'],
      state: 'running',
      phase: 'qa',
      iteration: 1,
      files: 0,
    },
    {
      key: 'c',
      title: 'Mobile',
      owns: ['mobile/'],
      state: 'running',
      phase: 'programming',
      iteration: 2,
      files: 0,
    },
    { key: 'd', title: 'Fondasi', owns: ['x/'], state: 'passed', phase: null, files: 4 },
  ]
  const task = { ...running('programming', 1), packages: pkgs }
  it('shows every agent the packages really use', () => {
    const s = teamState({ task, events: [ev(1, 'programming', 5, 1)], now: NOW })
    expect(s.agents.programmer).toMatchObject({ status: 'working', count: 2, label: 'Mengerjakan 2 paket' })
    expect(s.agents.qa.status).toBe('working')
    expect(s.agents.reviewer.status).toBe('standby')
  })
  it('never claims package work on a stale heartbeat', () => {
    const s = teamState({
      task: { ...task, execution: { ...task.execution, heartbeat_at: new Date(NOW - 60000).toISOString() } },
      events: [],
      now: NOW,
    })
    expect(s.agents.programmer.status).not.toBe('working')
    expect(s.agents.qa.status).not.toBe('working')
  })
  it('lists each package in the detail and above the head', () => {
    const d = detail('programmer', task, [ev(1, 'programming', 5, 1)])
    expect(d.packages.map(p => p.title)).toEqual(['Backend', 'Frontend', 'Mobile', 'Fondasi'])
    expect(d.doing).toBe('Mengerjakan 2 paket')
    expect(bubbleLines(d).lines).toContain('Fondasi: Lulus')
    expect(detail('qa', task, [ev(1, 'programming', 5, 1)]).packages.map(p => p.title)).toEqual(['Frontend'])
  })
})
it('formats artifact sizes', () => {
  expect(fileSize(500)).toBe('500 B')
  expect(fileSize(2048)).toBe('2 KB')
  expect(fileSize(5 * 1048576)).toBe('5.0 MB')
  expect(fileSize(undefined)).toBe('—')
})

describe('design parts', () => {
  const at = s => new Date(Date.UTC(2026, 0, 1, 0, 0, s)).toISOString()
  const part = (n, from, to, extra = {}) => ({
    stage: 'analysis',
    part: n,
    attempt: 1,
    state: to == null ? 'running' : 'done',
    kinds: ['srs'],
    reason: null,
    model_id: null,
    started_at: at(from),
    finished_at: to == null ? null : at(to),
    ...extra,
  })
  const CAT = [
    { agent: 'analyst', stage: 'analysis', kind: 'srs', title: 'SRS', source: 'authored' },
    { agent: 'planner', stage: 'planning', kind: 'prd', title: 'PRD', source: 'authored' },
  ]
  it('counts together only what the recorded times show', () => {
    expect(peakTogether([part(1, 0, 10), part(2, 1, 9), part(3, 2, 8)])).toBe(3)
    expect(peakTogether([part(1, 0, 10), part(2, 20, 30)])).toBe(1)
    expect(peakTogether([part(1, 0, 10), part(2, 5, null)], Date.parse(at(6)))).toBe(2)
    expect(peakTogether([])).toBe(0)
  })
  it('says one after another when the times say so', () => {
    expect(designWork([part(1, 0, 10), part(2, 20, 30)], CAT, 'analyst').notes[0]).toBe(
      'Arsitek merancang: 2 dari 2 bagian selesai, berjalan satu per satu',
    )
    expect(designWork([part(1, 0, 10), part(2, 1, 9)], CAT, 'analyst').notes[0]).toBe(
      'Arsitek merancang: 2 dari 2 bagian selesai, 2 bagian berjalan bersamaan',
    )
  })
  it('shows each call with its documents, time and reason, only to its own agent', () => {
    const failed = part(2, 0, 300, { state: 'failed', reason: 'timeout' })
    const { rows } = designWork([part(1, 0, 90), failed], CAT, 'analyst')
    expect(rows[0]).toMatchObject({
      label: 'Bagian 1',
      titles: ['SRS'],
      stateText: 'Selesai',
      took: '1 mnt 30 dtk',
    })
    expect(rows[1]).toMatchObject({ stateText: 'Gagal', reason: 'Model terlalu lama menjawab.' })
    expect(designWork([part(1, 0, 90)], CAT, 'planner').rows).toEqual([])
  })
  it('marks a document being written, not as not reached', () => {
    const task = { ...running('analysis'), design_parts: [part(1, 0, null)] }
    const d = detail('analyst', task, [ev(1, 'analysis', 5)], { catalog: CAT })
    expect(d.documents[0]).toMatchObject({ kind: 'srs', statusText: 'Sedang ditulis' })
    expect(d.pending).toEqual(['SRS'])
  })
  it('never reads a division attempt as a part of the design', () => {
    const division = {
      stage: 'division',
      part: 1,
      attempt: 1,
      state: 'failed',
      kinds: ['work_packages'],
      reason: 'timeout',
      model_id: null,
      started_at: at(0),
      finished_at: at(9),
    }
    const d = designWork([division], CAT, 'analyst')
    expect(d.rows[0].label).toBe('Pembagian kerja')
    expect(d.notes).toEqual([])
  })
})
