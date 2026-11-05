import { test, expect } from '@playwright/test'
import { nav } from './helpers.js'
test('real backend, navigation and task submission without execution', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await expect(page.locator('canvas')).toBeVisible()
  await nav(page, 'Command')
  await page.getByLabel('Instruksi', { exact: true }).fill('E2E audit: inspect calculator proposal only.')
  await page.getByRole('button', { name: 'Kirim task' }).click()
  await expect(page.getByRole('status')).toContainText('Task diterima')
  await expect(page.getByRole('button', { name: 'Jalankan proposal' })).toBeVisible()
  await nav(page, 'Settings')
  await expect(page.getByText('Browser → gateway lokal → SIAMANG → model')).toBeVisible()
  expect(errors).toEqual([])
})
test('WebGL unavailable preserves accessible command UI and mobile layout', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      return /webgl/.test(type) ? null : original.call(this, type, ...args)
    }
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByText('Office dalam mode 2D')).toBeVisible()
  await nav(page, 'Command')
  await expect(page.getByLabel('Instruksi', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
test('gateway rejects cross-origin writes and unauthenticated access', async ({ request }) => {
  expect((await request.get('/api/v0/projects')).status()).toBe(403)
  expect(
    (await request.post('/api/v0/tasks', { headers: { Origin: 'http://evil.test' }, data: {} })).status(),
  ).toBe(403)
})
test('offline state never shows active agent', async ({ page }) => {
  await page.route('**/api/v0/**', route => route.abort())
  await page.goto('/')
  await expect(page.getByText('Backend tidak tersedia', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('disconnected')
  await nav(page, 'Command')
  await expect(page.getByRole('button', { name: 'Kirim task' })).toBeDisabled()
})
test('isolated read-only fixture disables approval', async ({ page }) => {
  await page.route('**/api/v0/capabilities', async route => {
    const response = await route.fetch()
    const data = await response.json()
    data.permissions = ['read']
    await route.fulfill({ json: data })
  })
  await page.goto('/')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await nav(page, 'Command')
  await page.getByLabel('Instruksi', { exact: true }).fill('test-only fixture')
  await expect(page.getByRole('button', { name: 'Kirim task' })).toBeDisabled()
  await nav(page, 'Approvals')
  for (const b of await page.getByRole('button', { name: 'Approve', exact: true }).all())
    await expect(b).toBeDisabled()
})
test('isolated approval failure keeps dialog open without claiming success', async ({ page }) => {
  await page.route('**/api/v0/approvals?*', route =>
    route.fulfill({
      json: {
        items: [
          { run_id: 'req_fixture', task_id: 'tsk_fixture', eligible: true, wait_reason: 'approval_required' },
        ],
      },
    }),
  )
  await page.route('**/api/v0/approvals/req_fixture', route =>
    route.fulfill({ status: 409, json: { error: { code: 'evidence_invalid' } } }),
  )
  await page.goto('/')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await nav(page, 'Approvals')
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Konfirmasi', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('evidence_invalid')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('status')).toHaveCount(0)
})
test('office navigation repeatedly disposes renderer without duplicate canvases', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('canvas')).toHaveCount(1)
  for (let i = 0; i < 4; i++) {
    await nav(page, 'Tasks')
    await expect(page.locator('canvas')).toHaveCount(0)
    await nav(page, 'Office')
    await expect(page.locator('canvas')).toHaveCount(1)
  }
})
test('green theme, theme switch, deep links and no CSP violations', async ({ page }) => {
  const violations = []
  page.on('console', m => {
    if (/Content Security Policy/i.test(m.text())) violations.push(m.text())
  })
  page.on('pageerror', e => violations.push(e.message))
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/tasks')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await expect(page.locator('.sidebar .router-link-exact-active')).toContainText('Tasks')
  const token = name =>
    page.evaluate(
      n => getComputedStyle(document.documentElement).getPropertyValue(n).trim().toLowerCase(),
      name,
    )
  expect(await token('--color-primary')).toBe('#1b7f52')
  await page.getByRole('button', { name: 'Tema mengikuti sistem' }).click()
  await page.getByRole('button', { name: 'Tema terang' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await token('--color-primary')).toBe('#3dba84')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  expect(violations).toEqual([])
})
test('closing the confirm dialog returns focus to its opener', async ({ page }) => {
  await page.route('**/api/v0/approvals?*', route =>
    route.fulfill({
      json: {
        items: [
          { run_id: 'req_fixture', task_id: 'tsk_fixture', eligible: true, wait_reason: 'approval_required' },
        ],
      },
    }),
  )
  await page.goto('/approvals')
  const approve = page.getByRole('button', { name: 'Approve', exact: true })
  await approve.click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(async () => {
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0, { timeout: 500 })
  }).toPass()
  await expect(approve).toBeFocused()
})
test('projects list opens the new project dialog in place and refuses a bad repository', async ({ page }) => {
  await page.goto('/projects')
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Task baru' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Proyek baru' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText(
    'Tautkan repo GitHub. Amang membaca isinya, lalu bertanya tentang kebutuhan Anda sebelum tim bekerja.',
  )
  await expect(page).toHaveURL(/\/projects$/)
  await dialog.getByLabel('Repository GitHub').fill('bukan repo')
  await expect(dialog.getByRole('button', { name: 'Buat proyek' })).toBeDisabled()
  await dialog.getByLabel('Repository GitHub').fill('https://github.com/pemilik/nama-repo')
  await expect(dialog.getByLabel('Nama proyek')).toHaveValue('nama-repo')
  await expect(dialog.getByRole('button', { name: 'Buat proyek' })).toBeEnabled()
  await dialog.getByRole('button', { name: 'Batal' }).click()
  await expect(dialog).toHaveCount(0)
})
const REPO = { repository: 'a/b', default_branch: 'main', state: 'ready' },
  portfolio = spec => ({
    items: [
      {
        project_id: 'prj_fixture',
        name: 'Fixture',
        status: 'active',
        task_counts: {},
        waiting: {},
        features: [],
        repository: REPO,
        spec_state: spec,
      },
    ],
  }),
  QUESTIONS = [{ question: 'Warna apa?', options: ['Hijau', 'Biru'] }]
async function fixtureProject(page, spec, messages) {
  await page.route('**/api/v0/portfolio', route => route.fulfill({ json: portfolio(spec) }))
  await page.route('**/api/v0/projects', route =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { items: [{ project_id: 'prj_fixture', name: 'Fixture' }] } })
      : route.fallback(),
  )
  await page.route('**/api/v0/projects/prj_fixture/intake', route =>
    route.fulfill({
      json: { repository: REPO, spec: { state: spec, content: '# Judul\n\n- satu' }, messages },
    }),
  )
  await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v0/approvals?*', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v0/projects/prj_fixture/drafts', route => route.fulfill({ json: { items: [] } }))
}
test('a project card leads to its detail, and the Lead questions can be answered there', async ({ page }) => {
  await fixtureProject(page, 'gathering', [
    { position: 1, role: 'lead', message: 'Saya baca repo.', questions: QUESTIONS },
  ])
  let sent
  await page.route('**/api/v0/projects/prj_fixture/intake/messages', route => {
    sent = route.request().postDataJSON()
    route.fulfill({ status: 202, json: { project_id: 'prj_fixture', state: 'thinking' } })
  })
  await page.goto('/projects')
  await page.getByText('Amang menunggu jawaban Anda').click()
  await expect(page).toHaveURL(/\/projects\/prj_fixture$/)
  await page.getByRole('button', { name: 'Hijau' }).click()
  await page.getByRole('button', { name: 'Kirim ke Amang' }).click()
  await expect.poll(() => sent?.content).toContain('Jawaban: Hijau')
})
test('a specification waiting for approval is shown first and rendered', async ({ page }) => {
  await fixtureProject(page, 'pending_approval', [])
  await page.goto('/projects/prj_fixture')
  await expect(page.getByRole('button', { name: 'Setujui spesifikasi' })).toBeVisible()
  await expect(page.locator('.markdown li')).toHaveText('satu')
})
test('a vague command becomes a conversation, and only agreement creates the task', async ({ page }) => {
  await fixtureProject(page, 'approved', [])
  let draft = null,
    created = false
  const lead = (state, extra) => ({
    draft_id: 'dft_fixture',
    project_id: 'prj_fixture',
    state,
    title: '',
    brief: '',
    options: {},
    task_id: null,
    error: null,
    messages: [
      { position: 0, role: 'owner', message: 'tombol' },
      { position: 1, role: 'lead', message: 'Belum jelas.', questions: QUESTIONS },
    ],
    ...extra,
  })
  await page.route('**/api/v0/projects/prj_fixture/drafts', route =>
    route.request().method() === 'POST'
      ? ((draft = lead('need_answers')),
        route.fulfill({ status: 202, json: { draft_id: 'dft_fixture', state: 'thinking' } }))
      : route.fulfill({ json: { items: draft ? [draft] : [] } }),
  )
  await page.route('**/api/v0/projects/prj_fixture/drafts/dft_fixture/messages', route => {
    draft = lead('ready', { title: 'Tombol kontak', brief: '# Brief\n\nTambah tombol.' })
    route.fulfill({ status: 202, json: { draft_id: 'dft_fixture', state: 'thinking' } })
  })
  await page.route('**/api/v0/projects/prj_fixture/drafts/dft_fixture/confirm', route => {
    created = true
    draft = null
    route.fulfill({
      status: 202,
      json: {
        task: { task_id: 'tsk_fixture', status: 'queued', description: 'Tambah tombol.' },
        duplicate: false,
        scheduled: true,
        reason: null,
      },
    })
  })
  await page.goto('/command')
  await page.getByLabel('Instruksi', { exact: true }).fill('tombol')
  await page.getByRole('button', { name: 'Kirim ke Amang' }).click()
  await expect(page.getByText('Warna apa?')).toBeVisible()
  expect(created).toBe(false)
  await page.getByRole('button', { name: 'Biru' }).click()
  await page.getByRole('button', { name: 'Kirim ke Amang' }).click()
  const agree = page.getByRole('button', { name: 'Setuju, buat task dan mulai' })
  await expect(agree).toBeVisible()
  await expect(page.locator('.markdown')).toContainText('Tambah tombol.')
  expect(created).toBe(false)
  await agree.click()
  await expect(page).toHaveURL(/\/tasks$/)
  expect(created).toBe(true)
})
test('Detail brings the task into view, and a refused action stays on screen beside the button', async ({
  page,
}) => {
  const task = {
    task_id: 'tsk_stuck',
    status: 'waiting',
    title: 'Macet',
    description: 'Macet',
    execution: { wait_reason: 'input_required', phase: 'blocked', state: 'waiting' },
    branch: null,
  }
  await page.setViewportSize({ width: 1280, height: 500 })
  await fixtureProject(page, 'approved', [])
  await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [task] } }))
  await page.route('**/api/v0/tasks/tsk_stuck', route => route.fulfill({ json: task }))
  for (const [path, json] of [
    ['reports', { items: [], patch_applied: false }],
    ['execution-events', { items: [], next_after: 0 }],
    ['documents', { items: [] }],
  ])
    await page.route('**/api/v0/tasks/tsk_stuck/' + path + '*', route => route.fulfill({ json }))
  await page.route('**/api/v0/tasks/tsk_stuck/messages', route =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 409, json: { error: { code: 'clarification_not_pending' } } })
      : route.fulfill({ json: { items: [] } }),
  )
  await page.goto('/tasks')
  const detail = page.getByRole('button', { name: 'Detail', exact: false }).first()
  await detail.click()
  await expect(page.locator('.task-detail')).toBeInViewport()
  await page.getByRole('button', { name: 'Lanjutkan tanpa arahan' }).click()
  const refused = page.getByRole('alert').filter({ hasText: 'Belum terkirim' })
  await expect(refused).toBeInViewport()
  await page.waitForTimeout(3500)
  await expect(refused).toBeVisible()
})
test('an outdated backend is named instead of leaving buttons that seem dead', async ({ page }) => {
  await page.route('**/api/v0/health', route =>
    route.fulfill({ json: { status: 'healthy', contract_version: '0.3' } }),
  )
  await page.goto('/tasks')
  await expect(page.getByText('Backend masih versi lama')).toBeVisible()
})
test('a stopped task explains itself and offers a way forward', async ({ page }) => {
  const task = {
    task_id: 'tsk_stuck',
    status: 'waiting',
    title: 'Macet',
    description: 'Macet',
    execution: { wait_reason: 'quality_gate', phase: 'blocked', state: 'waiting' },
    branch: null,
  }
  await fixtureProject(page, 'approved', [])
  await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [task] } }))
  await page.route('**/api/v0/tasks/tsk_stuck', route => route.fulfill({ json: task }))
  for (const [path, json] of [
    ['reports', { items: [], patch_applied: false }],
    ['execution-events', { items: [], next_after: 0 }],
    ['documents', { items: [] }],
  ])
    await page.route('**/api/v0/tasks/tsk_stuck/' + path + '*', route => route.fulfill({ json }))
  let sent
  await page.route('**/api/v0/tasks/tsk_stuck/messages', route => {
    if (route.request().method() === 'POST') {
      sent = route.request().postDataJSON()
      return route.fulfill({ status: 202, json: { state: 'scheduled' } })
    }
    return route.fulfill({ json: { items: [] } })
  })
  await page.goto('/tasks')
  await expect(page.getByText('QA dan Reviewer menahan hasil ini')).toBeVisible()
  await page.getByRole('button', { name: 'Lanjutkan tanpa arahan' }).click()
  await expect.poll(() => sent?.content).toContain('Lanjutkan')
})
test('settings chooses an agent model and only sends what was offered', async ({ page }) => {
  const catalog = {
      agents: [
        { agent: 'lead', model_id: null, provider: null, provider_model: null },
        { agent: 'qa', model_id: 'claude-sonnet', provider: 'claude', provider_model: 'sonnet' },
      ],
      options: [
        {
          provider: 'claude',
          provider_type: 'claude_cli',
          provider_model: 'sonnet',
          model_id: 'claude-sonnet',
          available: true,
          cloud: true,
        },
        {
          provider: 'ollama',
          provider_type: 'ollama',
          provider_model: 'phi3:mini',
          model_id: null,
          available: true,
          cloud: false,
        },
        {
          provider: 'ollama',
          provider_type: 'ollama',
          provider_model: 'gone:1',
          model_id: 'ollama-gone',
          available: false,
          cloud: false,
        },
      ],
      unreachable: [],
    },
    sent = []
  await page.route('**/api/v0/settings/models', async route => {
    if (route.request().method() === 'POST') {
      sent.push(route.request().postDataJSON())
      await route.fulfill({ json: { agent: 'qa', model_id: 'ollama-phi3-mini' } })
    } else await route.fulfill({ json: catalog })
  })
  await page.goto('/settings')
  const qa = page.getByLabel('QA', { exact: true })
  await expect(qa).toHaveValue('claude/sonnet')
  await expect(qa.locator('option:checked')).toHaveText('sonnet · Claude CLI · cloud')
  await expect(page.getByLabel('Amang', { exact: true }).locator('option:checked')).toHaveText(
    'Belum ada model',
  )
  await expect(qa.locator('option', { hasText: 'gone:1' })).toBeDisabled()
  await qa.selectOption('ollama/phi3:mini')
  await expect(page.getByText('Tersimpan')).toBeVisible()
  expect(sent).toEqual([{ agent: 'qa', provider: 'ollama', provider_model: 'phi3:mini' }])
})
test('a task shows its packages, its build and its artifacts, and a failed build can become a revision', async ({
  page,
}) => {
  const task = {
    task_id: 'tsk_ci',
    status: 'completed',
    title: 'Presensi',
    description: 'Presensi',
    project_id: 'prj_fixture',
    execution: { state: 'completed', phase: 'finished' },
    branch: {
      branch: 'siamang/presensi',
      publication: 'published',
      pr_number: 9,
      pr_url: 'https://github.com/a/b/pull/9',
    },
    packages: [
      {
        key: 'foundation',
        kind: 'foundation',
        title: 'Fondasi',
        specialty: '',
        state: 'passed',
        owns: ['.github/workflows/'],
        files: 3,
        questions: [],
      },
      {
        key: 'pkg-1',
        kind: 'package',
        title: 'Backend',
        specialty: 'backend',
        state: 'passed',
        owns: ['backend/'],
        files: 8,
        questions: [],
      },
    ],
    ci: {
      state: 'failed',
      log: 'npm ERR! gagal',
      runs: [
        {
          id: 1,
          name: 'CI',
          status: 'completed',
          conclusion: 'failure',
          url: 'https://github.com/a/b/actions/runs/1',
          jobs: [
            { name: 'backend', status: 'completed', conclusion: 'failure' },
            { name: 'mobile', status: 'completed', conclusion: 'success' },
          ],
          artifacts: [
            { name: 'app-release.apk', size: 5242880, expired: false },
            { name: 'lama.zip', size: 1, expired: true },
          ],
        },
      ],
    },
  }
  await fixtureProject(page, 'approved', [])
  await page.route('**/api/v0/tasks?*', route => route.fulfill({ json: { items: [task] } }))
  await page.route('**/api/v0/tasks/tsk_ci', route => route.fulfill({ json: task }))
  for (const [path, json] of [
    ['reports', { items: [], patch_applied: false }],
    ['execution-events', { items: [], next_after: 0 }],
    ['documents', { items: [] }],
    ['messages', { items: [] }],
  ])
    await page.route('**/api/v0/tasks/tsk_ci/' + path + '*', route => route.fulfill({ json }))
  await page.goto('/tasks')
  await expect(page.getByRole('region', { name: 'Paket kerja' })).toContainText('Backend')
  await expect(page.getByRole('region', { name: 'CI' })).toContainText('Build atau test gagal')
  await expect(page.getByRole('link', { name: /app-release\.apk/ })).toBeVisible()
  await expect(page.getByText('5.0 MB')).toBeVisible()
  await expect(page.getByText('lama.zip')).toHaveCount(0)
  await expect(page.locator('.code-block')).toContainText('npm ERR!')
  await page.getByRole('button', { name: 'Buat task revisi dari kegagalan ini' }).click()
  await expect(page).toHaveURL(/\/command$/)
  await expect(page.getByLabel('Instruksi', { exact: true })).toHaveValue(/job: backend\./)
})
