import { describe, it, expect } from 'vitest'
import { AGENTS, teamState, transitions, agentForPhase } from '../src/domain/team.js'
import { demoSnapshot, DEMO_SCRIPT, DEMO_CYCLE } from '../src/domain/demo.js'

const now = Date.parse('2026-10-12T12:00:00Z')
const ev = (position, phase, extra = {}) => ({
  position,
  run_id: 'run_a',
  task_id: 'tsk',
  timestamp: new Date(now - 60000 + position * 1000).toISOString(),
  state: 'running',
  phase,
  iteration: null,
  wait_reason: null,
  ...extra,
})
const running = (phase, heartbeatAgo = 1000) => ({
  task_id: 'tsk',
  status: 'running',
  description: 'x',
  execution: {
    phase,
    activity_known: true,
    stale: false,
    heartbeat_at: new Date(now - heartbeatAgo).toISOString(),
  },
})

describe('team roster', () => {
  it('has the lead plus the five SIAMANG roles, each owning a backend phase', () => {
    expect(AGENTS.map(a => a.id)).toEqual(['amang', 'planner', 'analyst', 'programmer', 'qa', 'reviewer'])
    for (const p of ['planning', 'analysis', 'programming', 'qa', 'reviewing'])
      expect(agentForPhase(p)).not.toBe('amang')
    expect(agentForPhase('unknown_phase')).toBeNull()
  })
})

describe('truthful team state', () => {
  it('offline: nobody works and every agent is offline', () => {
    const s = teamState({ task: running('programming'), connected: false, now })
    expect(s.activeAgent).toBeNull()
    expect(s.mode).toBe('offline')
    expect(Object.values(s.agents).every(a => a.status === 'offline')).toBe(true)
  })
  it('fresh heartbeat for a known phase marks exactly that agent working', () => {
    const s = teamState({
      task: running('qa'),
      events: [ev(1, 'queued'), ev(2, 'planning'), ev(3, 'analysis'), ev(4, 'programming'), ev(5, 'qa')],
      now,
    })
    expect(s.activeAgent).toBe('qa')
    expect(s.agents.qa.status).toBe('working')
    expect(Object.entries(s.agents).filter(([, a]) => a.status === 'working')).toHaveLength(1)
    expect(s.agents.planner.status).toBe('done')
    expect(s.agents.reviewer.status).toBe('standby')
  })
  it('running without fresh heartbeat is never shown as work', () => {
    for (const task of [
      running('programming', 20000),
      { task_id: 't', status: 'running', description: 'x' },
      { ...running('programming'), execution: { ...running('programming').execution, stale: true } },
    ]) {
      const s = teamState({ task, now })
      expect(s.activeAgent).toBeNull()
      expect(Object.values(s.agents).some(a => a.status === 'working')).toBe(false)
    }
  })
  it('waiting is only approval when the backend says approval_required', () => {
    expect(
      teamState({
        task: {
          task_id: 't',
          status: 'waiting',
          description: 'x',
          execution: { wait_reason: 'approval_required' },
        },
        now,
      }).approval,
    ).toBe(true)
    const gate = teamState({
      task: { task_id: 't', status: 'waiting', description: 'x', execution: { wait_reason: 'quality_gate' } },
      now,
    })
    expect(gate.approval).toBe(false)
    expect(gate.agents.qa.status).toBe('blocked')
    expect(teamState({ task: { task_id: 't', status: 'waiting', description: 'x' }, now }).approval).toBe(
      false,
    )
  })
  it('terminal outcomes are reported without inventing activity', () => {
    const s = teamState({ task: { task_id: 't', status: 'completed', description: 'x' }, now })
    expect(s.outcome).toBe('completed')
    expect(s.activeAgent).toBeNull()
  })
})

describe('handoffs from persisted execution events', () => {
  it('derives one handoff per owner change, including review→programmer revisions', () => {
    const list = transitions([
      ev(1, 'queued'),
      ev(2, 'planning'),
      ev(3, 'analysis'),
      ev(4, 'programming', { iteration: 1 }),
      ev(5, 'qa'),
      ev(6, 'reviewing'),
      ev(7, 'programming', { iteration: 2 }),
      ev(8, 'qa'),
      ev(9, 'reviewing'),
      ev(10, 'awaiting_approval', { state: 'waiting' }),
    ])
    expect(list.map(t => t.from + '>' + t.to)).toEqual([
      'amang>planner',
      'planner>analyst',
      'analyst>programmer',
      'programmer>qa',
      'qa>reviewer',
      'reviewer>programmer',
      'programmer>qa',
      'qa>reviewer',
      'reviewer>amang',
    ])
    expect(list[5]).toMatchObject({ kind: 'revision', iteration: 2 })
    expect(list.at(-1).kind).toBe('report')
    expect(new Set(list.map(t => t.key)).size).toBe(list.length)
  })
  it('does not create a handoff across separate runs or for malformed events', () => {
    const list = transitions([
      ev(1, 'queued'),
      ev(2, 'planning'),
      { ...ev(3, 'queued'), run_id: 'run_b' },
      null,
      { position: 4 },
      { ...ev(5, 'planning'), run_id: 'run_b' },
    ])
    expect(list.map(t => t.from + '>' + t.to)).toEqual(['amang>planner', 'amang>planner'])
  })
})

describe('demo mode', () => {
  it('is always flagged as simulation', () => {
    for (let t = 0; t < DEMO_CYCLE * 2; t += 7) {
      const d = demoSnapshot(t)
      expect(d.simulated).toBe(true)
      expect(d.task.simulated).toBe(true)
      expect(d.events.every(e => e.simulated)).toBe(true)
    }
  })
  it('walks through every role and ends at approval then completion', () => {
    const seen = new Set()
    for (let t = 0; t < DEMO_CYCLE; t++) {
      const d = demoSnapshot(t)
      const s = teamState({ ...d, simulated: true })
      if (s.activeAgent) seen.add(s.activeAgent)
    }
    expect([...seen].sort()).toEqual(['amang', 'analyst', 'planner', 'programmer', 'qa', 'reviewer']) // Amang is active while he recovers a failed run
    const approval = DEMO_SCRIPT.find(s => s.phase === 'awaiting_approval').at
    expect(teamState(demoSnapshot(approval + 1)).approval).toBe(true)
    expect(demoSnapshot(DEMO_CYCLE - 1).task.status).toBe('completed')
    expect(demoSnapshot(DEMO_CYCLE + 1).task.task_id).not.toBe(demoSnapshot(1).task.task_id)
  })
})

describe('sandbox verification and recovery phases', () => {
  const run = (phase, extra = {}) => ({
    task_id: 't',
    status: 'running',
    description: 'x',
    execution: {
      phase,
      activity_known: true,
      stale: false,
      heartbeat_at: new Date().toISOString(),
      iteration: 2,
      ...extra,
    },
  })
  it('verifying belongs to QA and says the code is being run; recovering belongs to the Lead', () => {
    const v = teamState({ task: run('verifying'), events: [] })
    expect(v.activeAgent).toBe('qa')
    expect(v.phase).toBe('verifying')
    expect(v.agents.qa.label).toContain('sandbox')
    const r = teamState({ task: run('recovering'), events: [] })
    expect(r.activeAgent).toBe('amang')
    expect(r.phase).toBe('recovering')
    expect(r.iteration).toBe(2)
    expect(teamState({ task: run('programming'), events: [] }).phase).toBe('programming')
  })
  it('a stale heartbeat is still never animated as work in the new phases', () => {
    const old = run('verifying', { heartbeat_at: new Date(Date.now() - 60000).toISOString() })
    const s = teamState({ task: old, events: [] })
    expect(s.activeAgent).toBeNull()
    expect(s.phase).toBeNull()
    expect(s.mode).toBe('unverified')
  })
  it('the handoffs around a failed check are told as such', () => {
    const e = (position, phase) => ({
      position,
      run_id: 'r',
      task_id: 't',
      timestamp: new Date().toISOString(),
      state: 'running',
      phase,
    })
    const list = transitions([e(1, 'reviewing'), e(2, 'verifying'), e(3, 'recovering'), e(4, 'programming')])
    expect(list.map(x => [x.from, x.to, x.kind])).toEqual([
      ['reviewer', 'qa', 'handoff'],
      ['qa', 'amang', 'recovery'],
      ['amang', 'programmer', 'revision'],
    ])
    expect(list[0].say).toContain('sandbox')
    expect(list[1].say).toContain('gagal')
    expect(list[2].say).toContain('hasil test yang gagal')
  })
  it('the simulation shows a failed check, the recovery, and a second pass, all flagged as simulation', () => {
    const phases = DEMO_SCRIPT.map(s => s.phase)
    expect(phases).toContain('verifying')
    expect(phases.indexOf('recovering')).toBeGreaterThan(phases.indexOf('verifying'))
    expect(phases.filter(p => p === 'verifying')).toHaveLength(2)
    const at = DEMO_SCRIPT.find(s => s.phase === 'recovering').at + 1
    const s = teamState({ ...demoSnapshot(at), simulated: true })
    expect(s.simulated).toBe(true)
    expect(s.activeAgent).toBe('amang')
  })
})

describe('wall board sandbox step', () => {
  it('never reads as passed unless the run reached approval', async () => {
    const { sandboxStatus } = await import('../src/three/screens.js')
    const at = sec => teamState({ ...demoSnapshot(sec), simulated: true })
    const phaseAt = p => DEMO_SCRIPT.find(s => s.phase === p).at + 1
    expect(sandboxStatus(at(phaseAt('verifying')))).toBe('working')
    expect(sandboxStatus(at(phaseAt('recovering')))).toBe('failed')
    expect(sandboxStatus(at(DEMO_SCRIPT.filter(s => s.phase === 'programming').at(-1).at + 1))).toBe(
      'standby',
    )
    expect(sandboxStatus(at(phaseAt('awaiting_approval')))).toBe('done')
  })
})
