export function visualStatus(task, connected = true, now = Date.now()) {
  if (!connected) return { label: 'Koneksi terputus · data lama', pose: 'neutral' }
  if (!task) return { label: 'Belum ada task dipilih', pose: 'neutral' }
  const e = task.execution
  if (e?.stale || e?.recovery_required) return { label: 'Aktivitas tidak diketahui', pose: 'neutral' }
  if (task.status === 'running') {
    if (
      !e?.activity_known ||
      !e.heartbeat_at ||
      !Number.isFinite(Date.parse(e.heartbeat_at)) ||
      now - Date.parse(e.heartbeat_at) > 15000
    )
      return { label: 'Running · aktivitas belum terverifikasi', pose: 'neutral' }
    const labels = {
      briefing: 'Lead menyusun brief',
      reporting: 'Lead menyusun laporan akhir',
      planning: 'Menyusun rencana',
      analysis: 'Menganalisis proyek',
      designing: 'Merancang UI/UX',
      programming: 'Menyusun proposal kode',
      qa: 'Pemeriksaan QA statis',
      verifying: 'QA menjalankan test dan build di sandbox',
      recovering: 'Amang memulihkan run yang gagal',
      reviewing: 'Meninjau proposal',
    }
    return { label: labels[e.phase] || 'Tahap belum dikenal', pose: labels[e.phase] ? e.phase : 'neutral' }
  }
  if (task.status === 'waiting')
    return {
      label:
        {
          clarification_required: 'Amang menunggu jawaban Anda',
          design_approval: 'Menunggu persetujuan desain',
          approval_required: 'Menunggu persetujuan',
          quality_gate: 'Ditahan QA atau Reviewer · perlu arahan Anda',
          input_required: 'Programmer berhenti · perlu arahan Anda',
          worker_interrupted: 'Worker terinterupsi',
        }[e?.wait_reason] || 'Menunggu · alasan belum tersedia',
      pose: 'neutral',
    }
  const completed =
    {
      published: 'Disetujui · PR terbuka di GitHub',
      publishing: 'Disetujui · sedang commit dan push',
      failed: 'Disetujui · gagal masuk ke Git',
    }[task.branch?.publication] || 'Proposal disetujui · belum diterapkan'
  return {
    label:
      { queued: 'Task dalam antrean', completed, failed: 'Task gagal', cancelled: 'Task dibatalkan' }[
        task.status
      ] || 'Status belum dikenal',
    pose: 'neutral',
  }
}
