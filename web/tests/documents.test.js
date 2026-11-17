import { describe, it, expect } from 'vitest'
import {
  blockSummary,
  diagramTitle,
  diagramType,
  documentAgentFor,
  documentType,
  groupDocuments,
  modelLabel,
  outlineOf,
  searchHit,
} from '../src/domain/documents.js'
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
  expect(groups).toHaveLength(7)
  expect(by('designer')).toMatchObject({ name: 'Desainer', documents: [] }) // this catalog fixture has none
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

it('refuses a malformed answer about diagram edits', () => {
  expect(validResponse('/tasks/t/documents/erd/diagrams/0', 'POST', { changed: true })).toBe(false)
  expect(
    validResponse('/tasks/t/documents/erd/diagrams/0', 'POST', { changed: true, content_sha256: 'a' }),
  ).toBe(true)
  expect(validResponse('/tasks/t/document-edits', 'GET', { items: [] })).toBe(false)
  expect(validResponse('/tasks/t/document-edits', 'GET', { items: [], clipped: [], divided: false })).toBe(
    true,
  )
})

it('reads both answers to sending changed diagrams', () => {
  const send = '/tasks/t/design-edits/send'
  expect(validResponse(send, 'POST', { mode: 'stopped', state: 'scheduled' })).toBe(true)
  expect(validResponse(send, 'POST', { mode: 'stopped' })).toBe(false)
  expect(
    validResponse(send, 'POST', {
      mode: 'revision',
      task: { task_id: 't', status: 'queued', description: 'd' },
    }),
  ).toBe(true)
  expect(validResponse(send, 'POST', { mode: 'revision' })).toBe(false)
})

describe('finding one picture among many', () => {
  const SEQS = [
    '# Sequence Diagram',
    'Pengantar.',
    '## Login',
    '```mermaid\nsequenceDiagram\n  A->>B: masuk\n```',
    '## Bayar',
    'Alur bayar.',
    '```mermaid\n---\ntitle: Bayar kartu\n---\nsequenceDiagram\n  A->>B: bayar\n```',
    '```mermaid\n%% catatan\nflowchart TD\n  A --> B\n```',
    '```json\n{"a": 1}\n```',
  ].join('\n\n')
  it('names each diagram by the heading above it, its own title, or its number, and lists nothing twice', () => {
    const outline = outlineOf(SEQS)
    expect(outline.map(e => [e.id, e.label, e.kind || ''])).toEqual([
      ['h0', 'Sequence Diagram', ''],
      ['b0', 'Login', 'Sequence'],
      ['h2', 'Bayar', ''],
      ['b1', 'Bayar kartu', 'Sequence'],
      ['b2', 'Diagram 3', 'Flowchart'],
    ])
    expect(outline.find(e => e.id === 'b1').level).toBe(3) // under its heading
  })
  it('counts the blocks a document allows, as MarkdownText numbers them', () => {
    expect(outlineOf(SEQS, ['mermaid', 'json']).at(-1)).toMatchObject({
      id: 'b3',
      lang: 'json',
      label: 'Token 4',
    })
    expect(blockSummary(outlineOf(SEQS))).toBe('3 diagram')
    expect(
      blockSummary(outlineOf('```html\n<p/>\n```\n\n```mermaid\nflowchart TD\n```', ['mermaid', 'html'])),
    ).toBe('1 layar')
    expect(documentType(outlineOf('teks saja'))).toBe('text')
    expect(documentType(outlineOf(SEQS))).toBe('diagram')
    expect(documentType(outlineOf(SEQS, ['mermaid', 'json']))).toBe('wireframe')
  })
  it('knows the common diagram kinds and titles', () => {
    expect(diagramType('erDiagram\n  A ||--o{ B : x')).toBe('ERD')
    expect(diagramType('graph LR\n  A-->B')).toBe('Flowchart')
    expect(diagramType('stateDiagram-v2\n  [*] --> A')).toBe('State')
    expect(diagramType('bukan diagram')).toBe('Diagram')
    expect(diagramTitle('flowchart TD\n  title Alur utama\n  A-->B')).toBe('Alur utama')
    expect(diagramTitle('flowchart TD')).toBe('')
  })
  it('searches titles and text, and shows the words around a match', () => {
    const doc = { kind: 'sequence_diagram', title: 'Sequence Diagram', content: SEQS }
    expect(searchHit(doc, '')).toEqual({ match: true, snippet: '' })
    expect(searchHit(doc, 'sequence')).toEqual({ match: true, snippet: '' })
    const hit = searchHit(doc, 'BAYAR KARTU')
    expect(hit.match).toBe(true)
    expect(hit.snippet).toContain('Bayar kartu')
    expect(hit.snippet.startsWith('…')).toBe(true)
    expect(searchHit(doc, 'tidak ada').match).toBe(false)
    expect(
      searchHit({ title: 'X', content: '', reason: 'Tidak relevan untuk task ini.' }, 'relevan').match,
    ).toBe(true)
  })
})
