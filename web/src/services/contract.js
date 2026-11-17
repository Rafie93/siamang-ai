export function validResponse(path, method, data) {
  const route = path.split('?')[0],
    obj = data && typeof data === 'object' && !Array.isArray(data)
  if (!obj) return false
  const task = t =>
    !!t && typeof t.task_id === 'string' && typeof t.status === 'string' && typeof t.description === 'string'
  if (method === 'POST') {
    if (route === '/settings/models')
      return typeof data.agent === 'string' && typeof data.model_id === 'string'
    if (route === '/tasks')
      return task(data.task) && typeof data.duplicate === 'boolean' && data.scheduled === false
    if (route.endsWith('/design-decision'))
      return typeof data.run_id === 'string' && ['approve', 'reject'].includes(data.decision)
    if (route.endsWith('/design-edits/send'))
      return data.mode === 'revision'
        ? task(data.task)
        : data.mode === 'stopped' && data.state === 'scheduled'
    if (/\/diagrams\/\d+$/.test(route))
      return typeof data.content_sha256 === 'string' && typeof data.changed === 'boolean'
    if (route.endsWith('/features'))
      return typeof data.feature_id === 'string' && typeof data.title === 'string'
    if (route === '/projects') return typeof data.project_id === 'string' && typeof data.name === 'string'
    if (route.endsWith('/clone')) return data.state === 'cloning'
    if (route.endsWith('/intake/messages')) return data.state === 'thinking'
    if (route.endsWith('/intake/approve')) return data.state === 'approved'
    if (route.endsWith('/publish')) return data.publication === 'publishing'
    if (/^\/tasks\/[^/]+\/messages$/.test(route)) return data.state === 'scheduled'
    if (/\/drafts$/.test(route)) return typeof data.draft_id === 'string' && data.state === 'thinking'
    if (/\/drafts\/[^/]+\/messages$/.test(route)) return data.state === 'thinking'
    if (route.endsWith('/discard')) return data.state === 'discarded'
    if (route.endsWith('/confirm')) return task(data.task) && typeof data.scheduled === 'boolean'
    if (route.endsWith('/run')) return typeof data.run_id === 'string' && data.state === 'scheduled'
    if (route.endsWith('/cancel')) return task(data) && data.status === 'cancelled'
    if (route.endsWith('/retry')) return task(data.task) && data.scheduled === false
    if (route.startsWith('/approvals/'))
      return (
        typeof data.approval_id === 'string' &&
        ['approved', 'rejected'].includes(data.status) &&
        !!data.provenance
      )
  }
  if (route === '/health') return ['healthy', 'unhealthy'].includes(data.status)
  if (route === '/settings/models')
    return Array.isArray(data.agents) && Array.isArray(data.options) && Array.isArray(data.unreachable)
  if (route === '/capabilities')
    return (
      Array.isArray(data.permissions) &&
      Array.isArray(data.task_statuses) &&
      typeof data.model_configured === 'boolean' &&
      data.proposal_only === true &&
      data.patch_application === false
    )
  if (route.endsWith('/drafts'))
    return (
      Array.isArray(data.items) &&
      data.items.every(
        d => typeof d.draft_id === 'string' && typeof d.state === 'string' && Array.isArray(d.messages),
      )
    )
  if (route.endsWith('/intake')) return Array.isArray(data.messages)
  if (route.endsWith('/messages')) return Array.isArray(data.items)
  if (route === '/office')
    return (
      Array.isArray(data.items) &&
      data.items.every(
        p => typeof p.project_id === 'string' && Array.isArray(p.tasks) && !!p.task_counts && !!p.waiting,
      )
    )
  if (route === '/portfolio')
    return (
      Array.isArray(data.items) &&
      data.items.every(
        p =>
          typeof p.project_id === 'string' &&
          typeof p.name === 'string' &&
          !!p.task_counts &&
          !!p.waiting &&
          Array.isArray(p.features),
      )
    )
  if (route.endsWith('/document-edits'))
    return (
      Array.isArray(data.items) &&
      Array.isArray(data.clipped) &&
      typeof data.divided === 'boolean' &&
      data.items.every(e => typeof e.kind === 'string' && Number.isInteger(e.diagram))
    )
  if (route.endsWith('/documents'))
    return (
      Array.isArray(data.items) &&
      data.items.every(
        d =>
          typeof d.kind === 'string' &&
          typeof d.status === 'string' &&
          typeof d.source === 'string' &&
          typeof d.content === 'string',
      )
    )
  if (route === '/projects')
    return (
      Array.isArray(data.items) &&
      data.items.every(p => typeof p.project_id === 'string' && typeof p.name === 'string')
    )
  if (route === '/tasks') return Array.isArray(data.items) && data.items.every(task)
  if (/^\/tasks\/[^/]+$/.test(route)) return task(data)
  if (route === '/approvals')
    return (
      Array.isArray(data.items) &&
      data.items.every(a => typeof a.run_id === 'string' && typeof a.eligible === 'boolean')
    )
  if (route.endsWith('/reports')) return Array.isArray(data.items) && data.patch_applied === false
  if (route.endsWith('/execution-events'))
    return Array.isArray(data.items) && Number.isInteger(data.next_after)
  return true
}
