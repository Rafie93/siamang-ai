// What one stream event means for the page: which parts to reload, and how to apply what it already carries.

/** Everything the page loads. A full refresh is all of these. */
export const ALL_SCOPES = Object.freeze([
  'system',
  'board',
  'tasks',
  'drafts',
  'intake',
  'task',
  'messages',
  'events',
  'reports',
  'documents',
  'stage',
])

/** The scopes an event makes out of date, given what the page is showing. Heartbeats and pings change nothing to load. */
export function scopesFor(type, data, { selectedId, stageId, projectId }) {
  const out = new Set()
  const taskId = data?.task_id
  const thisProject = !!projectId && data?.project_id === projectId
  const selected = !!taskId && taskId === selectedId
  const staged = !!taskId && taskId === stageId && !selected
  if (type === 'lifecycle' || type === 'execution') {
    out.add('board')
    if (thisProject) out.add('tasks')
    if (selected) out.add('task')
    if (staged) out.add('stage')
    return out
  }
  if (type !== 'changed') return out
  for (const part of data.parts || []) {
    if (part === 'project') {
      out.add('board')
      if (thisProject) out.add('intake')
    } else if (part === 'drafts' && thisProject) out.add('drafts')
    else if (part === 'messages') {
      if (taskId && selected) out.add('messages')
      else if (!taskId && thisProject) out.add('intake')
    } else if (part === 'documents') {
      if (selected) out.add('documents')
      if (staged) out.add('stage')
    } else if (part === 'reports') {
      if (thisProject) out.add('tasks') // the approvals list depends on the same evidence
      if (selected) out.add('reports')
    } else if (part === 'task') {
      out.add('board')
      if (thisProject) out.add('tasks')
      if (selected) out.add('task')
      if (staged) out.add('stage')
    }
  }
  return out
}

/** Oldest first and each position once, however the pages and the stream interleave. */
export function mergeEvents(known, incoming) {
  const byPosition = new Map(known.map(e => [e.position, e]))
  for (const e of incoming) byPosition.set(e.position, e)
  return [...byPosition.values()].sort((a, b) => a.position - b.position)
}

/** The events already held for this task, so a page starts after them. A list from another task counts for nothing. */
export const knownEvents = (list, taskId) => (list?.[0]?.task_id === taskId ? list : [])

/**
 * The same task with this heartbeat, or the same object when it is about another run. The backend computes
 * `activity_known` and `stale` only when asked, so a beat the worker really sent has to refresh them here.
 */
export function withHeartbeat(task, beat) {
  const e = task?.execution
  if (!e || task.task_id !== beat.task_id || e.run_id !== beat.run_id || e.state !== 'running') return task
  return {
    ...task,
    execution: {
      ...e,
      heartbeat_at: beat.heartbeat_at,
      activity_known: task.status === 'running',
      stale: false,
    },
  }
}
