import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { router } from '@/router'
import { request, post, items } from '@/services/api.js'
import { visualStatus } from '@/domain/status.js'
import { teamState, agentName } from '@/domain/team.js'
import { demoSnapshot } from '@/domain/demo.js'
import { groupDocuments, documentAgentFor, modelLabel } from '@/domain/documents.js'
import { errorText } from '@/domain/labels.js'
import { agentDetail, attention, bubbleLines, stageFallback, withPortfolio } from '@/domain/office.js'

const API_CONTRACT = 0.6 // what this page needs: a backend started before an upgrade refuses its new actions
const SELECTION = 'siamang.selection'
function remembered() {
  try {
    return JSON.parse(localStorage.getItem(SELECTION)) || {}
  } catch {
    return {}
  }
}
function remember(projectId, taskId) {
  try {
    localStorage.setItem(SELECTION, JSON.stringify({ projectId, taskId }))
  } catch {}
}

export const useOfficeStore = defineStore('office', () => {
  const go = name => router.push({ name })
  const projects = ref([]),
    tasks = ref([]),
    projectId = ref(''),
    selectedId = ref(''),
    selected = ref(null),
    reports = ref([]),
    approvals = ref([]),
    events = ref([]),
    cap = ref(null)
  const connected = ref(false),
    loading = ref(true),
    busy = ref(false),
    error = ref(''),
    notice = ref(''),
    updated = ref(null),
    description = ref(''),
    priority = ref('normal'),
    motion = ref(!matchMedia('(prefers-reduced-motion: reduce)').matches),
    quality = ref('high'),
    filter = ref(''),
    offset = ref(0),
    confirm = ref(null),
    reason = ref('')
  const clock = ref(Date.now())
  let freshnessTimer
  const demo = ref(false),
    demoStart = ref(0),
    focusAgent = ref('')
  const feed = ref([]),
    feedCurrent = ref(null),
    stagePin = ref(''),
    live = ref(null),
    agentPopup = ref(''),
    bubbleAgent = ref('')
  const taskError = ref(''),
    detailRequest = ref(0),
    backendOutdated = ref(false),
    projectDialog = ref(false),
    projectError = ref(''),
    draft = ref(null),
    leadBusy = ref(false)
  const newProject = ref({ name: '', repository: '' }),
    intake = ref(null),
    intakeDraft = ref(''),
    taskMessages = ref([]),
    taskDraft = ref(''),
    revisesId = ref('')
  const portfolio = ref([]),
    documents = ref([]),
    documentAgent = ref('lead'),
    documentKinds = ref([]),
    featureId = ref(''),
    featureTitle = ref(''),
    repositoryDraft = ref({})
  const documentGroups = computed(() => groupDocuments(cap.value?.documents, documents.value))
  const activeGroup = computed(() => documentGroups.value.find(g => g.id === documentAgent.value))
  const features = computed(() => portfolio.value.find(p => p.project_id === projectId.value)?.features || [])
  const model = computed(() => modelLabel(cap.value))
  const currentProject = computed(() => portfolio.value.find(p => p.project_id === projectId.value) || null)
  const hasRepository = computed(() => !!currentProject.value?.repository)
  const specApproved = computed(() => !hasRepository.value || currentProject.value?.spec_state === 'approved')
  const revisable = computed(() => tasks.value.filter(t => t.branch?.publication === 'published'))
  const clarificationPending = computed(
    () =>
      selected.value?.status === 'waiting' &&
      selected.value?.execution?.wait_reason === 'clarification_required',
  )
  const stopped = computed(
    () =>
      selected.value?.status === 'waiting' &&
      ['clarification_required', 'quality_gate', 'input_required', 'worker_interrupted'].includes(
        selected.value?.execution?.wait_reason,
      ),
  )
  const leadAvailable = computed(() => !!cap.value?.agent_models?.lead)
  const SPEC_TEXT = {
    thinking: 'Lead sedang berpikir…',
    gathering: 'Lead menunggu jawaban Anda',
    pending_approval: 'Spesifikasi menunggu persetujuan',
    approved: 'Spesifikasi disetujui',
    failed: 'Terhenti',
  }
  async function createProject() {
    if (!newProject.value.name.trim() || !newProject.value.repository.trim() || !can('submit')) return
    busy.value = true
    projectError.value = ''
    try {
      const p = await post('/projects', {
        name: newProject.value.name.trim(),
        repository: newProject.value.repository.trim(),
      })
      newProject.value = { name: '', repository: '' }
      projectDialog.value = false
      projectId.value = p.project_id
      router.push({ name: 'project-detail', params: { id: p.project_id } })
      await changeProject()
    } catch (e) {
      projectError.value = e.code || 'project_failed'
    } finally {
      busy.value = false
    }
  }
  function openProjectDialog() {
    projectError.value = ''
    projectDialog.value = true
  }
  async function sendIntake() {
    if (!intakeDraft.value.trim() || !can('submit')) return
    busy.value = true
    error.value = ''
    try {
      await post('/projects/' + projectId.value + '/intake/messages', { content: intakeDraft.value.trim() })
      intakeDraft.value = ''
      await refresh()
    } catch (e) {
      error.value = e.code || 'message_failed'
    } finally {
      busy.value = false
    }
  }
  async function approveSpec() {
    if (!can('approve')) return
    busy.value = true
    error.value = ''
    try {
      await post('/projects/' + projectId.value + '/intake/approve')
      notice.value = 'Spesifikasi disetujui. Sekarang Anda bisa membuat task.'
      await refresh()
    } catch (e) {
      error.value = e.code || 'approve_failed'
    } finally {
      busy.value = false
    }
  }
  async function retryClone() {
    busy.value = true
    error.value = ''
    try {
      await post('/projects/' + projectId.value + '/clone')
      await refresh()
    } catch (e) {
      error.value = e.code || 'clone_failed'
    } finally {
      busy.value = false
    }
  }
  async function sendTaskMessage(text) {
    const content = (typeof text === 'string' ? text : taskDraft.value).trim()
    if (!content || !can('run')) return
    busy.value = true
    taskError.value = ''
    try {
      await post('/tasks/' + selectedId.value + '/messages', { content })
      taskDraft.value = ''
      notice.value = 'Pesan terkirim. Tim melanjutkan pekerjaan.'
      await refresh()
    } catch (e) {
      taskError.value = e.code || 'message_failed'
    } finally {
      busy.value = false
    }
  }
  async function sendDraft(text) {
    const content = (text ?? description.value).trim()
    if (!content || !can('submit')) return
    busy.value = true
    error.value = ''
    try {
      if (draft.value) {
        await post(`/projects/${projectId.value}/drafts/${draft.value.draft_id}/messages`, { content })
      } else {
        await post(`/projects/${projectId.value}/drafts`, {
          content,
          priority: priority.value,
          ...(featureId.value ? { feature_id: featureId.value } : {}),
          ...(revisesId.value ? { revises_task_id: revisesId.value } : {}),
        })
        description.value = ''
      }
      await refresh()
    } catch (e) {
      error.value = e.code || 'draft_failed'
    } finally {
      busy.value = false
    }
  }
  async function confirmDraft() {
    if (!draft.value || !can('submit')) return
    busy.value = true
    error.value = ''
    try {
      const r = await post(`/projects/${projectId.value}/drafts/${draft.value.draft_id}/confirm`)
      draft.value = null
      revisesId.value = ''
      selectedId.value = r.task.task_id
      notice.value = r.scheduled
        ? 'Task dibuat dan tim mulai bekerja.'
        : 'Task dibuat, tetapi belum berjalan: ' + errorText(r.reason)
      go('tasks')
      await refresh()
    } catch (e) {
      error.value = e.code || 'confirm_failed'
    } finally {
      busy.value = false
    }
  }
  async function discardDraft() {
    if (!draft.value || !can('submit')) return
    busy.value = true
    error.value = ''
    try {
      await post(`/projects/${projectId.value}/drafts/${draft.value.draft_id}/discard`)
      draft.value = null
      await refresh()
    } catch (e) {
      error.value = e.code || 'discard_failed'
    } finally {
      busy.value = false
    }
  }
  function startRevision(text) {
    revisesId.value = selectedId.value
    description.value = text
    go('command')
  }
  async function redivide() {
    if (!can('run')) return
    busy.value = true
    error.value = ''
    try {
      await post('/tasks/' + selectedId.value + '/work-division')
      await refresh()
    } catch (e) {
      error.value = e.code || 'redivide_failed'
    } finally {
      busy.value = false
    }
  }
  async function retryPublish() {
    if (!can('approve')) return
    busy.value = true
    error.value = ''
    try {
      await post('/tasks/' + selectedId.value + '/publish')
      await refresh()
    } catch (e) {
      error.value = e.code || 'publish_failed'
    } finally {
      busy.value = false
    }
  }
  const designPending = computed(
    () =>
      selected.value?.status === 'waiting' && selected.value?.execution?.wait_reason === 'design_approval',
  )
  function showDocuments(officeId) {
    documentAgent.value = documentAgentFor(officeId) || 'lead'
    documentKinds.value = []
    go('documents')
  }
  async function createFeature() {
    if (!featureTitle.value.trim() || !can('submit')) return
    busy.value = true
    error.value = ''
    try {
      await post('/projects/' + projectId.value + '/features', { title: featureTitle.value.trim() })
      featureTitle.value = ''
      await refresh()
    } catch (e) {
      error.value = e.code || 'feature_failed'
    } finally {
      busy.value = false
    }
  }
  async function linkRepository(p) {
    const repository = (repositoryDraft.value[p.project_id] || '').trim()
    if (!repository || !can('submit')) return
    busy.value = true
    error.value = ''
    try {
      await post('/projects/' + p.project_id + '/repository', { repository })
      repositoryDraft.value[p.project_id] = ''
      notice.value = 'Repository ditautkan. Belum ada akses GitHub yang dipakai.'
      await refresh()
    } catch (e) {
      error.value = e.code || 'repository_failed'
    } finally {
      busy.value = false
    }
  }
  // The stage is the task the office shows: the one the owner picked, else whichever is running in any project.
  const stageId = computed(
    () => stagePin.value || feedCurrent.value?.task_id || stageFallback(feed.value) || selectedId.value,
  )
  const stage = computed(() => {
    if (demo.value) return { ...demoSnapshot((clock.value - demoStart.value) / 1000), documents: [] }
    if (live.value?.task?.task_id === stageId.value) return live.value
    return { task: selected.value, events: events.value, documents: documents.value }
  })
  const stageProject = computed(() =>
    stage.value.simulated
      ? ''
      : feed.value.find(p => p.project_id === stage.value.task?.project_id)?.name ||
        project.value?.name ||
        '',
  )
  const stageSubject = computed(() =>
    stage.value.task
      ? `${stageProject.value ? stageProject.value + ' · ' : ''}${stage.value.task.title || stage.value.task.description}`.slice(
          0,
          120,
        )
      : '',
  )
  const team = computed(() =>
    demo.value
      ? teamState({ ...stage.value, connected: true, simulated: true, now: clock.value })
      : loading.value && !connected.value
        ? { ...teamState({ connected: false }), label: 'Menghubungkan ke backend…' }
        : withPortfolio(
            teamState({
              task: stage.value.task,
              events: stage.value.events,
              connected: connected.value,
              now: clock.value,
            }),
            feed.value,
          ),
  )
  const needsOwner = computed(() => attention(feed.value))
  const runningCount = computed(() => feed.value.reduce((n, p) => n + (p.task_counts.running || 0), 0))
  const taskCount = computed(() =>
    feed.value.reduce((n, p) => n + Object.values(p.task_counts).reduce((a, b) => a + b, 0), 0),
  )
  const approvalCount = computed(() => feed.value.reduce((n, p) => n + (p.waiting.approval_required || 0), 0))
  function agentInfo(id) {
    return agentDetail(id, {
      team: team.value,
      task: stage.value.task,
      events: stage.value.events,
      documents: stage.value.documents,
      catalog: cap.value?.documents || [],
      models: cap.value?.agent_models || {},
      feed: demo.value ? [] : feed.value,
      projectName: stageProject.value,
      now: clock.value,
    })
  }
  const agentBubble = computed(() => (bubbleAgent.value ? bubbleLines(agentInfo(bubbleAgent.value)) : null))
  function openAgent(id) {
    focusAgent.value = id
    bubbleAgent.value = ''
    agentPopup.value = id
  }
  function selectCharacter(id) {
    focusAgent.value = id || ''
    bubbleAgent.value = id || ''
  }
  function watchTask(id) {
    stagePin.value = id
    return refresh()
  }
  async function openTask(id, taskId) {
    if (id !== projectId.value) {
      projectId.value = id
      await changeProject()
    }
    selectedId.value = taskId
    resetTask()
    go('tasks')
    await refresh()
  }
  function openProject(id) {
    router.push({ name: 'project-detail', params: { id } })
  }
  const teamLog = computed(() => team.value.transitions.slice(-6).reverse())
  function toggleDemo() {
    demo.value = !demo.value
    demoStart.value = Date.now()
    clock.value = Date.now()
  }
  const statusText = {
    working: 'Bekerja',
    standby: 'Standby',
    done: 'Selesai',
    waiting: 'Menunggu',
    blocked: 'Ditahan',
    offline: 'Offline',
  }
  let timer,
    disposed = false,
    refreshing = false,
    failures = 0,
    submission = null
  const status = computed(() => {
    void clock.value
    return visualStatus(selected.value, connected.value)
  })
  const permissions = computed(() => cap.value?.permissions || [])
  const can = p => connected.value && permissions.value.includes(p) && !busy.value
  const project = computed(() => projects.value.find(p => p.project_id === projectId.value))
  const active = computed(() => tasks.value.filter(t => t.status === 'running').length)

  async function refresh() {
    if (refreshing || disposed) return
    refreshing = true
    try {
      const [health, caps, ps, pf, board] = await Promise.all([
        request('/health'),
        request('/capabilities'),
        request('/projects'),
        request('/portfolio'),
        request('/office').catch(() => ({ items: [], current: null })),
      ])
      feed.value = items(board)
      feedCurrent.value = board.current
      portfolio.value = items(pf).filter(p => p.status !== 'archived')
      if (health.status !== 'healthy') throw Error('backend_unhealthy')
      backendOutdated.value = Number(health.contract_version || 0) < API_CONTRACT
      if (!Array.isArray(caps.permissions)) throw Error('malformed_response')
      cap.value = caps
      projects.value = items(ps).filter(p => p.status !== 'archived')
      if (!projectId.value) {
        const kept = remembered()
        if (projects.value.some(p => p.project_id === kept.projectId)) {
          projectId.value = kept.projectId
          selectedId.value = kept.taskId || ''
        } else projectId.value = projects.value[0]?.project_id || ''
      }
      intake.value = portfolio.value.find(p => p.project_id === projectId.value)?.repository
        ? await request('/projects/' + projectId.value + '/intake')
        : null
      if (projectId.value) {
        const id = projectId.value
        const [ts, aps, ds] = await Promise.all([
          request(
            `/tasks?project_id=${id}&limit=20&offset=${offset.value}${filter.value ? '&status=' + filter.value : ''}`,
          ),
          request(`/approvals?project_id=${id}`),
          request(`/projects/${id}/drafts`).catch(() => ({ items: [] })),
        ])
        if (id !== projectId.value) return
        tasks.value = items(ts)
        approvals.value = items(aps)
        draft.value = items(ds)[0] || null
        if (!selectedId.value && tasks.value.length) selectedId.value = tasks.value[0].task_id
        remember(id, selectedId.value)
        if (selectedId.value) {
          const taskId = selectedId.value
          const [t, r, e, d] = await Promise.all([
            request('/tasks/' + taskId),
            request('/tasks/' + taskId + '/reports'),
            request('/tasks/' + taskId + '/execution-events?limit=100'),
            request('/tasks/' + taskId + '/documents'),
          ])
          if (taskId === selectedId.value) {
            taskMessages.value = t.branch ? items(await request('/tasks/' + taskId + '/messages')) : []
            if (!t.task_id || typeof t.status !== 'string') throw Error('malformed_response')
            selected.value = t
            reports.value = items(r)
            events.value = items(e)
            documents.value = items(d)
          }
        }
      }
      // The office may show a task outside the project picked on the other pages.
      const shown = stageId.value
      if (shown && shown !== selectedId.value) {
        const [t, e, d] = await Promise.all([
          request('/tasks/' + shown),
          request('/tasks/' + shown + '/execution-events?limit=100'),
          request('/tasks/' + shown + '/documents'),
        ])
        if (!t.task_id) throw Error('malformed_response')
        live.value = { task: t, events: items(e), documents: items(d) }
      } else live.value = null
      connected.value = true
      updated.value = new Date()
      failures = 0
      error.value = ''
    } catch (e) {
      connected.value = false
      error.value = e.code || e.message || 'connection_failed'
      failures++
    } finally {
      loading.value = false
      refreshing = false
      if (!disposed) {
        clearTimeout(timer)
        timer = setTimeout(refresh, Math.min(3000 * 2 ** failures, 30000))
      }
    }
  }
  async function changeProject() {
    draft.value = null
    intake.value = null
    intakeDraft.value = ''
    revisesId.value = ''
    taskMessages.value = []
    featureId.value = ''
    documents.value = []
    documentKinds.value = []
    selectedId.value = ''
    selected.value = null
    reports.value = []
    events.value = []
    tasks.value = []
    approvals.value = []
    offset.value = 0
    await refresh()
  }
  async function choose(t) {
    detailRequest.value++
    taskError.value = ''
    selectedId.value = t.task_id
    selected.value = t
    reports.value = []
    events.value = []
    documents.value = []
    documentKinds.value = []
    await refresh()
  }
  async function submit() {
    if (!description.value.trim() || !can('submit')) return
    busy.value = true
    error.value = ''
    const payload = {
      project_id: projectId.value,
      description: description.value.trim(),
      priority: priority.value,
      ...(featureId.value ? { feature_id: featureId.value } : {}),
      ...(revisesId.value ? { revises_task_id: revisesId.value } : {}),
    }
    const signature = JSON.stringify(payload)
    if (submission?.signature !== signature)
      submission = { signature, id: 'req_' + crypto.randomUUID().replaceAll('-', '') }
    try {
      const r = await post('/tasks', { ...payload, request_id: submission.id })
      selectedId.value = r.task.task_id
      notice.value = 'Task diterima. Jalankan secara eksplisit saat siap.'
      description.value = ''
      revisesId.value = ''
      submission = null
      go('tasks')
      await refresh()
    } catch (e) {
      error.value = e.code || 'submit_failed'
    } finally {
      busy.value = false
    }
  }
  function ask(action, id) {
    confirm.value = { action, id }
    reason.value = ''
  }
  async function act() {
    const { action, id } = confirm.value
    busy.value = true
    error.value = ''
    try {
      if (['approve', 'reject'].includes(action))
        await post('/approvals/' + id, { decision: action, reason: reason.value || undefined })
      else if (action === 'design-approve' || action === 'design-reject')
        await post('/tasks/' + id + '/design-decision', {
          decision: action === 'design-approve' ? 'approve' : 'reject',
          reason: reason.value || undefined,
        })
      else await post('/tasks/' + id + '/' + action)
      notice.value =
        action === 'approve'
          ? selected.value?.branch
            ? 'Proposal disetujui. Siamang membuat commit dan membuka PR.'
            : 'Proposal disetujui. Patch tidak diterapkan.'
          : action === 'design-approve'
            ? 'Desain disetujui. Programmer mulai bekerja.'
            : action === 'design-reject'
              ? 'Desain ditolak. Task dibatalkan.'
              : 'Backend mengonfirmasi aksi ' + action
      confirm.value = null
      await refresh()
    } catch (e) {
      error.value = e.code || 'action_failed'
    } finally {
      busy.value = false
    }
  }
  function localTime(value) {
    return value
      ? new Date(value).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      : '—'
  }
  function resetTask() {
    selected.value = null
    reports.value = []
    events.value = []
    documents.value = []
    documentKinds.value = []
  }
  function pickProject(id) {
    projectId.value = id
    return changeProject()
  }
  function pickTask(id) {
    selectedId.value = id
    resetTask()
    return refresh()
  }
  function setFilter(value) {
    filter.value = value
    offset.value = 0
    return refresh()
  }
  function setOffset(value) {
    offset.value = value
    return refresh()
  }
  function openProjectTasks(p) {
    pickProject(p.project_id)
    go('tasks')
  }
  function readEvidence(a) {
    selectedId.value = a.task_id
    refresh()
    go('reports')
  }
  function openTaskDocuments() {
    documentKinds.value = []
    go('documents')
  }
  function pickDocumentAgent(id) {
    documentAgent.value = id
    documentKinds.value = []
  }
  function start() {
    disposed = false
    refresh()
    freshnessTimer = setInterval(() => (clock.value = Date.now()), 1000)
  }
  function stop() {
    disposed = true
    clearTimeout(timer)
    clearInterval(freshnessTimer)
  }
  return {
    redivide,
    startRevision,
    feed,
    stagePin,
    stageId,
    stage,
    stageSubject,
    needsOwner,
    runningCount,
    taskCount,
    approvalCount,
    agentPopup,
    bubbleAgent,
    agentBubble,
    agentInfo,
    openAgent,
    selectCharacter,
    watchTask,
    openTask,
    openProject,
    taskError,
    detailRequest,
    backendOutdated,
    projectDialog,
    projectError,
    openProjectDialog,
    draft,
    leadAvailable,
    stopped,
    sendDraft,
    confirmDraft,
    discardDraft,
    projects,
    tasks,
    projectId,
    selectedId,
    selected,
    reports,
    approvals,
    events,
    cap,
    connected,
    loading,
    busy,
    error,
    notice,
    updated,
    description,
    priority,
    motion,
    quality,
    filter,
    offset,
    confirm,
    reason,
    demo,
    focusAgent,
    newProject,
    intake,
    intakeDraft,
    taskMessages,
    taskDraft,
    revisesId,
    portfolio,
    documents,
    documentAgent,
    documentKinds,
    featureId,
    featureTitle,
    repositoryDraft,
    documentGroups,
    activeGroup,
    features,
    model,
    currentProject,
    hasRepository,
    specApproved,
    revisable,
    clarificationPending,
    designPending,
    team,
    teamLog,
    status,
    permissions,
    project,
    active,
    SPEC_TEXT,
    statusText,
    can,
    go,
    refresh,
    createProject,
    sendIntake,
    approveSpec,
    retryClone,
    sendTaskMessage,
    retryPublish,
    showDocuments,
    createFeature,
    linkRepository,
    toggleDemo,
    choose,
    submit,
    ask,
    act,
    localTime,
    agentName,
    visualStatus,
    pickProject,
    pickTask,
    setFilter,
    setOffset,
    openProjectTasks,
    readEvidence,
    openTaskDocuments,
    pickDocumentAgent,
    start,
    stop,
  }
})
