import { route, seatSpot, visitSpot, SPOTS } from './navigation.js'
// Choreographs characters from a teamState snapshot (see domain/team.js).
// Working/handoff scenes only come from backend-derived state; ambient life (coffee,
// stretching) only happens for agents that are standby, and is never labelled as work.
const AMBIENT = ['coffee', 'whiteboard', 'window', 'stretch', 'stretch']
export function createDirector(cast, { random = Math.random } = {}) {
  const actors = Object.fromEntries(
    Object.entries(cast).map(([id, c]) => [
      id,
      { c, home: true, script: null, nextAmbient: 8 + random() * 20 },
    ]),
  )
  const timers = [],
    bubbles = {},
    seen = new Set(),
    queue = []
  let team = null,
    taskKey,
    motion = true,
    clock = 0,
    outcome = null,
    celebrateUntil = 0,
    ambientWalker = null
  for (const [id, a] of Object.entries(actors)) {
    const s = seatSpot(id)
    a.c.place(s.pos, s.face)
    a.c.sit(s.face)
  }

  const after = (sec, fn) => timers.push({ at: clock + sec, fn })
  const say = (id, text, sec = 3, tone = 'talk') => {
    bubbles[id] = { text, until: clock + sec, tone }
  }
  const seatOf = id => seatSpot(id)
  function goHome(id, then) {
    const a = actors[id],
      from = a.spot || seatOf(id),
      home = seatOf(id)
    a.c.walk(route(from, home), {
      face: home.face,
      then: () => {
        a.home = true
        a.spot = null
        a.c.sit(home.face)
        a.c.carry(false)
        a.c.mug(false)
        then?.()
      },
    })
  }
  function goTo(id, spot, then) {
    const a = actors[id],
      from = a.spot || seatOf(id)
    a.home = false
    a.c.stand()
    a.c.walk(route(from, spot), {
      face: spot.face,
      then: () => {
        a.spot = spot
        then?.()
      },
    })
  }
  const free = id => !actors[id].script && !actors[id].c.busy

  function playHandoff(h) {
    const from = actors[h.from],
      to = actors[h.to]
    if (!from || !to) return
    if (!motion) {
      say(h.from, h.say, 5)
      after(1.5, () => say(h.to, h.reply, 4))
      return
    }
    from.script = to.script = 'handoff'
    const spot = visitSpot(h.to),
      fast = queue.length > 0
    from.c.state.speed = fast ? 2.4 : 1.7
    from.c.carry(true)
    from.c.gesture('idle')
    goTo(h.from, spot, () => {
      from.c.lookAt(seatOf(h.to).pos)
      from.c.gesture('present')
      say(h.from, h.say, 2.6)
      to.c.lookAt(spot.pos)
      to.c.gesture('listen')
      after(2.2, () => {
        from.c.carry(false)
        from.c.gesture('listen')
        to.c.gesture('talk')
        say(h.to, h.reply, 1.9)
        after(1.6, () => {
          to.c.face(seatOf(h.to).face)
          to.script = null
          from.c.state.speed = 1.6
          goHome(h.from, () => {
            from.script = null
          })
        })
      })
    })
  }

  function ambient(id) {
    const a = actors[id],
      kind = AMBIENT[Math.floor(random() * AMBIENT.length)]
    a.nextAmbient = clock + 22 + random() * 30
    if (kind === 'stretch') {
      a.script = 'ambient'
      a.c.gesture('stretch')
      after(2.6, () => {
        a.c.gesture('idle')
        a.script = null
      })
      return
    }
    if (ambientWalker) return
    ambientWalker = id
    a.script = 'ambient'
    goTo(id, SPOTS[kind], () => {
      a.c.gesture(kind === 'coffee' ? 'drink' : 'think')
      if (kind === 'coffee') a.c.mug(true)
      a.dwellUntil = clock + 4 + random() * 2
    })
  }
  // Ambient trips end early when the agent is needed by a real handoff or starts working.
  function needed(id) {
    return queue.some(h => h.from === id || h.to === id) || team?.agents[id]?.status === 'working'
  }
  function finishAmbient(id) {
    const a = actors[id]
    if (a.script !== 'ambient' || !a.spot || a.c.busy) return
    if (clock < a.dwellUntil && !needed(id)) return
    a.dwellUntil = 0
    a.c.gesture('idle')
    a.c.mug(false)
    goHome(id, () => {
      a.script = null
      if (ambientWalker === id) ambientWalker = null
    })
  }

  function baseBehaviour(id) {
    const a = actors[id],
      s = team?.agents[id]?.status || 'standby'
    finishAmbient(id)
    if (id === 'amang') {
      if (team?.approval) {
        if (!motion) {
          if (!a.c.busy) say(id, 'Proposal siap · menunggu keputusan Anda', 1.2, 'wait')
          return
        }
        if (a.home && free(id) && !needed(id) && !queue.some(h => h.from === id || h.to === id)) {
          a.script = 'approval'
          goTo(id, SPOTS.approval, () => {
            a.c.gesture('present')
            a.script = 'kiosk'
          })
        }
        if (a.script === 'kiosk') say(id, 'Proposal siap · menunggu keputusan Anda', 1.2, 'wait')
        return
      }
      if (a.script === 'kiosk') {
        a.script = 'returning'
        a.c.gesture('idle')
        goHome(id, () => {
          a.script = null
        })
      }
    }
    if (a.script || a.c.busy || !a.home) return
    if (clock < celebrateUntil) {
      a.c.stand()
      a.c.gesture('celebrate')
      return
    }
    a.c.sit(seatOf(id).face)
    a.c.gesture(
      s === 'working'
        ? 'type'
        : s === 'blocked' || s === 'offline'
          ? 'slump'
          : s === 'waiting'
            ? 'think'
            : 'idle',
    )
    a.c.highlight(s === 'working' || s === 'blocked' || (id === 'amang' && s === 'waiting'))
    const idleTeam = !team?.activeAgent && !team?.approval
    if (
      motion &&
      (s === 'done' || (s === 'standby' && idleTeam)) &&
      !queue.length &&
      team?.mode !== 'offline' &&
      clock > a.nextAmbient
    )
      ambient(id)
  }

  // New task context: put everyone back at their desk immediately.
  function resetStage() {
    timers.length = 0
    queue.length = 0
    ambientWalker = null
    celebrateUntil = 0
    for (const k in bubbles) delete bubbles[k]
    for (const [id, a] of Object.entries(actors)) {
      const s = seatOf(id)
      a.c.stop()
      a.c.carry(false)
      a.c.mug(false)
      a.c.place(s.pos, s.face)
      a.c.sit(s.face)
      a.c.gesture('idle')
      a.home = true
      a.spot = null
      a.script = null
    }
  }
  function setTeam(next) {
    if (next.taskKey !== taskKey) {
      if (taskKey !== undefined) resetStage()
      taskKey = next.taskKey
      queue.length = 0
      seen.clear()
      outcome = next.outcome
      const list = next.transitions,
        live = !!(next.activeAgent || next.approval || next.mode === 'unverified')
      list.slice(0, live ? -1 : undefined).forEach(t => seen.add(t.key))
    }
    if (next.mode !== 'offline')
      for (const t of next.transitions)
        if (!seen.has(t.key)) {
          seen.add(t.key)
          queue.push(t)
        }
    // Real pipelines can outpace walking; skip stale handoffs (they stay in the text log).
    while (queue.length > 1) queue.shift()
    if (next.outcome && next.outcome !== outcome) {
      if (next.outcome === 'completed') {
        celebrateUntil = clock + (motion ? 4.5 : 0)
        say('amang', 'Proposal disetujui · patch belum diterapkan', 5)
      }
      if (next.outcome === 'failed') say('amang', 'Task gagal · periksa laporan', 6, 'alert')
    }
    outcome = next.outcome
    team = next
  }

  function update(dt) {
    clock += dt
    for (let i = timers.length - 1; i >= 0; i--)
      if (timers[i].at <= clock) {
        const t = timers.splice(i, 1)[0]
        t.fn()
      }
    if (queue.length) {
      const h = queue[0]
      if (free(h.from) && free(h.to) && actors[h.from].home && actors[h.to].home) {
        queue.shift()
        playHandoff(h)
      }
    }
    for (const id in actors) baseBehaviour(id)
    for (const id in bubbles) if (bubbles[id].until < clock) delete bubbles[id]
  }
  return {
    setTeam,
    update,
    bubbles,
    actors,
    queue,
    setMotion(v) {
      motion = v
    },
    get clock() {
      return clock
    },
  }
}
