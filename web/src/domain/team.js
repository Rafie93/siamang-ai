// Maps backend task + execution events onto the six office agents.
// Truthfulness rules (PRD §1): an agent is only "working" when the backend reports a fresh,
// known activity for its phase. Handoffs are derived only from persisted execution events.
import { visualStatus } from './status.js'

export const AGENTS = [
  { id: 'amang', name: 'Amang', role: 'Lead Agent', phase: null, color: '#2f3b33' },
  { id: 'planner', name: 'Planner', role: 'Perencana', phase: 'planning', color: '#d08a3c' },
  { id: 'analyst', name: 'Arsitek', role: 'System Analyst', phase: 'analysis', color: '#3d7fb8' },
  { id: 'programmer', name: 'Programmer', role: 'Coding', phase: 'programming', color: '#7b5bc4' },
  { id: 'qa', name: 'QA', role: 'Quality Assurance', phase: 'qa', color: '#3f9a63' },
  { id: 'reviewer', name: 'Reviewer', role: 'Code Review', phase: 'reviewing', color: '#c4534f' },
]
const byPhase = {
  queued: 'amang',
  clarification: 'amang',
  briefing: 'amang',
  design_approval: 'amang',
  reporting: 'amang',
  planning: 'planner',
  analysis: 'analyst',
  programming: 'programmer',
  qa: 'qa',
  verifying: 'qa',
  recovering: 'amang',
  reviewing: 'reviewer',
  awaiting_approval: 'amang',
  blocked: 'qa',
  finished: 'amang',
}
export const agentForPhase = phase => byPhase[phase] || null
export const agentName = id => AGENTS.find(a => a.id === id)?.name || id

const messages = {
  'amang>planner': ['Brief task diteruskan ke Planner', 'Siap, saya susun rencana.'],
  'planner>analyst': ['Rencana kerja diserahkan ke Arsitek', 'Saya analisis strukturnya.'],
  'analyst>programmer': ['Analisis & desain siap untuk Programmer', 'Oke, saya susun patch.'],
  'programmer>qa': ['Proposal patch dikirim ke QA', 'Saya periksa kualitasnya.'],
  'qa>reviewer': ['Hasil QA diteruskan ke Reviewer', 'Saya review proposalnya.'],
  'reviewer>programmer': ['Revisi diminta untuk iterasi berikutnya', 'Siap, saya perbaiki.'],
  'qa>programmer': ['Temuan QA dikembalikan ke Programmer', 'Siap, saya perbaiki.'],
  'amang>programmer': ['Desain disetujui, silakan mulai coding', 'Siap, saya kerjakan.'],
  'analyst>amang': ['Dokumen desain selesai untuk ditinjau', 'Terima kasih, saya ajukan ke operator.'],
  'reviewer>amang': ['Proposal lolos review · siap disetujui', 'Terima kasih, saya ajukan ke operator.'],
  'qa>amang': ['Quality gate menahan proposal', 'Dicatat, butuh tindak lanjut.'],
}
// Phases whose meaning does not follow from who hands to whom: running the code, and stepping in after a failure.
const phaseMessages = {
  verifying: ['Lolos review · patch dijalankan di sandbox', 'Saya jalankan test dan build di container.'],
  recovering: ['Test di sandbox gagal · Amang mengambil alih', 'Saya pilih langkah pemulihan.'],
  afterRecovery: ['Perbaiki berdasarkan hasil test yang gagal', 'Siap, saya coba lagi dengan catatan itu.'],
}
export function handoffText(from, to, phase, previousPhase) {
  const key = previousPhase === 'recovering' && to === 'programmer' ? 'afterRecovery' : phase
  return (
    phaseMessages[key] ||
    messages[from + '>' + to] || [`${agentName(from)} menyerahkan pekerjaan ke ${agentName(to)}`, 'Diterima.']
  )
}

// Consecutive persisted events that change the owning agent become one handoff.
export function transitions(events = []) {
  const out = []
  let previous = null,
    previousPhase = null,
    run = null
  for (const e of events) {
    if (!e || typeof e.phase !== 'string') continue
    const agent = agentForPhase(e.phase)
    if (e.run_id !== run) {
      run = e.run_id
      previous = agent
      previousPhase = e.phase
      continue
    }
    if (agent && previous && agent !== previous) {
      const [say, reply] = handoffText(previous, agent, e.phase, previousPhase)
      out.push({
        key: `${e.run_id}:${e.position}`,
        from: previous,
        to: agent,
        phase: e.phase,
        at: e.timestamp,
        iteration: e.iteration ?? null,
        say,
        reply,
        kind:
          e.phase === 'programming' && (previous !== 'analyst' || previousPhase === 'recovering')
            ? 'revision'
            : e.phase === 'recovering'
              ? 'recovery'
              : e.phase === 'awaiting_approval'
                ? 'report'
                : e.phase === 'blocked'
                  ? 'blocked'
                  : 'handoff',
      })
    }
    if (agent) previous = agent
    previousPhase = e.phase
  }
  return out
}

const outcomes = { completed: 'completed', failed: 'failed', cancelled: 'cancelled' }
export function teamState({
  task = null,
  events = [],
  connected = true,
  simulated = false,
  now = Date.now(),
} = {}) {
  const agents = Object.fromEntries(
    AGENTS.map(a => [
      a.id,
      { status: connected ? 'standby' : 'offline', label: connected ? 'Standby' : 'Offline · data lama' },
    ]),
  )
  const base = {
    taskKey: task?.task_id || null,
    simulated,
    agents,
    activeAgent: null,
    approval: false,
    outcome: null,
    transitions: [],
    mode: 'idle',
    label: '',
    phase: null,
    iteration: null,
  }
  if (!connected) return { ...base, mode: 'offline', label: 'Koneksi terputus · tim tidak dianggap bekerja' }
  if (!task) return { ...base, label: 'Belum ada task dipilih · tim standby' }
  const list = transitions(events),
    visual = visualStatus(task, true, now),
    e = task.execution || {}
  const latestRun = events.filter(x => x && x.run_id).at(-1)?.run_id
  for (const id of new Set(
    events
      .filter(x => x?.run_id === latestRun)
      .map(x => agentForPhase(x.phase))
      .filter(Boolean),
  ))
    if (id !== 'amang') Object.assign(agents[id], { status: 'done', label: 'Bagian selesai' })
  const state = {
    ...base,
    transitions: list,
    label: visual.label,
    mode: 'live',
    phase: task.status === 'running' && visual.pose !== 'neutral' ? visual.pose : null,
    iteration: e.iteration ?? null,
  }
  if (task.status === 'running') {
    const id = agentForPhase(visual.pose)
    if (id) {
      state.activeAgent = id
      Object.assign(agents[id], { status: 'working', label: visual.label })
    } else state.mode = 'unverified'
    if (id) {
      for (const p of task.packages || []) {
        const owner = p.state === 'running' ? agentForPhase(p.phase) : null
        if (!owner) continue
        const agent = agents[owner]
        const count = (agent.count || 0) + 1
        Object.assign(agent, {
          status: 'working',
          count,
          label: count > 1 ? `Mengerjakan ${count} paket` : `Paket ${p.title}`,
        })
      }
    }
  } else if (task.status === 'waiting') {
    if (e.wait_reason === 'clarification_required')
      Object.assign(agents.amang, { status: 'waiting', label: 'Menunggu jawaban Anda' })
    else if (e.wait_reason === 'design_approval')
      Object.assign(agents.amang, { status: 'waiting', label: 'Menunggu persetujuan desain' })
    else if (e.wait_reason === 'approval_required') {
      state.approval = true
      Object.assign(agents.amang, { status: 'waiting', label: 'Menunggu persetujuan Anda' })
    } else if (e.wait_reason === 'quality_gate')
      Object.assign(agents.qa, { status: 'blocked', label: 'Quality gate menahan proposal' })
    else Object.assign(agents.amang, { status: 'waiting', label: visual.label })
  } else if (outcomes[task.status]) state.outcome = outcomes[task.status]
  else if (task.status === 'queued')
    Object.assign(agents.amang, { status: 'waiting', label: 'Task di antrean · belum dijalankan' })
  return state
}
