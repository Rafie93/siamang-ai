import { test, expect } from '@playwright/test'
const FLOW = '# PRD\n\nPengantar.\n\n```mermaid\nflowchart TD\n  A[Mulai] --> B[Selesai]\n```\n\nPenutup.'
const HOSTILE =
  '```mermaid\nflowchart TD\n  A["<img src=x onerror=window.pwned=1>Awal"] --> B\n  click A href "javascript:window.pwned=1"\n```'
const BROKEN = '```mermaid\nini bukan diagram\n```'
const doc = (kind, content) => ({
  kind,
  status: 'generated',
  source: 'authored',
  content,
  reason: '',
  model_id: 'model-x',
})
const documents = [
  doc('prd', FLOW),
  doc('roadmap', HOSTILE),
  doc('wbs', BROKEN),
  {
    kind: 'task_list',
    status: 'not_applicable',
    source: 'authored',
    content: '',
    reason: 'Tidak relevan untuk task ini.',
    model_id: null,
  },
]
const task = {
  task_id: 'tsk_doc',
  title: 'Task Dokumen',
  description: 'Task Dokumen',
  status: 'completed',
  project_id: 'prj_doc',
  execution: {},
  branch: null,
}
async function fixture(page) {
  await page.route('**/api/v0/portfolio', route =>
    route.fulfill({
      json: {
        items: [
          {
            project_id: 'prj_doc',
            name: 'Fixture',
            status: 'active',
            repository: null,
            spec_state: null,
            task_counts: { completed: 1 },
            waiting: {},
            features: [],
            tasks: [task],
          },
        ],
      },
    }),
  )
  await page.route('**/api/v0/projects', route =>
    route.fulfill({ json: { items: [{ project_id: 'prj_doc', name: 'Fixture' }] } }),
  )
  await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [task] } }))
  await page.route('**/api/v0/approvals?*', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v0/projects/prj_doc/drafts', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v0/tasks/tsk_doc', route => route.fulfill({ json: task }))
  for (const [path, json] of [
    ['reports', { items: [], patch_applied: false }],
    ['execution-events', { items: [], next_after: 0 }],
    ['documents', { items: documents }],
    ['messages', { items: [] }],
  ])
    await page.route('**/api/v0/tasks/tsk_doc/' + path + '*', route => route.fulfill({ json }))
  await page.goto('/documents')
  await page.getByRole('tab', { name: /Planner/ }).click()
}
const panel = (page, title) => page.locator('.collapse-item').filter({ hasText: title })
const header = (page, title) => panel(page, title).locator('.collapse-head')

test('a document opens under its own row and closes again', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await fixture(page)
  const prd = panel(page, 'PRD'),
    body = prd.locator('.collapse-body')
  await expect(body).toBeHidden()
  await header(page, 'PRD').click()
  await expect(prd.locator('.mermaid-diagram svg')).toBeVisible({ timeout: 15000 })
  await expect(prd.locator('.mermaid-diagram')).toContainText('Mulai')
  await expect(body).toContainText('Pengantar.')
  const rowBottom =
      (await header(page, 'PRD').boundingBox()).y + (await header(page, 'PRD').boundingBox()).height,
    next = (await header(page, 'Roadmap').boundingBox()).y
  expect((await body.boundingBox()).y).toBeGreaterThanOrEqual(rowBottom - 1)
  expect(next).toBeGreaterThan((await body.boundingBox()).y + 50)
  await header(page, 'PRD').click()
  await expect(body).toBeHidden()
  await header(page, 'PRD').click()
  await expect(body).toBeVisible()
  await header(page, 'Task List').click()
  await expect(panel(page, 'Task List')).toContainText('Tidak relevan untuk task ini.')
  await expect(body).toBeVisible()
  await page.waitForTimeout(3500)
  await expect(body).toBeVisible()
  await prd.getByRole('button', { name: 'Tutup' }).click()
  await expect(body).toBeHidden()
  await expect(panel(page, 'Task List').locator('.collapse-body')).toBeVisible()
  expect(errors).toEqual([])
})
test('a diagram the model wrote cannot run script, and a broken one shows its source', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await fixture(page)
  await header(page, 'Roadmap').click()
  const svg = panel(page, 'Roadmap').locator('.mermaid-diagram svg')
  await expect(svg).toBeVisible({ timeout: 15000 })
  await expect(svg.locator('[onerror]')).toHaveCount(0)
  await expect(svg.locator('a[href^="javascript"], a[*|href^="javascript"]')).toHaveCount(0)
  expect(await page.evaluate(() => window.pwned)).toBeUndefined()
  await header(page, 'WBS').click()
  await expect(panel(page, 'WBS')).toContainText('tidak bisa digambar', { timeout: 15000 })
  await expect(panel(page, 'WBS').locator('pre')).toHaveText('ini bukan diagram')
  await expect(page.locator('body > [id^="dmermaid"]')).toHaveCount(0)
  expect(errors).toEqual([])
})
test('an open diagram does not widen the page on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fixture(page)
  await header(page, 'PRD').click()
  await expect(panel(page, 'PRD').locator('.mermaid-diagram svg')).toBeVisible({ timeout: 15000 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
