// Words for the owner. Backend codes stay in the API; nothing here decides anything.
export const ERROR_TEXT = {
  invalid_repository: 'Alamat repo tidak dikenali. Pakai bentuk https://github.com/pemilik/nama-repo.',
  repository_not_found: 'Repo tidak ditemukan, atau akun gh Anda tidak punya akses ke repo itu.',
  repository_not_writable:
    'Akun gh Anda hanya bisa membaca repo ini, jadi Siamang tidak bisa membuat branch di sana.',
  repository_not_empty: 'Repo sudah berisi branch saat hendak diberi commit pertama. Coba clone lagi.',
  repository_timeout: 'GitHub terlalu lama merespons. Coba lagi.',
  clone_failed: 'Clone repo gagal. Periksa koneksi dan login gh Anda.',
  gh_not_installed: 'Perintah gh tidak ditemukan di komputer ini.',
  git_not_installed: 'Perintah git tidak ditemukan di komputer ini.',
  interrupted: 'Terputus karena server dimatikan. Kirim lagi untuk melanjutkan.',
  lead_has_no_model: 'Lead belum diberi model. Pilih di halaman Settings.',
  model_not_available: 'Model itu tidak tersedia lagi. Periksa ollama serve lalu coba lagi.',
  config_not_editable: 'siamang.toml tidak bisa diubah dari sini. Ubah langsung di file-nya.',
  config_invalid: 'siamang.toml tidak valid. Perbaiki file-nya dulu.',
  lead_returned_empty_brief: 'Amang tidak menghasilkan ringkasan. Kirim pesan lagi untuk mencoba ulang.',
  rate_limited: 'Batas pemakaian model tercapai. Coba lagi nanti.',
  model_timeout: 'Model terlalu lama menjawab. Coba lagi.',
  timeout: 'Model terlalu lama menjawab.',
  model_omitted_document: 'Model tidak menulis dokumen yang diminta.',
  model_returned_empty_document: 'Model mengembalikan dokumen kosong.',
  spec_not_approved: 'Spesifikasi proyek belum disetujui.',
  worker_busy: 'Tim masih mengerjakan task lain. Jalankan setelah task itu selesai.',
  draft_not_ready: 'Amang belum selesai memahami instruksi ini.',
  draft_not_open: 'Percakapan ini sudah ditutup.',
  clarification_not_pending:
    'Task ini sedang tidak menunggu jawaban, atau backend belum di-restart ke versi terbaru.',
  message_failed: 'Pesan gagal terkirim. Coba lagi.',
  programmer_model_timeout: 'Programmer terlalu lama menjawab, bahkan setelah dicoba lagi.',
  programmer_model_invalid_output: 'Jawaban Programmer tidak bisa dibaca sebagai patch.',
  programmer_model_unavailable: 'Model Programmer tidak tersedia.',
  programmer_change_rejected: 'Perubahan dari Programmer tidak lolos pemeriksaan.',
  patch_stale: 'File di repo sudah berubah sejak patch dibuat.',
  redivide_failed: 'Pembagian kerja belum bisa diulang. Coba lagi.',
  design_not_pending: 'Diagram task ini sedang tidak bisa diubah.',
  document_changed:
    'Dokumen ini sudah berubah sejak editor dibuka. Salin kode Anda, tutup editor, lalu buka lagi.',
  diagram_invalid: 'Kode diagram tidak bisa disimpan: kosong, terlalu panjang, atau memuat baris ```.',
  diagram_not_found: 'Diagram ini tidak ditemukan di dokumen.',
  no_design_edits: 'Tidak ada perubahan diagram yang belum dikirim.',
  diagram_save_failed: 'Diagram gagal disimpan. Coba lagi.',
  push_failed: 'Push ke GitHub gagal.',
  pull_request_failed: 'Commit sudah terkirim, tetapi PR gagal dibuka.',
}
export const errorText = code => ERROR_TEXT[code] || code || 'Terjadi kesalahan.'

export const PACKAGE_TEXT = {
  pending: 'Menunggu giliran',
  running: 'Sedang dikerjakan',
  passed: 'Lulus',
  waiting: 'Butuh jawaban Anda',
  blocked: 'Ditahan QA atau Reviewer',
  failed: 'Gagal',
}
export const CI_TEXT = {
  watching: 'Build sedang berjalan di GitHub',
  success: 'Build dan test lulus',
  failed: 'Build atau test gagal',
  none: 'Repo ini belum punya workflow CI',
  timeout: 'Build terlalu lama, cek langsung di GitHub',
}
export function fileSize(bytes) {
  if (!Number.isFinite(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}
export const PHASE_TEXT = {
  queued: 'Masuk antrean',
  clarification: 'Amang bertanya',
  briefing: 'Lead menyusun brief',
  planning: 'Planner menyusun rencana',
  analysis: 'Arsitek merancang',
  designing: 'Desainer merancang UI/UX',
  design_approval: 'Menunggu persetujuan desain',
  programming: 'Programmer menulis kode',
  qa: 'QA memeriksa',
  verifying: 'QA menjalankan test di sandbox',
  recovering: 'Amang memulihkan run',
  reviewing: 'Reviewer meninjau',
  reporting: 'Lead menyusun laporan',
  awaiting_approval: 'Menunggu persetujuan akhir',
  blocked: 'Berhenti, perlu arahan Anda',
  interrupted: 'Terputus',
  finished: 'Selesai',
  failed: 'Gagal',
  cancelled: 'Dibatalkan',
}
export const PART_TEXT = {
  running: 'Sedang ditulis',
  done: 'Selesai',
  failed: 'Gagal',
  interrupted: 'Terputus',
}
export const WAIT_TEXT = {
  clarification_required: 'menunggu jawaban Anda',
  design_approval: 'menunggu persetujuan Anda',
  approval_required: 'menunggu persetujuan Anda',
  quality_gate: 'QA atau Reviewer menahan hasil',
  input_required: 'Programmer berhenti tanpa pertanyaan',
  worker_interrupted: 'server dimatikan',
}

// A stop the owner can answer, and what the panel tells them about it.
export const STOP_TEXT = {
  clarification_required: {
    title: 'Amang butuh jawaban Anda',
    body: 'Pekerjaan lanjut begitu Anda menjawab.',
  },
  quality_gate: {
    title: 'QA dan Reviewer menahan hasil ini',
    body: 'Temuannya ada di percakapan. Beri arahan agar Programmer mengerjakannya lagi, atau lanjutkan tanpa arahan untuk mencoba sekali lagi.',
  },
  input_required: {
    title: 'Programmer berhenti sebelum menulis kode',
    body: 'Run ini dibuat oleh versi lama yang tidak menampilkan file ke Programmer dan tidak mencatat pertanyaannya. Desain yang sudah Anda setujui tetap dipakai.',
  },
  worker_interrupted: {
    title: 'Pekerjaan terputus',
    body: 'Server dimatikan saat task ini berjalan. Lanjutkan dari tahap terakhir yang sudah aman.',
  },
}

/** Consecutive rows of one phase say nothing new to a reader, so the first of each run stands for it. */
export function phaseHistory(events) {
  const out = []
  for (const e of events || []) {
    if (
      out.at(-1)?.phase === e.phase &&
      out.at(-1)?.wait_reason === e.wait_reason &&
      (out.at(-1)?.attempt || 1) === (e.attempt || 1)
    )
      continue
    out.push(e)
  }
  return out
}

/** "gelombang 2 · percobaan ke-2" for a package: waves run in order, a package is tried again before it is cut in two. */
export function packageNote(p) {
  const parts = []
  if (p?.wave > 0) parts.push(`gelombang ${p.wave}`)
  if (p?.attempt > 1) parts.push(`percobaan ke-${p.attempt}`)
  return parts.join(' · ')
}

/** "percobaan ke-2 · Model terlalu lama menjawab" for a repeated step, '' for a first and only try. */
export function attemptText(e) {
  const kept = e?.reused > 0 ? `${e.reused} dokumen dari run sebelumnya dipakai ulang` : ''
  if (!(e?.attempt > 1)) return kept
  return [
    `percobaan ke-${e.attempt}` +
      (e.retry_reason ? ` · setelah gagal: ${errorText(e.retry_reason)}` : '') +
      (e.remaining ? ` · mengulang ${e.remaining} dokumen` : ''),
    kept,
  ]
    .filter(Boolean)
    .join(' · ')
}

/** Why a failed task stopped, with the stage and how many tries it had. */
export function failureText(details, phaseText = PHASE_TEXT) {
  let cause = errorText(details?.cause)
  if (details?.cause === 'programmer_change_rejected' && details.reason) {
    const where = details.where?.length ? ` (${details.where.join(', ')})` : ''
    cause += ` ${details.reason}${where}`
  }
  return details?.attempts > 1
    ? `${phaseText[details.stage] || details.stage || 'Tahap ini'} gagal setelah ${details.attempts} percobaan. ${cause}`
    : cause
}

/** owner/name when the text is a GitHub repository, otherwise null. Same rule as the API. */
export function repositorySlug(text) {
  const m =
    /^(?:https:\/\/github\.com\/)?([A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/.exec(
      (text || '').trim(),
    )
  return m ? m[1] : null
}

/** Where a project stands and what its owner should do next. */
export function projectStep(p) {
  const repo = p?.repository
  if (!repo) return { step: 4, text: 'Siap menerima task', action: false }
  if (repo.state === 'failed')
    return { step: 0, text: 'Gagal menautkan repo', error: repo.error, action: true }
  if (repo.state === 'cloning') return { step: 0, text: 'Menautkan repo…', action: false }
  const state = p.spec_state
  if (state === 'failed') return { step: 1, text: 'Amang terhenti', action: true }
  if (state === 'thinking') return { step: 1, text: 'Amang membaca dan berpikir…', action: false }
  if (state === 'gathering') return { step: 2, text: 'Amang menunggu jawaban Anda', action: true }
  if (state === 'pending_approval') return { step: 3, text: 'Spesifikasi menunggu persetujuan', action: true }
  if (state === 'approved') return { step: 4, text: 'Siap menerima task', action: false }
  return { step: 1, text: 'Menyiapkan proyek…', action: false }
}
export const PROJECT_STEPS = [
  'Tautkan repo',
  'Amang membaca repo',
  'Jawab pertanyaan',
  'Setujui spesifikasi',
  'Siap menerima task',
]

const short = q => (q.length > 90 ? q.slice(0, 87) + '…' : q)
/** The answers as one message: the Lead reads which question each one belongs to. */
export function composeAnswers(questions, picked, notes, extra = '') {
  const lines = questions.map((q, i) => {
    const parts = [...(picked[i] || []), (notes[i] || '').trim()].filter(Boolean)
    return `${i + 1}. ${short(q.question)}\nJawaban: ${parts.join('; ') || 'Terserah Amang, pilih yang paling masuk akal.'}`
  })
  if (extra.trim()) lines.push('Catatan: ' + extra.trim())
  return lines.join('\n').slice(0, 4000)
}

/** Questions still open are answered through the form, so the thread does not repeat them. */
export function withoutOpenQuestions(messages) {
  const last = messages?.at(-1)
  if (last?.role !== 'lead' || !last.questions?.length) return messages || []
  return [...messages.slice(0, -1), { ...last, questions: [] }]
}
export const openQuestions = messages => {
  const last = messages?.at(-1)
  return last?.role === 'lead' ? last.questions || [] : []
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g
export function parseInline(text) {
  return text
    .split(INLINE)
    .filter(Boolean)
    .map(part => {
      if (part.startsWith('**') && part.endsWith('**') && part.length > 4)
        return { type: 'strong', text: part.slice(2, -2) }
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2)
        return { type: 'code', text: part.slice(1, -1) }
      const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part)
      return link ? { type: 'link', text: link[1], href: link[2] } : { type: 'text', text: part }
    })
}
/** Enough Markdown for a specification. Rendered as nodes, never as HTML, because a model wrote it. */
export function parseMarkdown(text) {
  const blocks = [],
    lines = (text || '').split('\n')
  let i = 0
  const special = l => /^(#{1,6}\s|```|\s*[-*]\s|\s*\d+[.)]\s|>\s?|\|)/.test(l)
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] })
      i++
      continue
    }
    if (line.startsWith('```')) {
      const lang = (/^```\s*([A-Za-z]+)\s*$/.exec(line)?.[1] || '').toLowerCase(),
        body = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++])
      i++
      const text = body.join('\n')
      blocks.push(
        lang === 'mermaid'
          ? { type: 'mermaid', text }
          : lang
            ? { type: 'code', lang, text }
            : { type: 'code', text },
      )
      continue
    }
    if (/^\s*[-*]\s/.test(line) || /^\s*\d+[.)]\s/.test(line)) {
      const ordered = /^\s*\d/.test(line),
        items = []
      while (i < lines.length && (ordered ? /^\s*\d+[.)]\s/ : /^\s*[-*]\s/).test(lines[i]))
        items.push(lines[i++].replace(/^\s*(?:[-*]|\d+[.)])\s+/, ''))
      blocks.push({ type: 'list', ordered, items })
      continue
    }
    if (line.startsWith('>')) {
      const body = []
      while (i < lines.length && lines[i].startsWith('>')) body.push(lines[i++].replace(/^>\s?/, ''))
      blocks.push({ type: 'quote', text: body.join(' ') })
      continue
    }
    if (line.startsWith('|')) {
      const rows = []
      while (i < lines.length && lines[i].startsWith('|')) {
        const cells = lines[i++]
          .replace(/^\||\|\s*$/g, '')
          .split('|')
          .map(c => c.trim())
        if (!cells.every(c => /^:?-+:?$/.test(c))) rows.push(cells)
      }
      blocks.push({ type: 'table', head: rows[0] || [], rows: rows.slice(1) })
      continue
    }
    const body = [line]
    i++
    while (i < lines.length && lines[i].trim() && !special(lines[i])) body.push(lines[i++])
    blocks.push({ type: 'paragraph', text: body.join(' ') })
  }
  return blocks
}
