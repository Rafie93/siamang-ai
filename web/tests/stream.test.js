import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { openStream } from '../src/services/stream.js'

const TIMERS = { toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] }

class FakeSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  static made = []
  constructor(url) {
    this.url = url
    this.readyState = FakeSource.OPEN
    this.listeners = {}
    FakeSource.made.push(this)
  }
  addEventListener(type, listener) {
    this.listeners[type] = listener
  }
  close() {
    this.readyState = FakeSource.CLOSED
  }
  emit(type, data) {
    this.listeners[type]({ data: typeof data === 'string' ? data : JSON.stringify(data) })
  }
  fail(state) {
    this.readyState = state
    this.onerror()
  }
}
const latest = () => FakeSource.made.at(-1)

beforeEach(() => {
  FakeSource.made = []
  vi.useFakeTimers(TIMERS)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('openStream', () => {
  function open(over = {}) {
    const frames = [],
      onLost = vi.fn()
    const stream = openStream({ Source: FakeSource, onFrame: (t, d) => frames.push([t, d]), onLost, ...over })
    return { frames, onLost, stream }
  }
  it('is absent where the browser has no EventSource, so the page keeps polling', () => {
    expect(openStream({ Source: null, onFrame() {}, onLost() {} })).toBeNull()
  })
  it('passes frames on and ignores one it cannot read', () => {
    const { frames } = open()
    latest().emit('ready', { cursor: 1 })
    latest().emit('changed', '{not json')
    latest().emit('execution', { task_id: 't' })
    expect(frames).toEqual([
      ['ready', { cursor: 1 }],
      ['execution', { task_id: 't' }],
    ])
  })
  it('leaves a dropped connection to the browser, which resumes with the last id', () => {
    const { onLost } = open()
    latest().emit('ready', {})
    latest().fail(FakeSource.CONNECTING)
    latest().fail(FakeSource.CONNECTING)
    expect(onLost).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(10000)
    expect(FakeSource.made).toHaveLength(1)
  })
  it('retries itself after an error status, more slowly each time, and starts over once it works', () => {
    open()
    latest().fail(FakeSource.CLOSED)
    vi.advanceTimersByTime(2999)
    expect(FakeSource.made).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeSource.made).toHaveLength(2)
    latest().fail(FakeSource.CLOSED)
    vi.advanceTimersByTime(5999)
    expect(FakeSource.made).toHaveLength(2)
    vi.advanceTimersByTime(1)
    expect(FakeSource.made).toHaveLength(3)
    latest().emit('ready', {})
    latest().fail(FakeSource.CLOSED)
    vi.advanceTimersByTime(3000)
    expect(FakeSource.made).toHaveLength(4)
  })
  it('does not call a stream that never opened lost', () => {
    const { onLost } = open()
    latest().fail(FakeSource.CLOSED)
    expect(onLost).not.toHaveBeenCalled()
  })
  it('treats silence as a dead connection, since a ping should have come', () => {
    const { onLost } = open()
    latest().emit('ready', {})
    vi.advanceTimersByTime(20000)
    latest().emit('ping', {})
    vi.advanceTimersByTime(20000)
    expect(onLost).not.toHaveBeenCalled()
    vi.advanceTimersByTime(5000)
    expect(onLost).toHaveBeenCalledTimes(1)
    expect(FakeSource.made[0].readyState).toBe(FakeSource.CLOSED)
    vi.advanceTimersByTime(3000)
    expect(FakeSource.made).toHaveLength(2)
  })
  it('stops everything when closed', () => {
    const { stream } = open()
    latest().fail(FakeSource.CLOSED)
    stream.close()
    vi.advanceTimersByTime(120000)
    expect(FakeSource.made).toHaveLength(1)
  })
})

describe('office store on the stream', () => {
  const project = {
    project_id: 'prj_a',
    name: 'A',
    status: 'active',
    task_counts: {},
    waiting: {},
    features: [],
  }
  const beating = at => ({
    run_id: 'run_1',
    state: 'running',
    phase: 'planning',
    activity_known: true,
    stale: false,
    heartbeat_at: at,
  })
  let history, calls, office, gate, task

  function route(path) {
    if (path === '/health') return { status: 'healthy', contract_version: '0.8' }
    if (path === '/capabilities')
      return {
        permissions: ['read'],
        task_statuses: [],
        model_configured: true,
        proposal_only: true,
        patch_application: false,
      }
    if (path === '/projects' || path === '/portfolio') return { items: [project] }
    if (path === '/office') return { items: [{ ...project, tasks: [task] }], current: null }
    if (path === '/approvals') return { items: [] }
    if (path === '/tasks') return { items: [task] }
    if (path === '/tasks/tsk_a') return task
    if (path.endsWith('/reports')) return { items: [], patch_applied: false }
    return { items: [] }
  }
  function count(fragment) {
    return calls.filter(c => c.startsWith('/api/v0' + fragment)).length
  }
  const fresh = () => {
    calls = []
  }
  // Runs a task's history as the route does: oldest first, 100 at a time, after a cursor.
  function pageOf(url) {
    const after = Number(url.searchParams.get('after') || 0),
      limit = Number(url.searchParams.get('limit') || 100)
    const items = history.filter(e => e.position > after).slice(0, limit)
    return { items, next_after: items.at(-1)?.position ?? after }
  }
  async function settle(ms = 0) {
    if (ms) vi.advanceTimersByTime(ms)
    for (let i = 0; i < 30; i++) await flushPromises() // a load is a long chain of awaits
  }
  /** Time passing on a healthy but idle stream: the backend still pings every 10 seconds. */
  async function quiet(ms) {
    for (let left = ms; left > 0; left -= 10000) {
      await settle(Math.min(left, 10000))
      latest().emit('ping', {})
    }
  }
  async function begin() {
    const { useOfficeStore } = await import('../src/stores/office.js')
    setActivePinia(createPinia())
    office = useOfficeStore()
    office.start()
    await settle()
  }

  beforeEach(() => {
    history = [{ position: 1, run_id: 'run_1', task_id: 'tsk_a', phase: 'queued', state: 'scheduled' }]
    task = {
      task_id: 'tsk_a',
      project_id: 'prj_a',
      status: 'running',
      description: 'd',
      execution: beating(new Date(Date.now()).toISOString()),
    }
    calls = []
    gate = null
    localStorage.clear()
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    vi.stubGlobal('EventSource', FakeSource)
    vi.stubGlobal(
      'fetch',
      vi.fn(async input => {
        const url = new URL(input, 'http://local')
        if (url.pathname === '/session') return { ok: true, status: 200, json: async () => ({ csrf: 'c' }) }
        calls.push(url.pathname + url.search)
        if (gate) await gate
        const path = url.pathname.replace('/api/v0', '')
        const body = path.endsWith('/execution-events') ? pageOf(url) : route(path)
        return { ok: true, status: 200, json: async () => body }
      }),
    )
  })
  afterEach(() => office?.stop())

  async function live() {
    await begin()
    latest().emit('ready', { cursor: 1, replay: false })
    await settle()
    fresh()
  }

  it('polls every 3 seconds until the stream opens, then only as a safety net', async () => {
    await begin()
    expect(office.streaming).toBe(false)
    fresh()
    await settle(3000)
    expect(count('/health')).toBe(1)
    latest().emit('ready', { cursor: 1, replay: false })
    await settle()
    expect(office.streaming).toBe(true)
    fresh()
    await quiet(29000)
    expect(calls).toEqual([])
    await settle(1000)
    expect(count('/health')).toBe(1)
  })
  it('loads everything again when the stream opens, for whatever happened before it', async () => {
    await begin()
    fresh()
    latest().emit('ready', { cursor: 1, replay: false })
    await settle()
    for (const route of ['/health', '/office', '/tasks?', '/tasks/tsk_a', '/approvals'])
      expect(count(route)).toBeGreaterThan(0)
  })
  it('keeps a running task fresh on heartbeats alone, and shows the truth when they stop', async () => {
    await live()
    const beat = at =>
      latest().emit('heartbeat', { run_id: 'run_1', task_id: 'tsk_a', project_id: 'prj_a', heartbeat_at: at })
    for (let i = 0; i < 8; i++) {
      await settle(3000)
      beat(new Date(Date.now()).toISOString())
    }
    await settle()
    expect(calls).toEqual([]) // 24 seconds and not one request
    expect(office.status.label).toBe('Menyusun rencana')
    expect(office.feed[0].tasks[0].execution.heartbeat_at).toBe(office.selected.execution.heartbeat_at)
    expect(office.tasks[0].execution.heartbeat_at).toBe(office.selected.execution.heartbeat_at)
    office.stop() // no safety poll here, only the passing of time
    vi.advanceTimersByTime(16000)
    expect(office.visualStatus(office.selected, true, Date.now()).label).toContain('belum terverifikasi')
  })
  it('adds a stage at once, then reloads only the snapshot it changed', async () => {
    await live()
    const event = { position: 2, run_id: 'run_1', task_id: 'tsk_a', phase: 'planning', state: 'running' }
    latest().emit('execution', { project_id: 'prj_a', task_id: 'tsk_a', event })
    expect(office.events.map(e => e.position)).toEqual([1, 2]) // before any request
    await settle(120)
    expect(count('/tasks/tsk_a')).toBe(1)
    expect(count('/office')).toBe(1)
    for (const route of ['/health', '/capabilities', '/tasks/tsk_a/reports', '/tasks/tsk_a/documents'])
      expect(count(route)).toBe(0)
    expect(count('/tasks/tsk_a/execution-events')).toBe(0)
  })
  it('turns a burst into one reload', async () => {
    await live()
    for (let p = 2; p < 12; p++)
      latest().emit('execution', {
        project_id: 'prj_a',
        task_id: 'tsk_a',
        event: { position: p, run_id: 'run_1', task_id: 'tsk_a', phase: 'qa' },
      })
    latest().emit('changed', { project_id: 'prj_a', task_id: 'tsk_a', parts: ['documents', 'task'] })
    await settle(120)
    expect(calls.filter(c => c === '/api/v0/tasks/tsk_a')).toHaveLength(1)
    expect(calls.filter(c => c === '/api/v0/office')).toHaveLength(1)
    expect(count('/tasks/tsk_a/documents')).toBe(1)
    expect(office.events).toHaveLength(11)
  })
  it('does not reload at all for another project in a quiet page', async () => {
    await live()
    latest().emit('changed', {
      project_id: 'prj_z',
      task_id: 'tsk_z',
      parts: ['documents', 'messages', 'reports'],
    })
    await settle(1000)
    expect(calls).toEqual([])
  })
  it('runs the safety poll on time however many events keep arriving', async () => {
    await live()
    for (let elapsed = 0; elapsed < 30000; elapsed += 5000) {
      await settle(5000)
      latest().emit('changed', { project_id: 'prj_a', task_id: 'tsk_a', parts: ['task'] })
    }
    await settle()
    expect(count('/health')).toBe(1)
  })
  it('says so when a reload after an event fails, and recovers by itself', async () => {
    await live()
    const working = fetch.getMockImplementation()
    fetch.mockImplementation(async input =>
      String(input).endsWith('/tasks/tsk_a')
        ? { ok: false, status: 500, json: async () => ({ error: { code: 'internal_error' } }) }
        : working(input),
    )
    latest().emit('changed', { project_id: 'prj_a', task_id: 'tsk_a', parts: ['task'] })
    await settle(120)
    expect(office.connected).toBe(false)
    expect(office.error).toBe('internal_error')
    fetch.mockImplementation(working)
    await settle(6000) // the backoff after one failure
    expect(office.connected).toBe(true)
    expect(office.error).toBe('')
  })
  it('reads a long history to its newest event', async () => {
    history = Array.from({ length: 250 }, (_, i) => ({
      position: i + 1,
      run_id: 'run_1',
      task_id: 'tsk_a',
      phase: i === 249 ? 'reviewing' : 'qa',
    }))
    await live()
    expect(office.events).toHaveLength(250)
    expect(office.events.at(-1).phase).toBe('reviewing')
  })
  it('asks only for what is new on the next full load', async () => {
    await live()
    await quiet(30000)
    expect(calls.filter(c => c.includes('execution-events'))).toEqual([
      '/api/v0/tasks/tsk_a/execution-events?limit=100&after=1',
    ])
  })
  it('fills a hole on the next full load, even after a later event already arrived', async () => {
    await live()
    history.push({ position: 2, run_id: 'run_1', task_id: 'tsk_a', phase: 'planning' })
    const late = { position: 3, run_id: 'run_1', task_id: 'tsk_a', phase: 'analysis' }
    history.push(late)
    latest().emit('execution', { project_id: 'prj_a', task_id: 'tsk_a', event: late }) // position 2 went unheard
    expect(office.events.map(e => e.position)).toEqual([1, 3])
    await quiet(30000)
    expect(office.events.map(e => e.position)).toEqual([1, 2, 3])
  })
  it('never starts a history from an event the stream delivered first', async () => {
    await live()
    history = Array.from({ length: 5 }, (_, i) => ({
      position: i + 1,
      run_id: 'run_2',
      task_id: 'tsk_a',
      phase: 'qa',
    }))
    office.events = [] // what picking a task does: its history is not here yet
    latest().emit('execution', { project_id: 'prj_a', task_id: 'tsk_a', event: history[4] })
    await settle(120)
    expect(office.events.map(e => e.position)).toEqual([1, 2, 3, 4, 5])
  })
  it('polls straight away when the stream is lost, then every 3 seconds', async () => {
    await live()
    latest().fail(FakeSource.CONNECTING)
    expect(office.streaming).toBe(false)
    await settle()
    expect(count('/health')).toBe(1)
    fresh()
    await settle(3000)
    expect(count('/health')).toBe(1)
    latest().fail(FakeSource.OPEN)
    latest().emit('ready', { cursor: 1, replay: true })
    await settle()
    expect(office.streaming).toBe(true)
  })
  it('lets go of its connection while hidden, and comes back when looked at', async () => {
    await live()
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(latest().readyState).toBe(FakeSource.CLOSED)
    expect(office.streaming).toBe(false)
    await settle(3000)
    expect(calls).toEqual([]) // nobody is looking: the slow poll
    await settle(27000)
    expect(count('/health')).toBe(1)
    hidden.mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(latest().readyState).toBe(FakeSource.OPEN)
    expect(FakeSource.made).toHaveLength(2)
    hidden.mockRestore()
  })
  it('does not drop a refresh asked for during another load', async () => {
    await live()
    let open
    gate = new Promise(resolve => (open = resolve))
    const first = office.refresh()
    const second = office.refresh()
    await settle()
    gate = null
    open()
    await Promise.all([first, second])
    await settle()
    expect(calls.filter(c => c === '/api/v0/health').length).toBe(2)
  })
  it('keeps polling after the project changes under a load in progress', async () => {
    await live()
    latest().fail(FakeSource.CONNECTING)
    await settle()
    let open
    gate = new Promise(resolve => (open = resolve))
    const load = office.refresh()
    await settle()
    office.projectId = 'prj_other'
    gate = null
    open()
    await load
    await settle()
    fresh()
    await settle(3000)
    expect(count('/health')).toBe(1)
  })
  it('stops listening and loading when stopped', async () => {
    await live()
    latest().emit('changed', { project_id: 'prj_a', task_id: 'tsk_a', parts: ['task'] })
    office.stop()
    expect(latest().readyState).toBe(FakeSource.CLOSED)
    await settle(60000)
    expect(calls).toEqual([])
  })
})
