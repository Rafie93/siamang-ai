import { describe, it, expect } from 'vitest'
import { visualStatus } from '../src/domain/status.js'
describe('truthful activity', () => {
  it('never infers programming from running', () =>
    expect(visualStatus({ status: 'running' }).pose).toBe('neutral'))
  it('requires fresh observed activity', () => {
    const task = {
      status: 'running',
      execution: {
        phase: 'programming',
        activity_known: true,
        stale: false,
        heartbeat_at: new Date().toISOString(),
      },
    }
    expect(visualStatus(task).pose).toBe('programming')
    expect(visualStatus(task, false).pose).toBe('neutral')
    task.execution.stale = true
    expect(visualStatus(task).pose).toBe('neutral')
  })
  it('does not infer approval or success', () => {
    expect(visualStatus({ status: 'waiting' }).label).not.toContain('persetujuan')
    expect(visualStatus({ status: 'future' }).label).toContain('belum')
    expect(visualStatus({ status: 'completed' }).label).toContain('belum diterapkan')
  })
})
it('expires cached heartbeat locally even before a poll completes', () =>
  expect(
    visualStatus({
      status: 'running',
      execution: {
        phase: 'programming',
        activity_known: true,
        heartbeat_at: new Date(Date.now() - 20000).toISOString(),
      },
    }).pose,
  ).toBe('neutral'))
