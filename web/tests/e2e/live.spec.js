import { test, expect } from '@playwright/test'
import { nav } from './helpers.js'

test('real backend: the stream opens, an idle page stays quiet, and a new task appears at once', async ({
  page,
}) => {
  const asked = []
  page.on('request', r => {
    if (r.url().includes('/api/v0/') && !r.url().endsWith('/api/v0/stream')) asked.push(r.url())
  })
  await page.goto('/')
  await expect(page.getByText('Live', { exact: true }).first()).toBeVisible()
  await page.waitForTimeout(500)
  asked.length = 0
  await page.waitForTimeout(6000)
  expect(asked).toEqual([]) // polling would have asked about 30 times by now
  await nav(page, 'Settings')
  await expect(page.getByText('pembaruan live (SSE)')).toBeVisible()
})

test('the page says it is polling when the stream is refused, and it does poll', async ({ page }) => {
  await page.route('**/api/v0/stream', route =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: '{"error":{"code":"stream_unavailable"}}',
    }),
  )
  const health = []
  page.on('request', r => {
    if (r.url().endsWith('/api/v0/health')) health.push(r.url())
  })
  await page.goto('/')
  await expect(page.getByText('Polling', { exact: true }).first()).toBeVisible()
  await expect.poll(() => health.length, { timeout: 8000 }).toBeGreaterThan(1)
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
})

test('a browser without EventSource still works, by polling', async ({ page }) => {
  await page.addInitScript(() => delete window.EventSource)
  await page.goto('/')
  await expect(page.getByText('Polling', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Backend tersambung', { exact: true })).toBeVisible()
})
