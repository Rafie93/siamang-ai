import * as THREE from 'three'
import { DESKS } from './navigation.js'
import { createScreen, createPipelineBoard } from './screens.js'
import { AGENTS } from '../domain/team.js'

// Builds the open-plan office. Returns handles for things that react to team state.
export function buildOffice(scene, kit) {
  const { box, ball, cyl, group, mat } = kit
  const root = group([0, 0, 0], scene)
  // Floor: warm wood + carpet zone for the team pods
  box([20, 0.2, 11.6], 0xcdbb98, [0, -0.1, -1.2], root, { shadow: false })
  for (let x = -9.5; x < 10; x += 1)
    box([0.02, 0.005, 11.6], 0xb8a47f, [x, 0.003, -1.2], root, { shadow: false })
  box([10.6, 0.02, 8], 0x8fa596, [-1.2, 0.012, -1], root, { shadow: false, rough: 0.95 })
  box([3.4, 0.02, 3], 0xd9cdb0, [-8, 0.014, 0.4], root, { shadow: false, rough: 0.95 })
  // Walls with window strip on the back wall and a city skyline beyond
  const wall = 0xf3eee2
  box([20, 0.5, 0.2], wall, [0, 0.25, -6.9], root)
  box([20, 0.6, 0.2], wall, [0, 3.9, -6.9], root)
  for (let x = -10; x <= 10; x += 2.5) box([0.22, 3.2, 0.24], 0xffffff, [x, 2.1, -6.88], root)
  box([20, 3.2, 0.04], 0xbfe1ea, [0, 2.1, -6.95], root, {
    transparent: true,
    opacity: 0.35,
    rough: 0.1,
    shadow: false,
  })
  box([0.2, 4.2, 11.6], 0xe3e8dc, [-10.1, 2.1, -1.2], root)
  for (const [x, h, w] of [
    [-8, 3.2, 1.6],
    [-5.5, 4.6, 1.2],
    [-3, 2.6, 2],
    [0, 5.4, 1.4],
    [2.4, 3.4, 1.8],
    [5, 4.8, 1.3],
    [7.6, 2.9, 2],
  ]) {
    box([w, h, 1], 0xa9bfc8, [x, h / 2, -11], root, { shadow: false })
    for (let y = 0.6; y < h - 0.3; y += 0.6)
      box([w * 0.7, 0.12, 0.02], 0xf8f0c8, [x, y, -10.48], root, {
        shadow: false,
        emissive: 0xf8e7a0,
        intensity: 0.3,
      })
  }
  box([40, 0.1, 20], 0xb7cfa9, [0, -0.05, -16], root, { shadow: false })
  // Wall decor: clock + framed posters
  cyl(0.32, 0.05, 0xffffff, [-10, 3, -3.6], root).rotation.z = Math.PI / 2
  box([0.04, 0.9, 1.3], 0x3d7fb8, [-9.98, 2.2, -0.2], root)
  box([0.04, 0.9, 1.3], 0xd08a3c, [-9.98, 2.2, 2.4], root)

  // Workstations
  const screens = {},
    desks = {}
  for (const agent of AGENTS) {
    const d = DESKS[agent.id],
      g = group([d.desk[0], 0, d.desk[1]], root, d.face),
      lead = agent.id === 'amang'
    desks[agent.id] = g
    const top = lead ? 0x6d4c3d : 0xd8b48a
    box([lead ? 1.8 : 1.6, 0.06, 0.8], top, [0, 0.74, 0], g)
    for (const x of [-0.72, 0.72]) box([0.06, 0.72, 0.66], 0x2a3631, [x * (lead ? 1.1 : 1), 0.36, 0], g)
    // Monitor(s) facing the seated agent (agents sit at local z=-0.85 facing +z)
    const screen = createScreen(agent.id)
    screens[agent.id] = screen
    const mon = group([0, 1.12, 0.18], g)
    box([0.78, 0.48, 0.04], 0x1d2421, [0, 0, 0], mon)
    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(0.72, 0.42),
      new THREE.MeshBasicMaterial({ map: screen.texture }),
    )
    panel.rotation.y = Math.PI
    panel.position.z = -0.025
    mon.add(panel)
    box([0.06, 0.26, 0.06], 0x1d2421, [0, -0.33, 0.02], mon)
    box([0.26, 0.02, 0.18], 0x1d2421, [0, -0.37, 0.02], mon)
    if (agent.id === 'programmer' || lead) {
      const side = group([0.62, 1.1, 0.05], g, -0.45)
      box([0.5, 0.34, 0.04], 0x1d2421, [0, 0, 0], side)
      box([0.44, 0.28, 0.01], 0x24413a, [0, 0, -0.025], side, { emissive: 0x24413a, intensity: 0.6 })
    }
    box([0.46, 0.02, 0.15], 0x3a4541, [0, 0.78, -0.2], g)
    box([0.07, 0.02, 0.1], 0x3a4541, [0.34, 0.78, -0.2], g)
    cyl(0.04, 0.1, new THREE.Color(agent.color).getHex(), [-0.55, 0.82, -0.1], g)
    if (agent.id === 'planner') {
      box([0.22, 0.01, 0.3], 0xfaf3dd, [0.5, 0.775, -0.05], g)
      for (let i = 0; i < 3; i++)
        box([0.05, 0.012, 0.05], [0xffd166, 0xef476f, 0x06d6a0][i], [-0.4 + i * 0.08, 0.78, 0.15], g)
    }
    if (agent.id === 'analyst') cyl(0.04, 0.6, 0x8ec5f0, [0.5, 0.8, -0.1], g).rotation.z = Math.PI / 2
    if (agent.id === 'qa') box([0.12, 0.18, 0.12], 0x3f9a63, [0.55, 0.86, 0.1], g)
    if (agent.id === 'reviewer')
      for (let i = 0; i < 3; i++) box([0.24, 0.03, 0.32], 0xf1ece0, [0.5, 0.78 + i * 0.03, 0], g)
    // Chair
    const chair = group([0, 0, -0.85], g)
    box([0.5, 0.08, 0.48], 0x2f3c37, [0, 0.46, 0], chair)
    box([0.5, 0.55, 0.07], 0x2f3c37, [0, 0.8, -0.27], chair)
    cyl(0.04, 0.4, 0x1c2421, [0, 0.24, 0], chair)
    cyl(0.26, 0.04, 0x1c2421, [0, 0.03, 0], chair)
    // Name plate
    box([0.36, 0.08, 0.02], new THREE.Color(agent.color).getHex(), [0, 0.8, 0.38], g)
  }

  // Lead area: approval kiosk next to Amang
  const kiosk = group([-9, 0, -3], root, Math.PI / 4)
  box([0.5, 1.1, 0.35], 0x2a3631, [0, 0.55, 0], kiosk)
  box([0.8, 0.55, 0.06], 0x1d2421, [0, 1.4, 0.05], kiosk)
  const approvalLight = box([0.7, 0.45, 0.02], 0x384a43, [0, 1.4, 0.09], kiosk, {
    material: new THREE.MeshStandardMaterial({ color: 0x384a43, emissive: 0x000000 }),
  })
  box([1.2, 0.04, 1.2], 0x2a3631, [0, 3.6, 0], kiosk, { shadow: false })

  // Pipeline wall board on the back wall
  const board = createPipelineBoard()
  const boardMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(4.2, 1.05),
    new THREE.MeshBasicMaterial({ map: board.texture }),
  )
  boardMesh.position.set(-1.6, 3.05, -6.74)
  root.add(boardMesh)
  box([4.4, 1.2, 0.06], 0x1d2421, [-1.6, 3.05, -6.79], root, { shadow: false })
  // Whiteboard with sticky notes
  box([2.2, 1.2, 0.05], 0xffffff, [2.9, 1.7, -6.74], root, { rough: 0.3 })
  box([2.3, 0.06, 0.12], 0xb0b5b2, [2.9, 1.08, -6.7], root)
  for (let i = 0; i < 9; i++)
    box(
      [0.22, 0.22, 0.01],
      [0xffd166, 0x8ec5f0, 0xf2a19d, 0x8fe0a8][i % 4],
      [2.1 + (i % 4) * 0.5, 2.05 - Math.floor(i / 4) * 0.35, -6.71],
      root,
      { shadow: false },
    )
  // Meeting table
  box([2, 0.06, 1.5], 0xe8dcc6, [5.4, 0.74, -2.35], root)
  cyl(0.08, 0.72, 0x2a3631, [5.4, 0.36, -2.35], root)
  cyl(0.45, 0.03, 0x2a3631, [5.4, 0.02, -2.35], root)
  for (const [x, z] of [
    [4.6, -3.35],
    [6.2, -3.35],
    [4.6, -1.35],
    [6.2, -1.35],
  ]) {
    box([0.44, 0.07, 0.44], 0x5c7a6c, [x, 0.46, z], root)
    box([0.44, 0.42, 0.06], 0x5c7a6c, [x, 0.72, z + (z < -2 ? -0.2 : 0.2)], root)
  }
  // Pantry
  box([2.1, 0.92, 0.62], 0xf1ece0, [7.55, 0.46, -6.1], root)
  box([2.1, 0.05, 0.66], 0x6d4c3d, [7.55, 0.94, -6.1], root)
  box([0.36, 0.46, 0.32], 0x2a3631, [7.1, 1.2, -6.1], root)
  cyl(0.05, 0.08, 0xffffff, [7.1, 1.0, -5.98], root)
  box([0.25, 0.3, 0.25], 0xc4534f, [7.9, 1.12, -6.1], root)
  // Server rack with blinking LEDs
  const leds = []
  box([1.1, 2.1, 0.9], 0x1c2421, [-9.1, 1.05, -5.9], root, { metal: 0.3, rough: 0.4 })
  for (let y = 0.3; y < 2; y += 0.18)
    for (let i = 0; i < 4; i++)
      leds.push(
        box([0.05, 0.03, 0.01], 0x7dffa8, [-9.45 + i * 0.08, y, -5.44], root, {
          shadow: false,
          material: new THREE.MeshBasicMaterial({ color: 0x7dffa8 }),
        }),
      )
  // Bookshelf, plants, lamps
  box([1.6, 2, 0.4], 0xb7835a, [-6, 1, -6.55], root)
  for (let y = 0.4; y < 2; y += 0.5) {
    box([1.5, 0.04, 0.36], 0xe6d3b0, [-6, y, -6.5], root)
    for (let i = 0; i < 7; i++)
      box(
        [0.11, 0.32, 0.24],
        [0x3d7fb8, 0xc4534f, 0xd08a3c, 0x3f9a63][i % 4],
        [-6.6 + i * 0.18, y + 0.18, -6.48],
        root,
      )
  }
  for (const [x, z, s] of [
    [9, -5.8, 1.1],
    [-9.2, 3.2, 1],
    [8.8, 2.6, 0.9],
    [2, -6.3, 0.8],
    [-3, 3.6, 0.7],
  ]) {
    cyl(0.24 * s, 0.5 * s, 0xb56b4c, [x, 0.25 * s, z], root)
    for (let i = 0; i < 6; i++)
      ball(
        0.26 * s,
        0x517b4d,
        [x + Math.sin(i * 2.1) * 0.2 * s, 0.65 * s + i * 0.12 * s, z + Math.cos(i * 2.1) * 0.18 * s],
        root,
      )
  }
  for (const [x, z] of [
    [-4.6, -5.9],
    [8.6, 0.8],
  ]) {
    cyl(0.18, 0.03, 0x2a3631, [x, 0.015, z], root)
    cyl(0.02, 1.6, 0x2a3631, [x, 0.8, z], root)
    cyl(0.2, 0.26, 0xf2e6c8, [x, 1.68, z], root, { emissive: 0xffe6b0, intensity: 0.6 })
  }

  const colors = {
    idle: new THREE.Color(0x384a43),
    approval: new THREE.Color(0xffb02e),
    completed: new THREE.Color(0x2fbf71),
    failed: new THREE.Color(0xd9534f),
  }
  function setTeam(team) {
    board.draw(team)
    const tone = team.approval
      ? 'approval'
      : team.outcome === 'completed'
        ? 'completed'
        : team.outcome === 'failed'
          ? 'failed'
          : 'idle'
    approvalLight.material.color.copy(colors[tone])
    approvalLight.material.emissive.copy(tone === 'idle' ? new THREE.Color(0) : colors[tone])
    approvalLight.material.emissiveIntensity = 0.8
  }
  function update(t, team) {
    for (const [id, s] of Object.entries(screens))
      s.draw(team?.agents[id]?.status || 'standby', t, { phase: team?.phase, iteration: team?.iteration })
    leds.forEach((l, i) => {
      l.visible = Math.sin(t * 3 + i * 1.7) > -0.6
    })
  }
  function dispose() {
    for (const s of Object.values(screens)) s.dispose()
    board.dispose()
  }
  return { setTeam, update, dispose, desks, screens }
}
