import { it, expect } from 'vitest'
import { permitted, trusted, equal, sessionCookie, SESSION_COOKIE } from '../scripts/gateway.mjs'
it('restricts authority and transport', () => {
  expect(permitted('POST', '/v0/tasks')).toBe(true)
  expect(permitted('POST', '/v0/projects')).toBe(true)
  expect(permitted('POST', '/v0/projects/prj_a')).toBe(false)
  expect(permitted('POST', '/v0/shell')).toBe(false)
  expect(permitted('GET', '/v0/tasks/tsk_abc/reports')).toBe(true)
  expect(permitted('DELETE', '/v0/tasks')).toBe(false)
})
it('rejects cross origin and rebinding hosts', () => {
  expect(
    trusted({ headers: { host: '127.0.0.1:5173', origin: 'https://evil.test' } }, '127.0.0.1:5173'),
  ).toBe(false)
  expect(trusted({ headers: { host: 'evil.test' } }, '127.0.0.1:5173')).toBe(false)
  expect(equal(undefined, 'abc')).toBe(false)
})
it('allows the task conversation and nothing beside it', () => {
  expect(permitted('POST', '/v0/projects/prj_a/drafts')).toBe(true)
  expect(permitted('POST', '/v0/projects/prj_a/drafts/dft_b/confirm')).toBe(true)
  expect(permitted('GET', '/v0/projects/prj_a/drafts/dft_b')).toBe(true)
  expect(permitted('POST', '/v0/projects/prj_a/drafts/dft_b/delete')).toBe(false)
  expect(permitted('POST', '/v0/projects/prj_a/drafts/../x')).toBe(false)
})
it('allows choosing models on exactly one settings route', () => {
  expect(permitted('GET', '/v0/settings/models')).toBe(true)
  expect(permitted('POST', '/v0/settings/models')).toBe(true)
  expect(permitted('POST', '/v0/settings')).toBe(false)
  expect(permitted('POST', '/v0/settings/providers')).toBe(false)
  expect(permitted('POST', '/v0/settings/models/x')).toBe(false)
})
it('allows dividing the work again, the action behind the Bagi ulang button', () => {
  expect(permitted('POST', '/v0/tasks/tsk_abc/work-division')).toBe(true)
  expect(permitted('GET', '/v0/tasks/tsk_abc/work-division')).toBe(false)
})
it('reads the whole session value from the cookie header, and nothing else', () => {
  expect(sessionCookie(`${SESSION_COOKIE}=abc`)).toBe('abc')
  expect(sessionCookie(`other=1; ${SESSION_COOKIE}=abc; last=2`)).toBe('abc')
  expect(sessionCookie(`not_${SESSION_COOKIE}=abc`)).toBeUndefined()
  expect(sessionCookie(undefined)).toBeUndefined()
})
