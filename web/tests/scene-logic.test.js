import { describe, it, expect } from 'vitest'
import {
  DESKS,
  SPOTS,
  OBSTACLES,
  route,
  seatSpot,
  visitSpot,
  segmentHitsBox,
} from '../src/three/navigation.js'
import { createDirector } from '../src/three/director.js'
import { createCharacter } from '../src/three/character.js'
import { createKit } from '../src/three/kit.js'
import { AGENTS, teamState } from '../src/domain/team.js'

const ids = Object.keys(DESKS)
const ownDesk = id =>
  DESKS[id] && OBSTACLES[['planner', 'analyst', 'programmer', 'qa', 'reviewer'].indexOf(id)]
function collisions(path, start, ignore = []) {
  const pts = [start, ...path],
    hits = []
  for (let i = 1; i < pts.length; i++)
    for (const box of OBSTACLES)
      if (!ignore.includes(box) && segmentHitsBox(pts[i - 1], pts[i], box, 0.12))
        hits.push([pts[i - 1], pts[i], box])
  return hits
}

describe('navigation', () => {
  it('every desk-to-desk visit avoids furniture', () => {
    for (const a of ids)
      for (const b of ids) {
        if (a === b) continue
        const r = route(seatSpot(a), visitSpot(b))
        expect(collisions(r, seatSpot(a).pos, [ownDesk(a)].filter(Boolean))).toEqual([])
        expect(r.at(-1)).toEqual(visitSpot(b).pos)
      }
  })
  it('ambient spots are reachable from every seat and back', () => {
    for (const a of ids)
      for (const spot of Object.values(SPOTS)) {
        expect(collisions(route(seatSpot(a), spot), seatSpot(a).pos, [ownDesk(a)].filter(Boolean))).toEqual(
          [],
        )
        expect(route(spot, seatSpot(a)).at(-1)).toEqual(seatSpot(a).pos)
      }
  })
  it('segment test detects overlaps', () => {
    expect(segmentHitsBox([0, -5], [0, 5], [-1, -1, 1, 1])).toBe(true)
    expect(segmentHitsBox([3, -5], [3, 5], [-1, -1, 1, 1])).toBe(false)
  })
})

function fakeCast() {
  const cast = {}
  for (const a of AGENTS) {
    const c = {
      id: a.id,
      pos: [0, 0],
      heading: 0,
      action: 'sit',
      gesture: 'idle',
      busy: false,
      carrying: false,
      highlighted: false,
      path: null,
      then: null,
      state: { speed: 1.6 },
      place(p, f) {
        this.pos = p
        this.heading = f
      },
      walk(points, { face, then } = {}) {
        this.path = points
        this.then = then
        this.busy = true
        this.action = 'walk'
      },
      arrive() {
        const t = this.then
        this.pos = this.path.at(-1)
        this.busy = false
        this.action = 'stand'
        this.path = null
        this.then = null
        t?.()
      },
      stop() {
        this.busy = false
        this.path = null
      },
      sit() {
        this.action = 'sit'
      },
      stand() {
        this.action = 'stand'
      },
      gesture(g) {
        this.gesture_ = g
      },
      face() {},
      lookAt() {},
      carry(v) {
        this.carrying = v
      },
      mug() {},
      highlight(v) {
        this.highlighted = v
      },
    }
    Object.defineProperty(c, 'g', {
      get() {
        return this.gesture_
      },
    })
    cast[a.id] = c
  }
  return cast
}
const ev = (position, phase) => ({
  position,
  run_id: 'r',
  task_id: 't',
  timestamp: new Date().toISOString(),
  state: 'running',
  phase,
})
const live = (phase, events) =>
  teamState({
    task: {
      task_id: 't',
      status: 'running',
      description: 'x',
      execution: { phase, activity_known: true, stale: false, heartbeat_at: new Date().toISOString() },
    },
    events,
  })
const settle = (cast, d, steps = 400) => {
  for (let i = 0; i < steps; i++) {
    d.update(0.1)
    for (const c of Object.values(cast)) if (c.busy && c.path) c.arrive()
  }
}

describe('director choreography', () => {
  it('only the backend-active agent types; handoff walks from sender to receiver and back', () => {
    const cast = fakeCast(),
      d = createDirector(cast, { random: () => 0.99 })
    d.setTeam(live('planning', [ev(1, 'queued'), ev(2, 'planning')]))
    d.update(0.1)
    expect(cast.amang.busy).toBe(true)
    expect(cast.planner.g).not.toBe('type')
    settle(cast, d)
    expect(cast.planner.g).toBe('type')
    expect(cast.programmer.g).toBe('idle')
    d.setTeam(live('analysis', [ev(1, 'queued'), ev(2, 'planning'), ev(3, 'analysis')]))
    d.update(0.1)
    expect(cast.planner.busy).toBe(true)
    expect(cast.planner.carrying).toBe(true)
    cast.planner.arrive()
    expect(d.bubbles.planner.text).toContain('Arsitek')
    settle(cast, d)
    expect(cast.planner.pos).toEqual(seatSpot('planner').pos)
    expect(cast.planner.carrying).toBe(false)
    expect(cast.analyst.g).toBe('type')
  })
  it('offline never animates work and cancels ambient walks', () => {
    const cast = fakeCast(),
      d = createDirector(cast, { random: () => 0 })
    d.setTeam(teamState({ connected: false }))
    settle(cast, d, 600)
    expect(Object.values(cast).some(c => c.g === 'type' || c.busy)).toBe(false)
  })
  it('approval sends Amang to the kiosk; reduced motion uses bubbles without walking', () => {
    const cast = fakeCast(),
      d = createDirector(cast, { random: () => 0.99 })
    d.setTeam(
      teamState({
        task: {
          task_id: 't',
          status: 'waiting',
          description: 'x',
          execution: { wait_reason: 'approval_required' },
        },
      }),
    )
    d.update(0.1)
    expect(cast.amang.busy).toBe(true)
    cast.amang.arrive()
    d.update(0.1)
    expect(d.bubbles.amang.tone).toBe('wait')
    const still = fakeCast(),
      r = createDirector(still, { random: () => 0.99 })
    r.setMotion(false)
    r.setTeam(live('analysis', [ev(1, 'queued'), ev(2, 'planning'), ev(3, 'analysis')]))
    r.update(0.1)
    r.setTeam(
      live('programming', [ev(1, 'queued'), ev(2, 'planning'), ev(3, 'analysis'), ev(4, 'programming')]),
    )
    r.update(0.1)
    expect(Object.values(still).some(c => c.busy)).toBe(false)
    expect(r.bubbles.analyst.text).toContain('Programmer')
  })
  it('switching task resets everyone to their seat and drops pending handoffs', () => {
    const cast = fakeCast(),
      d = createDirector(cast, { random: () => 0.99 })
    d.setTeam(live('analysis', [ev(1, 'queued'), ev(2, 'planning'), ev(3, 'analysis')]))
    d.update(0.1)
    expect(cast.planner.busy).toBe(true)
    d.setTeam(teamState({ task: { task_id: 'other', status: 'queued', description: 'x' } }))
    d.update(0.1)
    expect(cast.planner.busy).toBe(false)
    expect(cast.planner.pos).toEqual(seatSpot('planner').pos)
    expect(d.queue).toHaveLength(0)
  })
})

describe('procedural character', () => {
  it('walks a path, sits, and keeps every joint finite', () => {
    const kit = createKit(),
      c = createCharacter('programmer', kit)
    c.place([0, 0])
    let arrived = false
    c.walk(
      [
        [2, 0],
        [2, 2],
      ],
      { face: Math.PI, then: () => (arrived = true) },
    )
    for (let i = 0; i < 200 && !arrived; i++) c.update(1 / 30, i / 30)
    expect(arrived).toBe(true)
    expect(c.root.position.x).toBeCloseTo(2, 1)
    expect(c.root.position.z).toBeCloseTo(2, 1)
    for (const g of [
      'type',
      'think',
      'talk',
      'celebrate',
      'slump',
      'stretch',
      'drink',
      'present',
      'listen',
    ]) {
      c.gesture(g)
      c.sit()
      c.update(1 / 30, 1)
      c.stand()
      c.update(1 / 30, 2)
    }
    c.root.traverse(o => {
      for (const v of [o.position.x, o.position.y, o.position.z, o.rotation.x, o.rotation.y, o.rotation.z])
        expect(Number.isFinite(v)).toBe(true)
    })
    c.dispose()
    kit.dispose()
  })
  it('every agent model can be built and is pickable by id', () => {
    const kit = createKit()
    for (const a of AGENTS) {
      const c = createCharacter(a.id, kit, { color: a.color })
      let meshes = 0
      c.root.traverse(o => {
        if (o.isMesh) {
          meshes++
          expect(o.userData.agentId).toBe(a.id)
        }
      })
      expect(meshes).toBeGreaterThan(15)
      c.dispose()
    }
    kit.dispose()
  })
})

describe('ambient life never blocks real work', () => {
  it('standby agents stay at their desks while a run is active; idle agents may wander and are recalled', () => {
    const cast = fakeCast(),
      d = createDirector(cast, { random: () => 0 })
    d.setTeam(live('planning', [ev(1, 'queued'), ev(2, 'planning')]))
    settle(cast, d, 600)
    expect(cast.programmer.pos).toEqual(seatSpot('programmer').pos)
    const idle = fakeCast(),
      r = createDirector(idle, { random: () => 0 })
    r.setTeam(teamState({ task: { task_id: 't', status: 'queued', description: 'x' } }))
    let wanderer = null
    for (let i = 0; i < 400 && !wanderer; i++) {
      r.update(0.1)
      wanderer = Object.values(idle).find(c => c.busy)
    }
    expect(wanderer).toBeTruthy()
    wanderer.arrive()
    r.setTeam(live('planning', [ev(1, 'queued'), ev(2, 'planning')]))
    settle(idle, r, 600)
    for (const c of Object.values(idle)) expect(c.pos).toEqual(seatSpot(c.id).pos)
  })
})
