// The dashboard's reading of all projects at once, and what one agent is doing right now.
// Every line comes from recorded backend state. A model call reports nothing until it ends, so progress is
// counted in finished calls, and "bersamaan" is computed from the times the backend recorded, never assumed.
import { AGENTS, agentForPhase } from './team.js'
import { documentAgentFor, STATUS_TEXT } from './documents.js'
import {
  PACKAGE_TEXT,
  PART_TEXT,
  PHASE_TEXT,
  WAIT_TEXT,
  errorText,
  packageNote,
  phaseHistory,
  projectStep,
} from './labels.js'

const TASK_WAITS = {
  clarification_required: 'Jawab pertanyaan Amang',
  design_approval: 'Setujui desain',
  approval_required: 'Setujui hasil akhir',
  quality_gate: 'QA atau Reviewer menahan hasil',
  input_required: 'Programmer perlu arahan',
  worker_interrupted: 'Pekerjaan terputus',
}
export const taskWait = t => TASK_WAITS[t?.execution?.wait_reason] || null

/** What needs the owner, across every project. */
export function attention(feed = []) {
  const out = []
  for (const p of feed) {
    const step = projectStep(p)
    if (step.action)
      out.push({
        project_id: p.project_id,
        project: p.name,
        task_id: null,
        text: step.error ? errorText(step.error) : step.text,
      })
    for (const t of p.tasks || [])
      if (t.status === 'waiting')
        out.push({
          project_id: p.project_id,
          project: p.name,
          task_id: t.task_id,
          title: t.title,
          text: taskWait(t) || 'Menunggu Anda',
        })
  }
  return out
}

/** The Lead answers for every project, so it is waiting when any of them is, even if the stage shows another. */
export function withPortfolio(team, feed = []) {
  const lead = team.agents.amang
  if (team.simulated || team.mode === 'offline' || lead.status !== 'standby') return team
  const open = attention(feed).length
  if (!open) return team
  return {
    ...team,
    agents: { ...team.agents, amang: { status: 'waiting', label: `${open} hal menunggu Anda` } },
  }
}

/** With nothing running or picked, the office shows what needs the owner, else the latest task. */
export function stageFallback(feed = []) {
  const tasks = feed.flatMap(p => p.tasks || [])
  const latest = list =>
    [...list].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0]?.task_id || ''
  return latest(tasks.filter(t => t.status === 'waiting')) || latest(tasks)
}

export function duration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s} dtk`
  if (s < 3600) return `${Math.floor(s / 60)} mnt ${s % 60} dtk`
  return `${Math.floor(s / 3600)} jam ${Math.floor((s % 3600) / 60)} mnt`
}

/** After the Loop has numbered its iterations, an unnumbered agent phase is that agent writing its documents. */
function writesDocuments(phase, events) {
  const e = events.at(-1)
  if (!e || e.phase !== phase || e.iteration != null) return false
  return (
    ['qa', 'reviewing'].includes(phase) || (phase === 'programming' && events.some(x => x.iteration != null))
  )
}

function doingNow(phase, iteration, writing) {
  const round = iteration ? `, iterasi ${iteration}` : ''
  if (writing)
    return {
      programming: 'Menulis dokumentasi implementasi',
      qa: 'Menulis rencana dan kasus test',
      reviewing: 'Menulis ulasan kode',
    }[phase]
  return {
    briefing: 'Menyusun brief dan pembagian tugas',
    reporting: 'Menyusun laporan akhir dan rekomendasi',
    planning: 'Menyusun PRD, roadmap, dan rincian kerja',
    analysis: 'Merancang solusi teknis',
    designing: 'Merancang alur, layar, dan design system',
    programming: `Menulis patch${round}`,
    qa: `Memeriksa patch secara statis${round}`,
    verifying: `Menjalankan test dan build di sandbox${round}`,
    recovering: `Memilih langkah pemulihan${round}`,
    reviewing: `Meninjau patch${round}`,
  }[phase]
}

/** The most calls that were really inside the model at one moment, from their recorded start and end. */
export function peakTogether(parts, now = Date.now()) {
  const marks = []
  for (const p of parts) {
    const from = Date.parse(p.started_at),
      to = p.finished_at ? Date.parse(p.finished_at) : p.state === 'running' ? now : null
    if (Number.isFinite(from) && Number.isFinite(to)) marks.push([from, 1], [to, -1])
  }
  let live = 0,
    peak = 0
  // A start at the very moment another ends counts as together: their real clocks cannot tell them apart.
  for (const [, change] of marks.sort((a, b) => a[0] - b[0] || b[1] - a[1])) {
    live += change
    peak = Math.max(peak, live)
  }
  return peak
}

/** One row per recorded model call of the agent's stages, and what those records prove. */
export function designWork(parts = [], catalog = [], docAgent, now = Date.now()) {
  const titles = new Map(catalog.map(s => [s.kind, s.title]))
  const stages = new Set(catalog.filter(s => s.agent === docAgent).map(s => s.stage))
  const mine = parts.filter(p => stages.has(p.stage) || (p.stage === 'division' && docAgent === 'analyst'))
  const rows = mine.map(p => {
    const to = p.finished_at ? Date.parse(p.finished_at) : p.state === 'running' ? now : null
    return {
      key: `${p.stage}/${p.part}/${p.attempt}`,
      stage: p.stage,
      part: p.part,
      attempt: p.attempt,
      state: p.state,
      stateText: PART_TEXT[p.state] || p.state,
      label: p.stage === 'division' ? 'Pembagian kerja' : `Bagian ${p.part}`,
      titles: p.kinds.map(k => titles.get(k) || k),
      reason: p.reason ? errorText(p.reason) : '',
      took: to ? duration(to - Date.parse(p.started_at)) : null,
      model: p.model_id || null,
    }
  })
  const latest = new Map()
  for (const p of mine)
    if (p.stage !== 'division') {
      const k = `${p.stage}/${p.attempt}`
      latest.set(k, [...(latest.get(k) || []), p])
    }
  const notes = [...latest.entries()].map(([k, round]) => {
    const stage = k.split('/')[0],
      attempt = +k.split('/')[1],
      peak = peakTogether(round, now),
      finished = round.filter(p => p.state === 'done').length
    const how = peak > 1 ? `${peak} bagian berjalan bersamaan` : 'berjalan satu per satu'
    return `${PHASE_TEXT[stage] || stage}${attempt > 1 ? `, percobaan ke-${attempt}` : ''}: ${finished} dari ${round.length} bagian selesai, ${how}`
  })
  return { rows, notes, running: mine.filter(p => p.state === 'running').flatMap(p => p.kinds) }
}

export function agentDetail(
  id,
  {
    team,
    task = null,
    events = [],
    documents = [],
    catalog = [],
    models = {},
    feed = [],
    projectName = '',
    now = Date.now(),
  },
) {
  const agent = AGENTS.find(a => a.id === id)
  if (!agent || !team) return null
  const state = team.agents[id] || { status: 'offline', label: 'Offline' },
    working = state.status === 'working'
  const docAgent = documentAgentFor(id),
    recorded = new Map(documents.map(d => [d.kind, d]))
  const design = designWork(task?.design_parts, catalog, docAgent, now)
  // Without a task there is no run to report on, and every document would read as not done.
  const docs = !task
    ? []
    : catalog
        .filter(s => s.agent === docAgent)
        .map(s => {
          const d = recorded.get(s.kind)
          const writingNow = !d && design.running.includes(s.kind)
          return {
            kind: s.kind,
            title: s.title,
            stage: s.stage,
            source: s.source,
            status: d?.status || (writingNow ? 'writing' : 'pending'),
            statusText: writingNow ? 'Sedang ditulis' : STATUS_TEXT[d?.status || 'pending'] || d?.status,
            reason: d?.reason || '',
          }
        })
  const run = events.filter(e => e?.run_id).at(-1)?.run_id,
    latest = events.filter(e => e?.run_id === run),
    history = phaseHistory(latest)
  const steps = history
    .map((e, i) => ({ ...e, until: history[i + 1]?.timestamp || null }))
    .filter(e => agentForPhase(e.phase) === id)
    .map(e => ({
      position: e.position,
      text: PHASE_TEXT[e.phase] || e.phase,
      detail: WAIT_TEXT[e.wait_reason] || (e.iteration ? `iterasi ${e.iteration}` : ''),
      at: e.timestamp,
      took: e.until ? duration(Date.parse(e.until) - Date.parse(e.timestamp)) : null,
    }))
  const execution = task?.execution || {},
    phase = execution.phase
  const writing = working && writesDocuments(phase, latest)
  const doing =
    working && (state.count > 1 || (state.count === 1 && id !== 'programmer'))
      ? state.label
      : working
        ? doingNow(phase, execution.iteration, writing) || state.label
        : state.status === 'standby' && task
          ? 'Belum mendapat giliran pada task ini'
          : state.status === 'done'
            ? 'Bagiannya pada task ini sudah selesai'
            : state.status === 'standby'
              ? 'Tidak ada pekerjaan saat ini'
              : state.label
  const last = working ? history.at(-1) : null
  // Only a stage that writes documents names them: during the Loop the agent is on the patch itself.
  const pending =
    writing || ['briefing', 'planning', 'analysis', 'designing', 'reporting'].includes(working ? phase : '')
      ? docs
          .filter(
            d =>
              d.stage === phase &&
              d.source === 'authored' &&
              (d.status === 'pending' || d.status === 'writing'),
          )
          .map(d => d.title)
      : []
  const generated = docs.filter(d => d.status === 'generated').length
  const projects =
    id === 'amang'
      ? feed.map(p => ({
          project_id: p.project_id,
          name: p.name,
          step: projectStep(p).text,
          running: (p.tasks || []).find(t => t.status === 'running')?.title || null,
          open: attention([p]).map(a => (a.task_id ? `${a.title}: ${a.text}` : a.text)),
        }))
      : []
  const assigned = models[docAgent]
  const work = (task?.packages || [])
    .filter(p => id === 'programmer' || (p.state === 'running' && agentForPhase(p.phase) === id))
    .map(p => ({
      key: p.key,
      title: p.title,
      specialty: p.specialty,
      owns: p.owns,
      files: p.files,
      error: p.error,
      state: p.state,
      stateText: PACKAGE_TEXT[p.state] || p.state,
      phase: p.state === 'running' ? PHASE_TEXT[p.phase] || p.phase : null,
      iteration: p.iteration,
      note: packageNote(p),
    }))
  return {
    design,
    packages: work,
    count: state.count || 0,
    id,
    name: agent.name,
    role: agent.role,
    color: agent.color,
    status: state.status,
    label: state.label,
    doing,
    working,
    project: task ? projectName : '',
    task: task ? task.title || task.description : '',
    task_id: task?.task_id || null,
    simulated: !!team.simulated,
    since: last ? duration(now - Date.parse(last.timestamp)) : null,
    pending,
    steps,
    documents: docs,
    generated,
    model: assigned?.model_id || null,
    projects,
  }
}

/** The same detail, short enough to stand above a character's head. */
export function bubbleLines(detail) {
  if (!detail) return null
  const lines = [detail.doing]
  if (detail.task) lines.push(`${detail.project ? detail.project + ' · ' : ''}${detail.task}`.slice(0, 110))
  if (detail.since) lines.push(`Berjalan ${detail.since}`)
  for (const p of detail.packages.slice(0, 4)) lines.push(`${p.title}: ${p.phase || p.stateText}`)
  for (const n of detail.design.notes.slice(-1)) lines.push(n)
  if (detail.pending.length)
    lines.push(
      `Sedang ditulis: ${detail.pending.slice(0, 3).join(', ')}${detail.pending.length > 3 ? ` +${detail.pending.length - 3}` : ''}`,
    )
  if (detail.documents.length)
    lines.push(`Dokumen selesai: ${detail.generated} dari ${detail.documents.length}`)
  if (detail.projects.length) {
    const open = detail.projects.reduce((n, p) => n + p.open.length, 0)
    lines.push(`Memegang ${detail.projects.length} proyek · ${open} menunggu Anda`)
  }
  if (detail.simulated) lines.push('Simulasi, bukan aktivitas backend')
  return { id: detail.id, title: detail.name, lines }
}
