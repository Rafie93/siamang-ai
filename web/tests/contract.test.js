import { it, expect } from 'vitest'
import { validResponse } from '../src/services/contract.js'
it('does not accept empty or invented success responses', () => {
  expect(validResponse('/tasks', 'POST', {})).toBe(false)
  expect(validResponse('/approvals/req_x', 'POST', { success: true })).toBe(false)
  expect(validResponse('/tasks/tsk_x/reports', 'GET', { items: [], patch_applied: true })).toBe(false)
})
it('checks backend capability safety promises', () =>
  expect(
    validResponse('/capabilities', 'GET', {
      permissions: ['approve'],
      task_statuses: [],
      model_configured: true,
      proposal_only: false,
      patch_application: true,
    }),
  ).toBe(false))
it('checks the model settings shapes', () => {
  expect(validResponse('/settings/models', 'GET', { agents: [], options: [], unreachable: [] })).toBe(true)
  expect(validResponse('/settings/models', 'GET', { agents: [] })).toBe(false)
  expect(validResponse('/settings/models', 'POST', { agent: 'qa', model_id: 'x' })).toBe(true)
  expect(validResponse('/settings/models', 'POST', { agent: 'qa' })).toBe(false)
})
it('accepts a chat with one agent only in its own shape', () => {
  const thread = {
    agent: 'designer',
    items: [{ position: 1, role: 'owner', message: 'Hai' }],
    thinking: true,
    available: true,
  }
  expect(validResponse('/tasks/tsk_x/agents/designer/chat', 'GET', thread)).toBe(true)
  expect(validResponse('/projects/prj_x/agents/designer/chat', 'GET', { ...thread, thinking: 'yes' })).toBe(
    false,
  )
  expect(
    validResponse('/tasks/tsk_x/agents/designer/chat', 'GET', {
      ...thread,
      items: [{ position: 1, role: 'lead' }],
    }),
  ).toBe(false)
  expect(
    validResponse('/tasks/tsk_x/agents/designer/chat', 'POST', { agent: 'designer', state: 'thinking' }),
  ).toBe(true)
  expect(
    validResponse('/tasks/tsk_x/agents/designer/chat', 'POST', { agent: 'designer', state: 'scheduled' }),
  ).toBe(false)
})
it('checks the account shapes and never needs a token in them', () => {
  const github = { installed: true, connected: true, stored: true, source: 'siamang', login: 'a', name: null }
  expect(validResponse('/settings/github', 'GET', github)).toBe(true)
  expect(validResponse('/settings/github', 'POST', github)).toBe(true)
  expect(validResponse('/settings/github/disconnect', 'POST', { ...github, source: 'gh' })).toBe(true)
  expect(validResponse('/settings/github', 'GET', { ...github, source: 'token' })).toBe(false)
  expect(validResponse('/settings/github', 'GET', { connected: true })).toBe(false)
  const claude = { installed: true, logged_in: false, login: { state: 'waiting', url: null } }
  expect(validResponse('/settings/claude', 'GET', claude)).toBe(true)
  expect(validResponse('/settings/claude/login', 'POST', claude)).toBe(true)
  expect(validResponse('/settings/claude/code', 'POST', claude)).toBe(true)
  expect(validResponse('/settings/claude', 'GET', { ...claude, login: { state: 'x' } })).toBe(false)
  expect(validResponse('/settings/claude', 'GET', { installed: true, logged_in: true })).toBe(false)
})
