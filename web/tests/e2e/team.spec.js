import { test, expect } from '@playwright/test'
import { nav } from './helpers.js'
const agents = ['Amang', 'Planner', 'Arsitek', 'Programmer', 'QA', 'Reviewer']
const roster = page => page.locator('.team-list button')

test('office shows six agents in 3D and in an accessible roster', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await expect(page.locator('canvas')).toHaveCount(1)
  await expect(roster(page)).toHaveCount(6)
  for (const name of agents)
    await expect(page.locator('.team-list').getByText(name, { exact: true })).toBeVisible()
  await expect(page.locator('.agent-tag')).toHaveCount(6)
  await expect(page.locator('.pill', { hasText: 'LIVE · BACKEND' })).toBeVisible()
  await roster(page).filter({ hasText: 'Programmer' }).click()
  await expect(page.locator('.agent-tag.selected')).toHaveCount(1)
  expect(errors).toEqual([])
})

test('demo mode is labelled as simulation and plays handoffs between agents', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/')
  await page.getByRole('button', { name: '▶ Mode demo' }).click()
  await expect(page.locator('.sim-banner')).toContainText('SIMULASI')
  await expect(page.locator('.sim-note')).toBeVisible()
  await expect(roster(page).filter({ hasText: 'Planner' })).toHaveAttribute('data-status', 'working', {
    timeout: 10000,
  })
  await expect(page.locator('.team-log')).toContainText('Amang → Planner')
  await expect(page.locator('.team-log small').first()).toContainText('simulasi')
  await expect(page.locator('.agent-bubble:not([hidden])').first()).toBeVisible({ timeout: 12000 })
  await expect(roster(page).filter({ hasText: 'Arsitek' })).toHaveAttribute('data-status', 'working', {
    timeout: 15000,
  })
  await page.getByRole('button', { name: '■ Keluar demo' }).click()
  await expect(page.locator('.sim-banner')).toHaveCount(0)
  await nav(page, 'Tasks')
  await expect(page.getByText('Simulasi: tambah validasi')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('reduced motion still renders the team without errors', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  await expect(page.locator('canvas')).toHaveCount(1)
  await page.getByRole('button', { name: '▶ Mode demo' }).click()
  await expect(roster(page).filter({ hasText: 'Planner' })).toHaveAttribute('data-status', 'working', {
    timeout: 10000,
  })
  await nav(page, 'Settings')
  await expect(page.getByLabel('Aktifkan animasi tahap kerja')).not.toBeChecked()
  expect(errors).toEqual([])
})

test('mobile office keeps roster usable without horizontal scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(roster(page)).toHaveCount(6)
  await expect(page.locator('canvas')).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

// Real SIAMANG + Ollama pipeline. Opt-in because it performs live inference (~20–90 s).
test('live pipeline: agents hand work over from real execution events', async ({ page }) => {
  test.skip(!process.env.WEB_LIVE, 'set WEB_LIVE=1 to run against live Ollama')
  test.setTimeout(240000)
  const statuses = new Set()
  page.on('response', async r => {
    if (/\/api\/v0\/tasks\/[^/?]+$/.test(new URL(r.url()).pathname))
      try {
        const t = await r.json()
        statuses.add(t.execution?.phase)
      } catch {}
  })
  await page.goto('/')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await nav(page, 'Command')
  await page
    .getByLabel('Instruksi', { exact: true })
    .fill('Tambahkan fungsi subtract(a, b) di backend/calculator.py beserta tesnya. Proposal saja.')
  await page.getByRole('button', { name: 'Kirim task' }).click()
  await expect(page.getByRole('status')).toContainText('Task diterima')
  await nav(page, 'Office')
  await page.getByRole('button', { name: 'Jalankan task' }).click()
  await page.getByRole('button', { name: 'Konfirmasi', exact: true }).click()
  const working = []
  const watch = setInterval(async () => {
    try {
      for (const b of await roster(page).all())
        if ((await b.getAttribute('data-status')) === 'working')
          working.push((await b.locator('strong').textContent()).trim())
    } catch {}
  }, 700)
  await expect(page.locator('.team-log'))
    .toContainText('Reviewer → Amang', { timeout: 200000 })
    .finally(() => clearInterval(watch))
  await expect(roster(page).filter({ hasText: 'Amang' })).toHaveAttribute('data-status', 'waiting', {
    timeout: 15000,
  })
  await expect(page.locator('.team-log')).not.toContainText('simulasi')
  console.log(
    'observed working agents:',
    [...new Set(working)].join(', '),
    '| phases seen in polls:',
    [...statuses].join(', '),
  )
  await page.locator('.scene-wrap').screenshot({ path: 'test-results/office-live-approval.png' })
})

test('a roster click opens a live popup, a character click speaks above its head', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await page.waitForTimeout(800)
  await roster(page).filter({ hasText: 'Programmer' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Programmer')
  await expect(dialog).toContainText('Hasil kerja')
  await dialog.getByRole('button', { name: 'Tutup' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('.agent-detail-bubble')).toBeHidden()
  await page.getByRole('button', { name: 'Reset kamera' }).click()
  await page.waitForTimeout(400)
  const box = await page.locator('.agent-tag', { hasText: 'Reviewer' }).boundingBox()
  await page.mouse.click(box.x + box.width / 2, box.y + box.height + 22)
  const bubble = page.locator('.agent-detail-bubble')
  await expect(bubble).toBeVisible()
  await expect(bubble).toContainText('Reviewer')
  await expect(dialog).toHaveCount(0)
  const shape = await bubble.boundingBox(),
    scene = await page.locator('.scene-wrap').boundingBox()
  expect(shape.y).toBeGreaterThanOrEqual(scene.y)
  expect(shape.x).toBeGreaterThanOrEqual(scene.x)
  expect(shape.x + shape.width).toBeLessThanOrEqual(scene.x + scene.width + 1)
  const free = await page.locator('.scene-wrap').boundingBox()
  await page.mouse.click(free.x + free.width * 0.5, free.y + free.height - 90)
  await expect(bubble).toBeHidden()
  expect(errors).toEqual([])
})
test('the dashboard holds every project and can put a task of any of them on stage', async ({ page }) => {
  const task = (id, title, wait) => ({
    task_id: id,
    title,
    description: title,
    status: 'waiting',
    project_id: id === 'tsk_b' ? 'prj_b' : 'prj_a',
    execution: { wait_reason: wait, phase: 'x', state: 'waiting' },
    branch: null,
  })
  const items = [
    {
      project_id: 'prj_a',
      name: 'Proyek Satu',
      status: 'active',
      repository: { repository: 'a/a', state: 'ready' },
      spec_state: 'approved',
      task_counts: { waiting: 1 },
      waiting: { design_approval: 1 },
      tasks: [task('tsk_a', 'Task Satu', 'design_approval')],
    },
    {
      project_id: 'prj_b',
      name: 'Proyek Dua',
      status: 'active',
      repository: { repository: 'a/b', state: 'ready' },
      spec_state: 'approved',
      task_counts: { waiting: 1 },
      waiting: { approval_required: 1 },
      tasks: [task('tsk_b', 'Task Dua', 'approval_required')],
    },
  ]
  await page.route('**/api/v0/office', route => route.fulfill({ json: { items, current: null } }))
  for (const id of ['tsk_a', 'tsk_b']) {
    await page.route('**/api/v0/tasks/' + id, route =>
      route.fulfill({
        json: task(
          id,
          id === 'tsk_a' ? 'Task Satu' : 'Task Dua',
          id === 'tsk_a' ? 'design_approval' : 'approval_required',
        ),
      }),
    )
    await page.route('**/api/v0/tasks/' + id + '/execution-events*', route =>
      route.fulfill({ json: { items: [], next_after: 0 } }),
    )
    await page.route('**/api/v0/tasks/' + id + '/documents', route => route.fulfill({ json: { items: [] } }))
  }
  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Proyek Satu' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Proyek Dua' })).toBeVisible()
  await expect(page.getByLabel('Project')).toHaveCount(0)
  await page
    .getByRole('button', { name: /Task Dua/ })
    .first()
    .click()
  await expect(page.locator('.scene-caption')).toContainText('Proyek Dua · Task Dua')
  await expect(roster(page).filter({ hasText: 'Amang' })).toHaveAttribute('data-status', 'waiting')
  await roster(page).filter({ hasText: 'Amang' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Proyek yang dipegang')
  await expect(dialog).toContainText('Task Satu: Setujui desain')
  await expect(dialog).toContainText('Task Dua: Setujui hasil akhir')
})
