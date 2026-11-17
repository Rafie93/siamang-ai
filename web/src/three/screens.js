import * as THREE from 'three'
// Procedural monitor content. Decorative only: it shows *which kind* of work a role does,
// never claims real output. Content animates only while the backend reports that agent working.
const W = 256,
  H = 160
const palettes = {
  amang: '#9fd6b6',
  planner: '#f2c27b',
  analyst: '#8ec5f0',
  designer: '#f2a6d4',
  programmer: '#c7b2ff',
  qa: '#8fe0a8',
  reviewer: '#f2a19d',
}
export function createScreen(role) {
  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d'),
    texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  let last = ''
  function draw(status, t = 0, view = {}) {
    const key =
      status + (status === 'working' ? Math.floor(t * 5) : '') + (view.phase || '') + (view.iteration || '')
    if (key === last || !ctx) return
    last = key
    const accent = palettes[role] || '#ccc'
    ctx.fillStyle = status === 'offline' ? '#111' : '#132420'
    ctx.fillRect(0, 0, W, H)
    if (status === 'offline') {
      ctx.fillStyle = '#444'
      ctx.font = '18px monospace'
      ctx.fillText('offline', 90, 85)
      texture.needsUpdate = true
      return
    }
    ctx.fillStyle = '#1d332d'
    ctx.fillRect(0, 0, W, 18)
    ctx.fillStyle = accent
    ctx.font = 'bold 11px sans-serif'
    ctx.fillText(role.toUpperCase(), 8, 13)
    ctx.fillStyle =
      status === 'working' ? '#7dffa8' : status === 'waiting' || status === 'blocked' ? '#ffc65c' : '#6b7d77'
    ctx.beginPath()
    ctx.arc(W - 12, 9, 4, 0, 7)
    ctx.fill()
    if (status !== 'working') {
      ctx.globalAlpha = 0.55
      drawStatic(ctx, role, accent)
      ctx.globalAlpha = 1
      ctx.fillStyle = '#d9e8e1'
      ctx.font = '12px sans-serif'
      ctx.fillText(
        status === 'done'
          ? '✓ bagian selesai'
          : status === 'waiting'
            ? 'menunggu…'
            : status === 'blocked'
              ? 'quality gate'
              : 'standby',
        8,
        H - 10,
      )
      texture.needsUpdate = true
      return
    }
    const step = Math.floor(t * 5)
    if (role === 'qa' && view.phase === 'verifying') drawSandbox(ctx, step)
    else if (role === 'amang' && view.phase === 'recovering') drawRecovery(ctx, step, view.iteration)
    else if (role === 'programmer') {
      for (let i = 0; i < 9; i++) {
        const n = (i + step) % 17
        ctx.fillStyle = ['#c7b2ff', '#7dd3fc', '#fca5a5', '#d9f99d'][n % 4]
        ctx.fillRect(10 + (n % 3) * 12, 26 + i * 14, 40 + ((n * 37) % 120), 7)
      }
      ctx.fillStyle = '#fff'
      ctx.fillRect(10 + ((step * 9) % 150), 26 + 8 * 14, 6, 9)
    } else if (role === 'qa') {
      for (let i = 0; i < 7; i++) {
        const ok = i < step % 9
        ctx.fillStyle = ok ? '#7dffa8' : '#3b524b'
        ctx.fillRect(10, 28 + i * 17, 10, 10)
        ctx.fillStyle = '#cfe'
        ctx.fillRect(28, 30 + i * 17, 80 + ((i * 23) % 90), 6)
      }
    } else if (role === 'reviewer') {
      for (let i = 0; i < 8; i++) {
        const add = (i + step) % 3 === 0,
          del = (i + step) % 5 === 0
        ctx.fillStyle = add ? '#1f4d33' : del ? '#5a2626' : '#132420'
        ctx.fillRect(6, 26 + i * 15, W - 12, 13)
        ctx.fillStyle = add ? '#8fe0a8' : del ? '#f2a19d' : '#9fb3ad'
        ctx.fillRect(14, 31 + i * 15, 60 + ((i * 41) % 130), 5)
      }
    } else if (role === 'analyst') {
      ctx.strokeStyle = accent
      ctx.lineWidth = 2
      const nodes = [
        [40, 50],
        [128, 40],
        [210, 60],
        [80, 115],
        [180, 120],
      ]
      for (let i = 0; i < nodes.length; i++) {
        const [x, y] = nodes[i]
        ctx.strokeRect(x - 22, y - 12, 44, 24)
        if (i) (ctx.beginPath(), ctx.moveTo(...nodes[i - 1]), ctx.lineTo(x, y), ctx.stroke())
      }
      ctx.fillStyle = accent
      const k = step % nodes.length
      ctx.fillRect(nodes[k][0] - 22, nodes[k][1] - 12, 44, 24)
    } else if (role === 'designer') {
      // A wireframe taking shape: frame, header, cards, and a swatch row that cycles.
      ctx.strokeStyle = accent
      ctx.lineWidth = 2
      ctx.strokeRect(14, 24, 150, 124)
      ctx.fillStyle = '#3b524b'
      ctx.fillRect(20, 30, 138, 16)
      for (let i = 0; i < 4; i++) {
        ctx.fillStyle = i <= step % 5 ? accent : '#2c4a3f'
        ctx.fillRect(20 + (i % 2) * 70, 54 + Math.floor(i / 2) * 44, 64, 38)
      }
      const swatches = ['#f2a6d4', '#8ec5f0', '#f2c27b', '#8fe0a8', '#d9e8e1']
      for (let i = 0; i < swatches.length; i++) {
        ctx.fillStyle = swatches[(i + step) % swatches.length]
        ctx.fillRect(180, 28 + i * 24, 60, 18)
      }
    } else if (role === 'planner') {
      for (let i = 0; i < 6; i++) {
        ctx.fillStyle = i <= step % 7 ? accent : '#3b524b'
        ctx.fillRect(12, 28 + i * 20, 12, 12)
        ctx.fillStyle = '#d9e8e1'
        ctx.fillRect(32, 32 + i * 20, 70 + ((i * 29) % 100), 6)
      }
    } else drawStatic(ctx, role, accent)
    texture.needsUpdate = true
  }
  return {
    texture,
    draw,
    dispose() {
      texture.dispose()
    },
  }
}
// The sandbox: a terminal inside a container frame. The lines are decorative; the real result is in the report.
function drawSandbox(ctx, step) {
  ctx.strokeStyle = '#7dffa8'
  ctx.lineWidth = 2
  ctx.setLineDash([6, 4])
  ctx.strokeRect(6, 24, W - 12, H - 34)
  ctx.setLineDash([])
  ctx.fillStyle = '#7dffa8'
  ctx.font = 'bold 10px monospace'
  ctx.fillText('SANDBOX · tanpa jaringan', 12, 38)
  ctx.font = '11px monospace'
  ctx.fillStyle = '#cfe'
  ctx.fillText('$ test', 12, 56)
  const shown = Math.min(8, step % 12)
  for (let i = 0; i < shown; i++) {
    ctx.fillStyle = '#7dffa8'
    ctx.fillText('.', 14 + i * 10, 76)
  }
  ctx.fillStyle = '#d9e8e1'
  for (let i = 0; i < 3; i++) ctx.fillRect(12, 92 + i * 14, 70 + ((i * 47 + step * 13) % 120), 5)
  if (step % 2 === 0) {
    ctx.fillStyle = '#fff'
    ctx.fillRect(12, H - 24, 7, 10)
  }
}
// Amang stepping in: three rungs, the one for the current attempt lit.
function drawRecovery(ctx, step, iteration) {
  ctx.fillStyle = '#ffc65c'
  ctx.font = 'bold 11px sans-serif'
  ctx.fillText('PEMULIHAN · percobaan ' + (iteration || 2), 10, 38)
  const rungs = ['ulang + umpan balik', 'ganti pendekatan', 'susun ulang rencana']
  rungs.forEach((text, i) => {
    const lit = i === Math.min(2, Math.max(0, (iteration || 2) - 2))
    ctx.fillStyle = lit ? '#ffc65c' : '#3b524b'
    ctx.fillRect(12, 52 + i * 30, 16, 16)
    ctx.fillStyle = lit && step % 2 ? '#fff' : '#d9e8e1'
    ctx.font = '12px sans-serif'
    ctx.fillText(text, 38, 64 + i * 30)
  })
}
function drawStatic(ctx, role, accent) {
  ctx.fillStyle = accent
  for (let i = 0; i < 6; i++) ctx.fillRect(12, 30 + i * 18, 50 + ((i * 53 + role.length * 7) % 140), 6)
}

// The sandbox step is lit only by what the backend reported: running now, failed (the Lead stepped in), or passed
// (the run reached approval or was approved). Between a failure and the next attempt it is blank, never "done".
export function sandboxStatus(team) {
  if (team.phase === 'verifying') return 'working'
  if (team.phase === 'recovering') return 'failed'
  if (team.approval || team.outcome === 'completed') return 'done'
  return 'standby'
}

// Wall board summarising the real pipeline state (or the labelled simulation).
export function createPipelineBoard() {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 256
  const ctx = canvas.getContext('2d'),
    texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const steps = [
    ['planner', 'PLAN', t => t.activeAgent === 'planner'],
    ['analyst', 'ARSITEK', t => t.activeAgent === 'analyst'],
    ['designer', 'DESAIN', t => t.activeAgent === 'designer'],
    ['programmer', 'CODE', t => t.activeAgent === 'programmer'],
    ['qa', 'QA', t => t.activeAgent === 'qa' && t.phase !== 'verifying'],
    ['reviewer', 'REVIEW', t => t.activeAgent === 'reviewer'],
    ['sandbox', 'SANDBOX', t => t.phase === 'verifying'],
    ['amang', 'APPROVAL', t => t.approval],
  ]
  let last = ''
  function draw(team) {
    if (!ctx) return
    const key = JSON.stringify([
      team.mode,
      team.simulated,
      team.activeAgent,
      team.approval,
      team.outcome,
      team.phase,
      team.iteration,
      Object.values(team.agents).map(a => a.status),
      team.label,
    ])
    if (key === last) return
    last = key
    ctx.fillStyle = '#10201b'
    ctx.fillRect(0, 0, 1024, 256)
    ctx.fillStyle = '#e9f3ee'
    ctx.font = 'bold 34px sans-serif'
    ctx.fillText(team.simulated ? 'PIPELINE · SIMULASI' : 'PIPELINE SIAMANG', 32, 52)
    ctx.font = '22px sans-serif'
    ctx.fillStyle = team.simulated ? '#ffc65c' : '#9fb3ad'
    ctx.fillText((team.label || '').slice(0, 60), 32, 90)
    const recovering = team.phase === 'recovering'
    if (recovering || (team.iteration > 1 && team.mode === 'live')) {
      ctx.fillStyle = recovering ? '#a36b1f' : '#2c4a3f'
      ctx.fillRect(520, 24, 230, 40)
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 20px sans-serif'
      ctx.fillText(recovering ? 'PEMULIHAN' : 'PERCOBAAN KE-' + team.iteration, 536, 52)
    }
    steps.forEach(([id, label, isActive], i) => {
      const x = 32 + i * 120,
        s =
          id === 'sandbox'
            ? sandboxStatus(team)
            : id === 'qa' && team.phase === 'verifying'
              ? 'done'
              : team.agents[id]?.status
      const active = isActive(team)
      ctx.fillStyle = active
        ? '#2fbf71'
        : s === 'done'
          ? '#2c4a3f'
          : s === 'failed'
            ? '#7a2a28'
            : s === 'blocked'
              ? '#a36b1f'
              : '#1d332d'
      ctx.fillRect(x, 120, 110, 96)
      if (active) {
        ctx.strokeStyle = '#b6ffd3'
        ctx.lineWidth = 4
        ctx.strokeRect(x + 2, 122, 106, 92)
      }
      ctx.fillStyle = '#e9f3ee'
      ctx.font = 'bold 21px sans-serif'
      ctx.fillText(label, x + 8, 160, 94)
      ctx.font = '18px sans-serif'
      ctx.fillStyle = '#b8cbc4'
      ctx.fillText(
        active
          ? 'aktif'
          : s === 'done'
            ? '✓ selesai'
            : s === 'failed'
              ? '✗ gagal'
              : s === 'offline'
                ? 'offline'
                : s === 'blocked'
                  ? 'ditahan'
                  : '—',
        x + 10,
        196,
      )
      if (i < steps.length - 1) {
        ctx.fillStyle = '#4f6b62'
        ctx.fillRect(x + 110, 166, 10, 4)
      }
    })
    if (team.outcome) {
      ctx.fillStyle = { completed: '#2fbf71', failed: '#d9534f', cancelled: '#8a8f8c' }[team.outcome]
      ctx.fillRect(760, 24, 232, 52)
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 24px sans-serif'
      ctx.fillText(
        { completed: 'DISETUJUI', failed: 'GAGAL', cancelled: 'DIBATALKAN' }[team.outcome],
        780,
        60,
      )
    }
    texture.needsUpdate = true
  }
  return {
    texture,
    draw,
    dispose() {
      texture.dispose()
    },
  }
}
