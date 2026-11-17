import { describe, expect, it } from 'vitest'
import { ALL_SCOPES, knownEvents, mergeEvents, scopesFor, withHeartbeat } from '../src/domain/live.js'

const view = { selectedId: 'tsk_a', stageId: 'tsk_b', projectId: 'prj_a' }
const scopes = (type, data, at = view) => [...scopesFor(type, data, at)].sort()
const changed = (parts, task_id = 'tsk_a', project_id = 'prj_a') => [
  'changed',
  { parts, task_id, project_id },
]

describe('scopesFor', () => {
  it('reloads what a lifecycle or stage change touches, and no more', () => {
    for (const type of ['lifecycle', 'execution']) {
      expect(scopes(type, { project_id: 'prj_a', task_id: 'tsk_a' })).toEqual(['board', 'task', 'tasks'])
      expect(scopes(type, { project_id: 'prj_a', task_id: 'tsk_b' })).toEqual(['board', 'stage', 'tasks'])
      expect(scopes(type, { project_id: 'prj_z', task_id: 'tsk_z' })).toEqual(['board'])
    }
  })
  it('never reloads for a heartbeat, a ping, or an event it does not know', () => {
    for (const type of ['heartbeat', 'ping', 'something_new'])
      expect(scopes(type, { project_id: 'prj_a', task_id: 'tsk_a', parts: ['task'] })).toEqual([])
  })
  it('maps each changed part to the route that shows it', () => {
    expect(scopes(...changed(['documents']))).toEqual(['documents'])
    expect(scopes(...changed(['documents'], 'tsk_b'))).toEqual(['stage'])
    expect(scopes(...changed(['messages']))).toEqual(['messages'])
    expect(scopes(...changed(['messages'], null))).toEqual(['intake'])
    expect(scopes(...changed(['reports']))).toEqual(['reports', 'tasks'])
    expect(scopes(...changed(['task']))).toEqual(['board', 'task', 'tasks'])
    expect(scopes(...changed(['drafts'], null))).toEqual(['drafts'])
    expect(scopes(...changed(['project'], null))).toEqual(['board', 'intake'])
    expect(scopes(...changed(['autonomy']))).toEqual([]) // no page shows it yet
    expect(scopes(...changed(['usage']))).toEqual(['projectUsage', 'tasks', 'usage'])
    expect(scopes(...changed(['usage'], null))).toEqual(['projectUsage', 'tasks']) // the intake or a draft
    expect(scopes(...changed(['usage'], 'tsk_z', 'prj_z'))).toEqual([])
    expect(scopes(...changed(['a_part_from_a_newer_backend']))).toEqual([])
  })
  it('does not load another project into this page', () => {
    expect(scopes(...changed(['drafts'], null, 'prj_z'))).toEqual([])
    expect(scopes(...changed(['messages'], null, 'prj_z'))).toEqual([])
    expect(scopes(...changed(['messages'], 'tsk_z', 'prj_z'))).toEqual([])
    expect(scopes(...changed(['reports'], 'tsk_z', 'prj_z'))).toEqual([])
    expect(scopes(...changed(['project'], null, 'prj_z'))).toEqual(['board'])
  })
  it('only names scopes a full refresh also loads', () => {
    const everything = ['documents', 'messages', 'reports', 'task', 'drafts', 'project', 'usage']
    for (const scope of scopesFor(
      'changed',
      { project_id: 'prj_a', task_id: 'tsk_a', parts: everything },
      view,
    ))
      expect(ALL_SCOPES).toContain(scope)
  })
})

describe('events', () => {
  const event = position => ({ position, task_id: 'tsk_a' })
  it('keeps order and drops repeats, whichever way they arrive', () => {
    expect(mergeEvents([event(1), event(3)], [event(2), event(3), event(4)]).map(e => e.position)).toEqual([
      1, 2, 3, 4,
    ])
  })
  it('does not count another task as history', () => {
    expect(knownEvents([event(9)], 'tsk_b')).toEqual([])
    expect(knownEvents([event(9)], 'tsk_a')).toHaveLength(1)
    expect(knownEvents(undefined, 'tsk_a')).toEqual([])
  })
})

describe('withHeartbeat', () => {
  const task = (over = {}) => ({
    task_id: 'tsk_a',
    status: 'running',
    execution: {
      run_id: 'run_1',
      state: 'running',
      heartbeat_at: 'old',
      activity_known: false,
      stale: true,
      ...over,
    },
  })
  const beat = { task_id: 'tsk_a', run_id: 'run_1', heartbeat_at: 'new' }
  it('makes a stale display fresh again, because the worker really is alive', () => {
    expect(withHeartbeat(task(), beat).execution).toMatchObject({
      heartbeat_at: 'new',
      activity_known: true,
      stale: false,
    })
  })
  it('leaves every other case alone', () => {
    const other = task({ run_id: 'run_2' })
    expect(withHeartbeat(other, beat)).toBe(other)
    const finished = task({ state: 'waiting' })
    expect(withHeartbeat(finished, beat)).toBe(finished)
    expect(withHeartbeat(null, beat)).toBe(null)
    const noRun = { task_id: 'tsk_a', status: 'queued' }
    expect(withHeartbeat(noRun, beat)).toBe(noRun)
  })
})
