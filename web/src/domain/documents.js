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
