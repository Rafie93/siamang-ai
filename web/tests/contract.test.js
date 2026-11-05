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
