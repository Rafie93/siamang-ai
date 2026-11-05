import * as THREE from 'three'
// Procedural low-poly character with a joint hierarchy and damped procedural animation.
// Forward is +z. Joint targets are recomputed every frame from the current action, then
// eased so actions blend (sit → stand → walk) without keyframe assets.
const LOOKS = {
  amang: {
    skin: 0xd9c3a0,
    fur: 0x2b2622,
    shirt: 0x2b2622,
    pants: 0x2b2622,
    hair: null,
    armScale: 1.22,
    ape: true,
  },
  planner: { skin: 0xe8b48f, shirt: 0xd08a3c, pants: 0x34404a, hair: 0x5a3825, bun: true },
  analyst: { skin: 0xc68e64, shirt: 0x3d7fb8, pants: 0x2d3236, hair: 0x1f1a17, hardhat: true },
  programmer: {
    skin: 0xf0c9a6,
    shirt: 0x7b5bc4,
    pants: 0x28303a,
    hair: 0x2f2a26,
    headphones: true,
    hood: true,
  },
  qa: { skin: 0x9c6b4a, shirt: 0x3f9a63, pants: 0x3a3f45, hair: 0x14110f, cap: true },
  reviewer: {
    skin: 0xe2b896,
    shirt: 0xf1ece0,
    pants: 0x2a2f35,
    hair: 0x8a8a8a,
    glasses: true,
    tie: 0xc4534f,
  },
}
const damp = (a, b, k) => a + (b - a) * k,
  J = (n, s) => n + (s < 0 ? 'L' : 'R')
export const ACTIONS = ['sit', 'stand', 'walk']
export const GESTURES = [
  'idle',
  'type',
  'think',
  'talk',
  'listen',
  'celebrate',
  'slump',
  'wave',
  'stretch',
  'drink',
  'present',
]

export function createCharacter(id, kit, { color = '#888' } = {}) {
  const look = LOOKS[id] || LOOKS.planner,
    { box, ball, capsule, group } = kit,
    arm = look.armScale || 1
  const root = group()
  root.name = 'agent:' + id
  const body = group([0, 0, 0], root)
  const hips = group([0, 0.9, 0], body)
  box([0.3, 0.16, 0.2], look.pants, [0, 0, 0], hips)
  const spine = group([0, 0.06, 0], hips)
  const torso = ball(0.2, look.shirt, [0, 0.24, 0], spine)
  torso.scale.set(0.42, 0.56, 0.3)
  if (look.ape) ball(0.12, 0xb8a07c, [0, 0.24, 0.1], spine).scale.set(0.2, 0.3, 0.08)
  if (look.tie) box([0.05, 0.26, 0.02], look.tie, [0, 0.26, 0.15], spine)
  if (look.hood) ball(0.13, look.shirt, [0, 0.48, -0.1], spine).scale.set(0.3, 0.16, 0.18)
  const neck = group([0, 0.5, 0], spine)
  const head = group([0, 0.06, 0], neck)
  const skull = ball(0.15, look.ape ? look.fur : look.skin, [0, 0.12, 0], head)
  skull.scale.set(0.3, 0.31, 0.29)
  const face = look.ape ? ball(0.1, look.skin, [0, 0.09, 0.08], head) : null
  face?.scale.set(0.2, 0.19, 0.14)
  for (const x of [-0.05, 0.05]) ball(0.02, 0x101814, [x, 0.14, 0.135], head, { shadow: false })
  const MOUTH_HEIGHT = 0.012,
    mouth = box([0.06, MOUTH_HEIGHT, 0.01], 0x5a2f2a, [0, 0.06, 0.145], head, { shadow: false })
  if (look.hair) {
    const h = ball(0.16, look.hair, [0, 0.17, -0.02], head)
    h.scale.set(0.32, 0.24, 0.31)
    if (look.bun) ball(0.07, look.hair, [0, 0.3, -0.08], head)
  }
  if (look.ape) for (const x of [-0.15, 0.15]) ball(0.04, look.fur, [x, 0.13, 0], head)
  if (look.hardhat) {
    const hat = ball(0.17, 0xf2c230, [0, 0.2, 0], head)
    hat.scale.set(0.34, 0.2, 0.34)
    box([0.38, 0.02, 0.4], 0xf2c230, [0, 0.2, 0.02], head)
  }
  if (look.cap) {
    const c = ball(0.16, 0x2e7d50, [0, 0.21, 0], head)
    c.scale.set(0.32, 0.16, 0.32)
    box([0.2, 0.02, 0.14], 0x2e7d50, [0, 0.2, 0.17], head)
  }
  if (look.headphones) {
    box([0.34, 0.03, 0.05], 0x222222, [0, 0.29, 0], head)
    for (const x of [-0.16, 0.16]) box([0.05, 0.09, 0.09], 0x222222, [x, 0.13, 0], head)
  }
  if (look.glasses) {
    box([0.2, 0.03, 0.01], 0x111111, [0, 0.15, 0.15], head, { shadow: false })
  }
  // Arms
  const arms = {}
  for (const side of [-1, 1]) {
    const s = group([side * 0.2, 0.42, 0], spine),
      upper = capsule(0.05, 0.2 * arm, look.ape ? look.fur : look.shirt, s)
    const elbow = group([0, -0.31 * arm, 0], s)
    capsule(0.045, 0.18 * arm, look.ape ? look.fur : look.skin, elbow)
    const hand = ball(0.05, look.skin, [0, -0.29 * arm, 0], elbow)
    arms[side] = { shoulder: s, elbow, hand, upper }
  }
  // Legs
  const legs = {}
  for (const side of [-1, 1]) {
    const h = group([side * 0.09, -0.02, 0], hips)
    capsule(0.07, 0.3, look.pants, h)
    const knee = group([0, -0.44, 0], h)
    capsule(0.06, 0.3, look.pants, knee)
    box([0.1, 0.06, 0.2], 0x1f2421, [0, -0.43, 0.05], knee)
    legs[side] = { hip: h, knee }
  }
  // Props
  const doc = box([0.2, 0.27, 0.01], 0xfaf6ea, [0, -0.36 * arm, 0.06], arms[1].elbow, { shadow: false })
  doc.rotation.x = -1.2
  doc.visible = false
  const mug = kit.cyl(0.04, 0.1, 0xffffff, [0, -0.33 * arm, 0.04], arms[-1].elbow)
  mug.visible = false
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.36, 0.46, 32),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.0, depthWrite: false }),
  )
  ring.rotation.x = -Math.PI / 2
  ring.position.y = 0.02
  root.add(ring)
  root.traverse(o => {
    if (o.isMesh) o.userData.agentId = id
  })

  const joints = { hipsY: 0.9, spineX: 0, spineY: 0, headX: 0, headY: 0, headZ: 0, bodyY: 0 }
  for (const s of [-1, 1])
    Object.assign(joints, {
      [J('sx', s)]: 0,
      [J('sz', s)]: 0,
      [J('ex', s)]: 0,
      [J('hx', s)]: 0,
      [J('kx', s)]: 0,
    })
  const state = {
    action: 'stand',
    gesture: 'idle',
    path: [],
    speed: 1.6,
    heading: 0,
    targetHeading: 0,
    walkPhase: 0,
    onArrive: null,
    seed: Math.random() * 10,
    glow: 0,
    highlight: false,
  }

  function targets(t) {
    const g = state.gesture,
      o = {
        hipsY: 0.9,
        spineX: 0,
        spineY: 0,
        headX: 0,
        headY: Math.sin(t * 0.4 + state.seed) * 0.12,
        headZ: 0,
        bodyY: 0,
      }
    for (const s of [-1, 1])
      Object.assign(o, {
        [J('sx', s)]: 0,
        [J('sz', s)]: s * 0.08,
        [J('ex', s)]: -0.15,
        [J('hx', s)]: 0,
        [J('kx', s)]: 0,
      })
    const breath = Math.sin(t * 1.6 + state.seed) * 0.015
    if (state.action === 'walk') {
      const p = state.walkPhase,
        sw = Math.sin(p)
      Object.assign(o, {
        hxR: sw * 0.55,
        hxL: -sw * 0.55,
        kxR: Math.max(0, -Math.sin(p + 0.6)) * 0.9 + 0.05,
        kxL: Math.max(0, Math.sin(p + 0.6)) * 0.9 + 0.05,
        sxR: -sw * 0.45,
        sxL: sw * 0.45,
        exR: -0.35,
        exL: -0.35,
        bodyY: Math.abs(Math.cos(p)) * 0.035,
        spineX: 0.06,
        headY: 0,
      })
      if (doc.visible) Object.assign(o, { sxR: -0.9, exR: -0.6 })
    } else if (state.action === 'sit') {
      Object.assign(o, { hipsY: 0.5, hxR: -1.5, hxL: -1.5, kxR: 1.45, kxL: 1.45, spineX: 0.04 + breath })
      if (g === 'type')
        Object.assign(o, {
          sxR: -0.95,
          sxL: -0.95,
          exR: -0.75 + Math.sin(t * 17) * 0.09,
          exL: -0.75 + Math.sin(t * 15 + 1) * 0.09,
          spineX: 0.14,
          headX: 0.18 + Math.sin(t * 3) * 0.03,
          headY: Math.sin(t * 0.7) * 0.08,
        })
      else if (g === 'think')
        Object.assign(o, {
          sxR: -0.5,
          exR: -2.3,
          headX: -0.12,
          headZ: 0.12,
          headY: 0.2,
          sxL: -0.6,
          exL: -0.9,
        })
      else if (g === 'slump')
        Object.assign(o, { spineX: 0.35, headX: 0.45, sxR: -0.3, sxL: -0.3, exR: -1, exL: -1 })
      else if (g === 'stretch')
        Object.assign(o, { sxR: -2.9, sxL: -2.9, exR: -0.2, exL: -0.2, spineX: -0.2, headX: -0.3 })
      else if (g === 'talk' || g === 'listen')
        Object.assign(o, {
          sxR: -0.7,
          sxL: -0.6,
          exR: -0.9 + (g === 'talk' ? Math.sin(t * 5) * 0.25 : 0),
          exL: -0.9,
          headX: Math.sin(t * 2.2) * 0.06,
          headY: state.lookY ?? 0,
        })
      else Object.assign(o, { sxR: -0.55, sxL: -0.55, exR: -0.9, exL: -0.9, spineX: -0.04 + breath })
    } else {
      o.spineX = breath
      if (g === 'talk' || g === 'present')
        Object.assign(o, {
          sxR: -0.9 + Math.sin(t * 4) * 0.25,
          szR: 0.25,
          exR: -1.1 + Math.sin(t * 5) * 0.3,
          headX: Math.sin(t * 2.6) * 0.07,
        })
      if (g === 'listen') Object.assign(o, { headX: Math.sin(t * 2) * 0.08 + 0.05, headZ: 0.06 })
      if (g === 'celebrate') {
        const j = Math.abs(Math.sin(t * 6))
        Object.assign(o, {
          sxR: -2.9,
          sxL: -2.9,
          szR: 0.3,
          szL: -0.3,
          exR: -0.3,
          exL: -0.3,
          bodyY: j * 0.18,
          kxR: j * 0.3,
          kxL: j * 0.3,
          hxR: -j * 0.2,
          hxL: -j * 0.2,
          headX: -0.2,
        })
      }
      if (g === 'slump') Object.assign(o, { spineX: 0.3, headX: 0.4, sxR: 0.1, sxL: 0.1 })
      if (g === 'wave') Object.assign(o, { sxR: -2.7, szR: 0.35, exR: -0.6 + Math.sin(t * 9) * 0.4 })
      if (g === 'stretch') Object.assign(o, { sxR: -2.9, sxL: -2.9, spineX: -0.15, headX: -0.3 })
      if (g === 'drink') Object.assign(o, { sxL: -1.3, exL: -1.9, headX: -0.1 })
      if (g === 'think') Object.assign(o, { sxR: -0.5, exR: -2.3, headX: -0.12, headZ: 0.12 })
    }
    if (look.ape) for (const s of [-1, 1]) o[J('sz', s)] += s * 0.06
    return o
  }

  function update(dt, t) {
    // Path following
    if (state.path.length) {
      const [tx, tz] = state.path[0],
        dx = tx - root.position.x,
        dz = tz - root.position.z,
        dist = Math.hypot(dx, dz)
      state.targetHeading = Math.atan2(dx, dz)
      const step = Math.min(dist, state.speed * dt)
      if (dist < 0.02) {
        state.path.shift()
        if (!state.path.length) {
          const done = state.onArrive
          state.onArrive = null
          state.action = 'stand'
          if (state.arriveHeading != null) state.targetHeading = state.arriveHeading
          done?.()
        }
      } else {
        root.position.x += (dx / dist) * step
        root.position.z += (dz / dist) * step
        state.walkPhase += step * 5.2
      }
    }
    let dh = state.targetHeading - state.heading
    dh = Math.atan2(Math.sin(dh), Math.cos(dh))
    state.heading += dh * Math.min(1, dt * 9)
    root.rotation.y = state.heading
    const k = 1 - Math.exp(-dt * (state.action === 'walk' ? 14 : 7)),
      tg = targets(t)
    for (const key in joints) joints[key] = damp(joints[key], tg[key], k)
    hips.position.y = joints.hipsY
    body.position.y = joints.bodyY
    spine.rotation.x = joints.spineX
    spine.rotation.y = joints.spineY
    head.rotation.set(joints.headX, joints.headY, joints.headZ)
    for (const s of [-1, 1]) {
      arms[s].shoulder.rotation.set(joints[J('sx', s)], 0, joints[J('sz', s)])
      arms[s].elbow.rotation.x = joints[J('ex', s)]
      legs[s].hip.rotation.x = joints[J('hx', s)]
      legs[s].knee.rotation.x = joints[J('kx', s)]
    }
    mouth.scale.y =
      MOUTH_HEIGHT *
      (state.gesture === 'talk' || state.gesture === 'present' ? 1 + Math.abs(Math.sin(t * 14)) * 3 : 1)
    state.glow = damp(state.glow, state.highlight ? 0.55 : 0, 1 - Math.exp(-dt * 5))
    ring.material.opacity = state.glow * (0.75 + Math.sin(t * 4) * 0.25)
    ring.visible = state.glow > 0.01
  }

  // Teleport-free helpers: every move is a walk along waypoints.
  return {
    id,
    root,
    state,
    get busy() {
      return state.path.length > 0 || state.action === 'walk'
    },
    place([x, z], heading = 0) {
      root.position.set(x, 0, z)
      state.heading = state.targetHeading = heading
      root.rotation.y = heading
    },
    walk(points, { face = null, then = null } = {}) {
      if (!points.length) {
        if (face != null) state.targetHeading = face
        then?.()
        return
      }
      state.path = points.map(p => [...p])
      state.arriveHeading = face
      state.onArrive = then
      state.action = 'walk'
      state.gesture = 'idle'
    },
    stop() {
      state.path = []
      state.onArrive = null
      if (state.action === 'walk') state.action = 'stand'
    },
    sit(face) {
      state.action = 'sit'
      if (face != null) state.targetHeading = face
    },
    stand() {
      if (state.action !== 'walk') state.action = 'stand'
    },
    gesture(name) {
      state.gesture = name
    },
    face(angle) {
      state.targetHeading = angle
    },
    lookAt([x, z]) {
      state.targetHeading = Math.atan2(x - root.position.x, z - root.position.z)
    },
    carry(on) {
      doc.visible = on
    },
    mug(on) {
      mug.visible = on
    },
    highlight(on) {
      state.highlight = on
    },
    headPosition(target) {
      return head.getWorldPosition(target).add(new THREE.Vector3(0, 0.42, 0))
    },
    update,
    dispose() {
      ring.geometry.dispose()
      ring.material.dispose()
    },
  }
}
