// Explicit opt-in integration test: real Ollama, isolated local office database.
// Never sends approval or applies patches. Run only against the QA launcher.
import { mkdirSync, writeFileSync } from 'node:fs'
const base = process.env.WEB_URL || 'http://127.0.0.1:5180'
const session = await fetch(base + '/session')
const cookie = session.headers.get('set-cookie').split(';')[0]
const { csrf } = await session.json()
async function api(path, body) {
  const response = await fetch(base + '/api/v0' + path, {
    method: body ? 'POST' : 'GET',
    headers: { Cookie: cookie, Origin: base, 'X-Web-CSRF': csrf, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json()
  if (!response.ok) throw Error(JSON.stringify(data))
  return data
}
const project = (await api('/projects')).items.find(p => p.name === 'Office Sandbox')
if (!project) throw Error('Sandbox required')
const submitted = await api('/tasks', {
  project_id: project.project_id,
  description:
    'Implement add(a, b) returning a + b in backend/calculator.py. Add meaningful tests for positive, negative and zero numbers in tests/test_calculator.py. Only modify these two files. Produce a minimal proposal.',
  request_id: 'req_live' + Date.now(),
})
const taskId = submitted.task.task_id
await api('/tasks/' + taskId + '/run', {})
let task
const started = Date.now()
let previous
while (Date.now() - started < 180000) {
  task = await api('/tasks/' + taskId)
  if (task.execution.phase !== previous) {
    previous = task.execution.phase
    console.log(task.status, previous, task.execution.wait_reason || '')
  }
  if (['waiting', 'failed', 'cancelled', 'completed'].includes(task.status)) break
  await new Promise(r => setTimeout(r, 1000))
}
if (!task || ['queued', 'running'].includes(task.status))
  throw Error('Live run did not finish within 180 seconds')
const reports = await api('/tasks/' + taskId + '/reports')
const events = await api('/tasks/' + taskId + '/execution-events')
const result = {
  task_id: taskId,
  status: task.status,
  execution: task.execution,
  phases: events.items.map(e => e.phase),
  qa: reports.items.at(-1)?.qa?.status,
  review: reports.items.at(-1)?.review?.status,
  patch_applied: reports.patch_applied,
  approval_sent: false,
  elapsed_seconds: (Date.now() - started) / 1000,
}
mkdirSync('test-results', { recursive: true })
writeFileSync('test-results/live-ollama.json', JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify(result, null, 2))
if (task.status === 'failed') process.exitCode = 1
