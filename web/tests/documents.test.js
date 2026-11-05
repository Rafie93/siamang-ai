import { it, expect } from 'vitest'
import { groupDocuments, documentAgentFor, modelLabel } from '../src/domain/documents.js'
import { teamState, agentForPhase } from '../src/domain/team.js'
import { visualStatus } from '../src/domain/status.js'
import { validResponse } from '../src/services/contract.js'
import { permitted } from '../scripts/gateway.mjs'
const catalog = [
  { agent: 'planner', kind: 'prd', title: 'PRD', source: 'authored', stage: 'planning' },
  { agent: 'qa', kind: 'coverage_report', title: 'Coverage', source: 'measured', stage: 'qa' },
]
it('never invents a result for a catalog entry the backend did not record', () => {
  const groups = groupDocuments(catalog, [
    { kind: 'prd', status: 'generated', content: 'isi', source: 'authored' },
  ])
  const by = id => groups.find(g => g.id === id)
  expect(by('planner').documents[0].status).toBe('generated')
  expect(by('planner').generated).toBe(1)
  expect(by('qa').documents[0]).toMatchObject({ status: 'pending', content: '' })
  expect(by('qa').generated).toBe(0)
  expect(groups).toHaveLength(6)
})
it('maps office characters to document owners', () => {
  expect(documentAgentFor('amang')).toBe('lead')
  expect(documentAgentFor('qa')).toBe('qa')
  expect(documentAgentFor('')).toBe('')
})
it('labels the model the backend reports, not a fixed vendor', () => {
  expect(modelLabel(null)).toBe('Model belum terverifikasi')
  expect(modelLabel({ model_configured: false, agent_models: { qa: { model_id: 'x' } } })).toBe(
    'Model belum terverifikasi',
  )
  expect(
    modelLabel({
      model_configured: true,
      agent_models: {
        qa: { model_id: 'claude-sonnet' },
        lead: { model_id: 'claude-sonnet' },
        reviewer: { model_id: 'claude-opus' },
      },
    }),
  ).toBe('claude-sonnet, claude-opus')
  expect(modelLabel({ model_configured: true })).toBe('Model terkonfigurasi')
})
it('shows the lead working on briefing and waiting at the design gate', () => {
  expect(agentForPhase('briefing')).toBe('amang')
  expect(agentForPhase('reporting')).toBe('amang')
  const task = {
    task_id: 't',
    status: 'waiting',
    execution: { wait_reason: 'design_approval', phase: 'design_approval' },
  }
  const team = teamState({ task, events: [], connected: true })
  expect(team.agents.amang.label).toBe('Menunggu persetujuan desain')
  expect(team.approval).toBe(false)
  expect(team.activeAgent).toBe(null)
  expect(visualStatus(task, true).label).toBe('Menunggu persetujuan desain')
})
it('gateway opens only the named new routes', () => {
  for (const [m, p] of [
    ['GET', '/v0/portfolio'],
    ['GET', '/v0/projects/prj_a/features'],
    ['GET', '/v0/tasks/tsk_a/documents'],
    ['POST', '/v0/tasks/tsk_a/design-decision'],
    ['POST', '/v0/projects/prj_a/features'],
    ['POST', '/v0/projects'],
    ['POST', '/v0/projects/prj_a/clone'],
    ['GET', '/v0/projects/prj_a/intake'],
    ['POST', '/v0/projects/prj_a/intake/messages'],
    ['POST', '/v0/projects/prj_a/intake/approve'],
    ['GET', '/v0/tasks/tsk_a/messages'],
    ['POST', '/v0/tasks/tsk_a/messages'],
    ['POST', '/v0/tasks/tsk_a/publish'],
  ])
    expect(permitted(m, p)).toBe(true)
  for (const [m, p] of [
    ['POST', '/v0/portfolio'],
    ['POST', '/v0/projects/prj_a'],
    ['POST', '/v0/projects/prj_a/shell'],
    ['POST', '/v0/tasks/tsk_a/documents'],
    ['POST', '/v0/projects/prj_a/repository'],
    ['GET', '/v0/projects/prj_a/clone'],
    ['POST', '/v0/projects/prj_a/intake'],
    ['GET', '/v0/tasks/tsk_a/publish'],
    ['POST', '/v0/projects/../features'],
  ])
    expect(permitted(m, p)).toBe(false)
})
it('refuses malformed responses on the new routes', () => {
  expect(validResponse('/portfolio', 'GET', { items: [{ project_id: 'p', name: 'n' }] })).toBe(false)
  expect(validResponse('/tasks/t/documents', 'GET', { items: [{ kind: 'prd' }] })).toBe(false)
  expect(validResponse('/tasks/t/design-decision', 'POST', { success: true })).toBe(false)
  expect(validResponse('/projects', 'POST', { success: true })).toBe(false)
  expect(validResponse('/projects/p/intake/approve', 'POST', { state: 'pending_approval' })).toBe(false)
  expect(validResponse('/tasks/t/publish', 'POST', { publication: 'published' })).toBe(false)
  expect(validResponse('/projects/p/intake', 'GET', {})).toBe(false)
})
it('reports Git truthfully after approval', () => {
  const task = branch => ({ task_id: 't', status: 'completed', execution: {}, branch })
  expect(visualStatus(task({ publication: 'published' }), true).label).toBe(
    'Disetujui · PR terbuka di GitHub',
  )
  expect(visualStatus(task({ publication: 'failed' }), true).label).toBe('Disetujui · gagal masuk ke Git')
  expect(visualStatus(task(null), true).label).toBe('Proposal disetujui · belum diterapkan')
})
it('shows the lead waiting while a task needs answers', () => {
  const task = {
    task_id: 't',
    status: 'waiting',
    execution: { wait_reason: 'clarification_required', phase: 'clarification' },
  }
  const team = teamState({ task, events: [], connected: true })
  expect(agentForPhase('clarification')).toBe('amang')
  expect(team.agents.amang.label).toBe('Menunggu jawaban Anda')
  expect(team.activeAgent).toBe(null)
})
