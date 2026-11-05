// Clearly-labelled simulation for showcasing the office without a running backend.
// Output is always flagged simulated and never mixed into Tasks, Reports or Approvals.
export const DEMO_SCRIPT = [
  { at: 0, phase: 'queued', state: 'scheduled' },
  { at: 4, phase: 'planning', state: 'running' },
  { at: 14, phase: 'analysis', state: 'running' },
  { at: 24, phase: 'programming', state: 'running', iteration: 1 },
  { at: 36, phase: 'qa', state: 'running', iteration: 1 },
  { at: 45, phase: 'reviewing', state: 'running', iteration: 1 },
  { at: 54, phase: 'verifying', state: 'running', iteration: 1 },
  { at: 64, phase: 'recovering', state: 'running', iteration: 2 },
  { at: 71, phase: 'programming', state: 'running', iteration: 2 },
  { at: 81, phase: 'qa', state: 'running', iteration: 2 },
  { at: 89, phase: 'reviewing', state: 'running', iteration: 2 },
  { at: 97, phase: 'verifying', state: 'running', iteration: 2 },
  { at: 106, phase: 'awaiting_approval', state: 'waiting', wait_reason: 'approval_required' },
  { at: 118, phase: 'finished', state: 'completed' },
]
export const DEMO_CYCLE = 129

export function demoSnapshot(elapsedSeconds) {
  const cycle = Math.floor(Math.max(0, elapsedSeconds) / DEMO_CYCLE),
    t = Math.max(0, elapsedSeconds) % DEMO_CYCLE,
    start = Date.now() - t * 1000
  const run = 'demo_run_' + cycle,
    steps = DEMO_SCRIPT.filter(s => s.at <= t),
    last = steps.at(-1)
  const events = steps.map((s, i) => ({
    position: i + 1,
    run_id: run,
    task_id: 'demo_task_' + cycle,
    timestamp: new Date(start + s.at * 1000).toISOString(),
    state: s.state,
    phase: s.phase,
    iteration: s.iteration ?? null,
    wait_reason: s.wait_reason ?? null,
    simulated: true,
  }))
  const status = { scheduled: 'queued', running: 'running', waiting: 'waiting', completed: 'completed' }[
    last.state
  ]
  const task = {
    task_id: 'demo_task_' + cycle,
    status,
    simulated: true,
    title: 'Simulasi: tambah validasi kalkulator',
    description: 'Simulasi alur kerja tim. Bukan data backend.',
    execution: {
      run_id: run,
      phase: last.phase,
      state: last.state,
      activity_known: last.state === 'running',
      heartbeat_at: new Date().toISOString(),
      stale: false,
      recovery_required: false,
      wait_reason: last.wait_reason ?? null,
      iteration: last.iteration ?? null,
    },
  }
  return { task, events, simulated: true }
}
