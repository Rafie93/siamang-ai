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
}
const row = (page, title) => page.locator('.doc-row').filter({ hasText: title }).first()
const reader = page => page.locator('.doc-reader')
async function read(page, title) {
  await row(page, title).click()
  await expect(page.locator('.doc-reader-title')).toContainText(title)
  return reader(page)
}

test('a document is read beside the list, which keeps every other one in reach', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await fixture(page)
  const prd = await read(page, 'PRD')
  await expect(row(page, 'PRD')).toHaveAttribute('aria-current', 'true')
  await expect(prd.locator('.mermaid-diagram svg')).toBeVisible({ timeout: 15000 })
  await expect(prd.locator('.mermaid-diagram')).toContainText('Mulai')
  await expect(prd).toContainText('Pengantar.')
  await expect(row(page, 'PRD')).toContainText('1 diagram')
  const [list, body] = await Promise.all([page.locator('.doc-nav').boundingBox(), prd.boundingBox()])
  expect(list.x + list.width).toBeLessThanOrEqual(body.x + 1)

  const taskList = await read(page, 'Task List')
  await expect(taskList).toContainText('Tidak relevan untuk task ini.')
  await expect(row(page, 'PRD')).not.toHaveAttribute('aria-current', 'true')
  expect(errors).toEqual([])
})
test('search finds a document by its text and says where', async ({ page }) => {
  await fixture(page)
  await page.getByLabel('Cari dokumen').fill('selesai')
  await expect(page.locator('.doc-row')).toHaveCount(1)
  await expect(page.locator('.doc-snippet')).toContainText('Selesai')
  await page.getByLabel('Cari dokumen').fill('tidak ada di mana pun')
  await expect(page.locator('.doc-nav')).toContainText('Tidak ada yang cocok.')
  await page.getByLabel('Cari dokumen').fill('')
  await page.getByRole('button', { name: /^Diagram/ }).click()
  await expect(page.locator('.doc-row')).toHaveCount(3)
})
test('a diagram the model wrote cannot run script, and a broken one shows its source', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await fixture(page)
  const svg = (await read(page, 'Roadmap')).locator('.mermaid-diagram svg')
  await expect(svg).toBeVisible({ timeout: 15000 })
  await expect(svg.locator('[onerror]')).toHaveCount(0)
  await expect(svg.locator('a[href^="javascript"], a[*|href^="javascript"]')).toHaveCount(0)
  expect(await page.evaluate(() => window.pwned)).toBeUndefined()
  const wbs = await read(page, 'WBS')
  await expect(wbs).toContainText('tidak bisa digambar', { timeout: 15000 })
  await expect(wbs.locator('pre')).toHaveText('ini bukan diagram')
  await expect(page.locator('body > [id^="dmermaid"]')).toHaveCount(0)
  expect(errors).toEqual([])
})
test('an open diagram does not widen the page on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fixture(page)
  await expect((await read(page, 'PRD')).locator('.mermaid-diagram svg')).toBeVisible({ timeout: 15000 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test.describe('the diagram editor', () => {
  const SOURCE = 'classDiagram\n  class Lama'
  const content = '# Class\n\nPengantar.\n\n```mermaid\n' + SOURCE + '\n```\n\nPenutup.'
  const gate = {
    ...task,
    status: 'waiting',
    execution: { state: 'waiting', phase: 'design_approval', wait_reason: 'design_approval' },
  }
  const designDoc = (over = {}) => ({
    kind: 'class_diagram',
    stage: 'analysis',
    status: 'generated',
    source: 'authored',
    content,
    content_sha256: 'a'.repeat(64),
    reason: '',
    model_id: 'model-x',
    edited_by: null,
    ...over,
  })
  async function atTheGate(
    page,
    saved = { status: 200, json: { content_sha256: 'b'.repeat(64), changed: true } },
  ) {
    const sent = []
    let current = designDoc()
    await fixture(page)
    await page.route('**/api/v0/tasks/tsk_doc', route => route.fulfill({ json: gate }))
    await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [gate] } }))
    await page.route('**/api/v0/tasks/tsk_doc/documents', route =>
      route.fulfill({ json: { items: [current] } }),
    )
    await page.route('**/api/v0/tasks/tsk_doc/documents/class_diagram/diagrams/0', route => {
      sent.push(route.request().postDataJSON())
      if (saved.status === 200)
        current = designDoc({
          content: content.replace(SOURCE, sent.at(-1).source),
          content_sha256: 'b'.repeat(64),
          edited_by: 'owner',
        })
      return route.fulfill(saved)
    })
    await page.reload()
    await (await read(page, 'Class Diagram')).getByRole('button', { name: 'Edit diagram' }).click()
    return { sent, dialog: page.locator('dialog.diagram-editor') }
  }

  test('preview follows the code, a mistake blocks saving, and saving sends the base it opened on', async ({
    page,
  }) => {
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    const { sent, dialog } = await atTheGate(page)
    const code = dialog.getByLabel('Kode Mermaid'),
      save = dialog.getByRole('button', { name: 'Simpan' }),
      picture = dialog.locator('.diagram-editor-preview .mermaid-diagram')
    await expect(code).toHaveValue(SOURCE)
    await expect(picture).toContainText('Lama', { timeout: 15000 })
    await expect(save).toBeDisabled()

    const [left, right] = await Promise.all([
      code.boundingBox(),
      dialog.locator('.diagram-editor-preview').boundingBox(),
    ])
    expect(left.x + left.width).toBeLessThan(right.x)

    await code.fill('classDiagram\n  class Baru')
    await expect(picture).toContainText('Baru')
    await expect(save).toBeEnabled()
    await page.screenshot({ path: test.info().outputPath('editor.png') })

    await code.fill('classDiagram\n  class {{{')
    await expect(dialog.getByRole('alert')).toBeVisible()
    await expect(save).toBeDisabled()
    await expect(picture).toContainText('Baru') // the last good picture stays while the code is wrong

    await code.fill('classDiagram\n  class Baru')
    await expect(save).toBeEnabled()
    await save.click()
    await expect(dialog).toBeHidden()
    expect(sent).toEqual([{ source: 'classDiagram\n  class Baru', base_sha256: 'a'.repeat(64) }])
    await expect(reader(page)).toContainText('Diedit owner')
    await expect(reader(page).locator('.mermaid-diagram')).toContainText('Baru')
    expect(errors).toEqual([])
  })

  test('a refused save keeps the text, and closing with unsaved text asks first', async ({ page }) => {
    const { dialog } = await atTheGate(page, {
      status: 409,
      json: { error: { code: 'document_changed' } },
    })
    const code = dialog.getByLabel('Kode Mermaid')
    await code.fill('classDiagram\n  class Baru')
    await expect(dialog.getByRole('button', { name: 'Simpan' })).toBeEnabled({ timeout: 15000 })
    await dialog.getByRole('button', { name: 'Simpan' }).click()
    await expect(dialog).toContainText('sudah berubah sejak editor dibuka')
    await expect(code).toHaveValue('classDiagram\n  class Baru')
    await dialog.getByRole('button', { name: 'Batal' }).click()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Buang perubahan' }).click()
    await expect(dialog).toBeHidden()
  })

  test('approving asks whether to send the changed diagrams to the Programmer', async ({ page }) => {
    await fixture(page)
    const approved = []
    await page.route('**/api/v0/tasks/tsk_doc', route => route.fulfill({ json: gate }))
    await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [gate] } }))
    await page.route('**/api/v0/tasks/tsk_doc/document-edits', route =>
      route.fulfill({
        json: {
          items: [
            {
              kind: 'class_diagram',
              title: 'Class Diagram',
              diagram: 0,
              added: 2,
              removed: 1,
              edited_by: 'owner',
            },
            { kind: 'erd', title: 'ERD', diagram: 0, added: 1, removed: 0, edited_by: 'owner' },
          ],
          clipped: ['erd'],
          divided: true,
        },
      }),
    )
    await page.route('**/api/v0/tasks/tsk_doc/design-decision', route => {
      approved.push(route.request().postDataJSON())
      return route.fulfill({ json: { run_id: 'run_1', decision: 'approve', task_id: 'tsk_doc' } })
    })
    await page.goto('/tasks')
    await page.getByRole('button', { name: 'Setujui desain' }).click()
    const dialog = page.getByRole('dialog', { name: 'Ada 2 bagian desain berubah. Kirim ke Programmer?' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('Class Diagram')
    await expect(dialog).toContainText('+2 / −1')
    await expect(dialog).toContainText('bisa terpotong: ERD')
    await expect(dialog.getByRole('button', { name: 'Bagi ulang dulu' })).toBeVisible()
    await dialog.getByRole('button', { name: 'Kirim ke Programmer' }).click()
    await expect(dialog).toBeHidden()
    expect(approved).toEqual([{ decision: 'approve' }])
  })

  test('a shipped task keeps its document, shows the proposal, and asks to make a revision', async ({
    page,
  }) => {
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    const shipped = { ...task, branch: { publication: 'published', branch: 'siamang/x' } }
    let proposal = null
    const sent = []
    await fixture(page)
    await page.route('**/api/v0/tasks/tsk_doc', route => route.fulfill({ json: shipped }))
    await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [shipped] } }))
    await page.route('**/api/v0/tasks/tsk_doc/documents', route =>
      route.fulfill({ json: { items: [designDoc()] } }),
    )
    await page.route('**/api/v0/tasks/tsk_doc/document-edits', route =>
      route.fulfill({
        json: {
          mode: 'revision',
          items: proposal ? [proposal] : [],
          pending: !!proposal,
          clipped: [],
          divided: false,
        },
      }),
    )
    await page.route('**/api/v0/tasks/tsk_doc/documents/class_diagram/diagrams/0', route => {
      proposal = {
        kind: 'class_diagram',
        title: 'Class Diagram',
        diagram: 0,
        source: route.request().postDataJSON().source,
        added: 1,
        removed: 1,
        edited_by: 'owner',
      }
      return route.fulfill({ json: { content_sha256: 'a'.repeat(64), changed: true, mode: 'revision' } })
    })
    await page.route('**/api/v0/tasks/tsk_doc/design-edits/send', route => {
      sent.push(route.request().postDataJSON())
      return route.fulfill({
        status: 202,
        json: {
          mode: 'revision',
          scheduled: true,
          task: { task_id: 'tsk_rev', status: 'queued', description: 'Revisi diagram' },
        },
      })
    })
    await page.reload()
    await (await read(page, 'Class Diagram')).getByRole('button', { name: 'Edit diagram' }).click()
    const editor = page.locator('dialog.diagram-editor')
    await expect(editor).toContainText('sudah terbit')
    await editor.getByLabel('Kode Mermaid').fill('classDiagram\n  class Baru')
    await expect(editor.getByRole('button', { name: 'Simpan' })).toBeEnabled({ timeout: 15000 })
    await editor.getByRole('button', { name: 'Simpan' }).click()
    await expect(editor).toBeHidden()
    await expect(reader(page)).toContainText('1 usulan revisi')
    await expect(reader(page).locator('.mermaid-diagram')).toContainText('Baru')
    await page.getByRole('button', { name: 'Buat task revisi' }).click()
    const question = page.getByRole('dialog', { name: 'Ada 1 bagian desain berubah. Buat task revisi?' })
    await expect(question).toContainText('Kode dan dokumen task ini tidak berubah')
    await question.getByLabel('Catatan untuk tim (opsional)').fill('Jaga nama lama.')
    await question.getByRole('button', { name: 'Buat task revisi' }).click()
    await expect(question).toBeHidden()
    expect(sent).toEqual([{ note: 'Jaga nama lama.' }])
    await expect(page).toHaveURL(/\/tasks/)
    expect(errors).toEqual([])
  })

  test('a stopped run offers the edit and asks before coding again', async ({ page }) => {
    const stopped = {
      ...task,
      status: 'waiting',
      execution: { state: 'waiting', phase: 'blocked', wait_reason: 'quality_gate' },
    }
    const sent = []
    await fixture(page)
    await page.route('**/api/v0/tasks/tsk_doc', route => route.fulfill({ json: stopped }))
    await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [stopped] } }))
    await page.route('**/api/v0/tasks/tsk_doc/document-edits', route =>
      route.fulfill({
        json: {
          mode: 'stopped',
          items: [{ kind: 'erd', title: 'ERD', diagram: 0, added: 1, removed: 0, edited_by: 'owner' }],
          pending: true,
          clipped: [],
          divided: true,
        },
      }),
    )
    await page.route('**/api/v0/tasks/tsk_doc/design-edits/send', route => {
      sent.push(route.request().postDataJSON())
      return route.fulfill({ status: 202, json: { mode: 'stopped', state: 'scheduled', task_id: 'tsk_doc' } })
    })
    await page.goto('/tasks')
    await page.getByRole('button', { name: 'Kirim ke Programmer' }).click()
    const question = page.getByRole('dialog', { name: 'Ada 1 bagian desain berubah. Kirim ke Programmer?' })
    await expect(question).toContainText('termasuk yang sudah lulus')
    await expect(question.getByRole('button', { name: 'Bagi ulang dulu' })).toHaveCount(0)
    await question.getByRole('button', { name: 'Kirim ke Programmer' }).click()
    await expect(question).toBeHidden()
    expect(sent).toEqual([{}])
  })
})

test('the sidebar shrinks to its icons, says where each goes, and remembers it', async ({ page }) => {
  await fixture(page)
  const sidebar = page.locator('aside.sidebar')
  expect((await sidebar.boundingBox()).width).toBeGreaterThan(200)
  await page.getByRole('button', { name: 'Ciutkan sidebar' }).click()
  await expect.poll(async () => (await sidebar.boundingBox()).width).toBeLessThan(80)
  await expect(sidebar.getByRole('link', { name: 'Artifacts' })).toHaveAttribute('title', 'Artifacts')
  await expect(sidebar.locator('.nav-label').first()).toBeHidden()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Lebarkan sidebar' })).toBeVisible()
  await page.getByRole('button', { name: 'Lebarkan sidebar' }).click()
  await expect.poll(async () => (await sidebar.boundingBox()).width).toBeGreaterThan(200)
})
