import { parseMarkdown } from './labels.js'

// Joins the backend's document catalog with what a run actually recorded.
// A catalog entry with no record is "pending": the UI never invents a result for it.
export const DOCUMENT_AGENTS = [
  { id: 'lead', officeId: 'amang', name: 'Amang', role: 'Lead Agent' },
  { id: 'planner', officeId: 'planner', name: 'Planner', role: 'Perencana' },
  { id: 'analyst', officeId: 'analyst', name: 'Arsitek', role: 'System Analyst' },
  { id: 'designer', officeId: 'designer', name: 'Desainer', role: 'UI/UX Designer' },
  { id: 'programmer', officeId: 'programmer', name: 'Programmer', role: 'Implementasi' },
  { id: 'qa', officeId: 'qa', name: 'QA', role: 'Pengujian' },
  { id: 'reviewer', officeId: 'reviewer', name: 'Reviewer', role: 'Evaluasi' },
]
export const documentAgentFor = officeId => DOCUMENT_AGENTS.find(a => a.officeId === officeId)?.id || ''
export const STATUS_TEXT = {
  generated: 'Dihasilkan',
  not_applicable: 'Tidak relevan',
  not_run: 'Belum dijalankan',
  not_assigned: 'Belum diberi model',
  failed: 'Gagal',
  pending: 'Belum sampai tahap ini',
}
export const SOURCE_TEXT = { authored: 'Tulisan model', derived: 'Dihitung sistem', measured: 'Hasil ukur' }

export function groupDocuments(catalog = [], items = []) {
  const recorded = new Map(items.map(d => [d.kind, d]))
  return DOCUMENT_AGENTS.map(agent => {
    const documents = catalog
      .filter(s => s.agent === agent.id)
      .map(s => ({
        ...s,
        status: 'pending',
        content: '',
        reason: '',
        model_id: null,
        ...recorded.get(s.kind),
      }))
    return { ...agent, documents, generated: documents.filter(d => d.status === 'generated').length }
  })
}

export function modelLabel(capabilities) {
  if (!capabilities?.model_configured) return 'Model belum terverifikasi'
  const names = [...new Set(Object.values(capabilities.agent_models || {}).map(m => m.model_id))]
  return names.length ? names.join(', ') : 'Model terkonfigurasi'
}

// Finding one picture among many: a document's table of contents, and a search over titles and text.
const DIAGRAM_TYPES = [
  [/^sequenceDiagram\b/, 'Sequence'],
  [/^(flowchart|graph)\b/, 'Flowchart'],
  [/^classDiagram\b/, 'Class'],
  [/^erDiagram\b/, 'ERD'],
  [/^stateDiagram/, 'State'],
  [/^journey\b/, 'Journey'],
  [/^gantt\b/, 'Gantt'],
  [/^C4\w*/, 'C4'],
  [/^mindmap\b/, 'Mindmap'],
  [/^pie\b/, 'Pie'],
  [/^gitGraph\b/, 'Git'],
  [/^timeline\b/, 'Timeline'],
]
const BLOCK_NOUN = { mermaid: 'Diagram', html: 'Layar', json: 'Token' }

// The diagram's own lines: front matter, comments and blank lines are not what it draws.
function meaningful(source) {
  const lines = (source || '').split('\n')
  let i = 0
  if (lines[0]?.trim() === '---') {
    i = 1
    while (i < lines.length && lines[i].trim() !== '---') i++
    i++
  }
  return lines.slice(i).filter(l => l.trim() && !l.trim().startsWith('%%'))
}

/** What kind of Mermaid diagram the source draws, such as 'Sequence', or 'Diagram'. */
export function diagramType(source) {
  const first = meaningful(source)[0]?.trim() || ''
  return DIAGRAM_TYPES.find(([re]) => re.test(first))?.[1] || 'Diagram'
}

/** The title a Mermaid diagram gives itself, in its front matter or a `title` line; '' when it has none. */
export function diagramTitle(source) {
  const front = /^---\s*\n[\s\S]*?^title:\s*(.+?)\s*$[\s\S]*?^---/m.exec(source || '')
  if (front) return front[1].replace(/^["']|["']$/g, '')
  const line = meaningful(source).find(l => /^\s*title\s+\S/.test(l))
  return line ? line.trim().replace(/^title\s+/, '') : ''
}

/**
 * A document's headings and drawn blocks in reading order, numbered exactly as MarkdownText renders them (its
 * `anchor` ids). A block is named by the heading just above it, which then drops out so nothing is listed twice.
 */
export function outlineOf(text, blocks = ['mermaid']) {
  const entries = []
  let headings = 0,
    drawn = 0,
    open = null
  for (const b of parseMarkdown(text)) {
    if (b.type === 'heading') {
      open = { id: `h${headings++}`, type: 'heading', level: b.level, label: b.text }
      entries.push(open)
      continue
    }
    const lang = b.type === 'mermaid' ? 'mermaid' : b.type === 'code' && blocks.includes(b.lang) ? b.lang : ''
    if (!lang) continue
    const titled = lang === 'mermaid' ? diagramTitle(b.text) : ''
    const label = titled || open?.label || `${BLOCK_NOUN[lang]} ${drawn + 1}`
    if (open && !titled) open.absorbed = true
    entries.push({
      id: `b${drawn}`,
      type: 'block',
      lang,
      index: drawn++,
      level: (open?.level || 0) + 1,
      label,
      kind: lang === 'mermaid' ? diagramType(b.text) : BLOCK_NOUN[lang],
      source: b.text,
    })
    open = null
  }
  return entries.filter(e => !e.absorbed)
}

/** How many of each drawn block a document holds, as the list shows it: '20 diagram', '3 layar', 'token'. */
export function blockSummary(outline) {
  const count = lang => outline.filter(e => e.type === 'block' && e.lang === lang).length
  if (count('html')) return `${count('html')} layar`
  if (count('mermaid')) return `${count('mermaid')} diagram`
  return count('json') ? 'token' : ''
}

/** Which filter a document falls under. */
export function documentType(outline) {
  if (outline.some(e => e.lang === 'html' || e.lang === 'json')) return 'wireframe'
  return outline.some(e => e.lang === 'mermaid') ? 'diagram' : 'text'
}

/** Whether a document matches the search, and the words around the first match in its text. */
export function searchHit(doc, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return { match: true, snippet: '' }
  if ((doc.title || '').toLowerCase().includes(q) || (doc.kind || '').toLowerCase().includes(q))
    return { match: true, snippet: '' }
  const text = doc.content || doc.reason || ''
  const at = text.toLowerCase().indexOf(q)
  if (at < 0) return { match: false, snippet: '' }
  const from = Math.max(0, at - 40),
    to = Math.min(text.length, at + q.length + 60)
  const snippet =
    (from ? '…' : '') + text.slice(from, to).replace(/\s+/g, ' ').trim() + (to < text.length ? '…' : '')
  return { match: true, snippet }
}
