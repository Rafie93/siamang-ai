// Office floor plan in metres. Pure data + routing so it can be tested without WebGL.
// Characters walk desk → lane → main aisle → lane → destination, never through furniture.
export const AISLE_Z = -1
const back = -4.15,
  front = 2.25
const desk = (x, z, face, deskZ) => ({ seat: [x, z], face, desk: [x, deskZ], lane: [x + 1.15, z] })
export const DESKS = {
  planner: desk(-3.5, back, 0, -3.35),
  analyst: desk(0, back, 0, -3.35),
  programmer: desk(-3.5, front, Math.PI, 1.45),
  qa: desk(0, front, Math.PI, 1.45),
  reviewer: desk(3.5, front, Math.PI, 1.45),
  amang: { seat: [-8, 1], face: Math.PI / 2, desk: [-7.15, 1], lane: [-7.9, -0.05] },
}
// Furniture footprints [minX,minZ,maxX,maxZ] for collision tests and layout.
export const OBSTACLES = [
  ...['planner', 'analyst', 'programmer', 'qa', 'reviewer'].map(id => {
    const [x, z] = DESKS[id].desk
    return [x - 0.85, z - 0.42, x + 0.85, z + 0.42]
  }),
  [-7.6, 0.15, -6.7, 1.85],
  [4.4, -3.1, 6.4, -1.6],
  [6.5, -6.6, 8.6, -5.6],
  [-9.8, -6.6, -8.4, -5.2],
  [-9.3, -3.3, -8.7, -2.7],
]
export const SPOTS = {
  coffee: { pos: [7.2, -5.05], face: Math.PI, lane: [7.2, -4.4] },
  whiteboard: { pos: [2.9, -5.6], face: Math.PI, lane: [2.9, -4.4] },
  window: { pos: [-6, -5.6], face: Math.PI, lane: [-6, -4.4] },
  approval: { pos: [-8.2, -2.2], face: -Math.PI * 0.75, lane: [-7.9, -1.6] },
}
// Where a visitor stands to talk with a seated agent.
export function visitSpot(id) {
  const d = DESKS[id]
  if (!d) return null
  if (id === 'amang') return { pos: [-7.9, -0.05], face: 0, lane: [-7.9, -0.05] }
  return { pos: [d.lane[0], d.seat[1] + (d.face === 0 ? 0.15 : -0.15)], face: -Math.PI / 2, lane: d.lane }
}
export function seatSpot(id) {
  const d = DESKS[id]
  return { pos: d.seat, face: d.face, lane: d.lane }
}

const same = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]) < 0.05
export function route(from, to) {
  // from/to: {pos,lane}; returns waypoint list ending at to.pos
  const pts = [from.lane, [from.lane[0], AISLE_Z], [to.lane[0], AISLE_Z], to.lane, to.pos],
    out = []
  for (const p of pts) if (!out.length ? !same(p, from.pos) : !same(p, out.at(-1))) out.push(p)
  return out
}
export function segmentHitsBox([ax, az], [bx, bz], [x0, z0, x1, z1], pad = 0.18) {
  x0 -= pad
  z0 -= pad
  x1 += pad
  z1 += pad
  let t0 = 0,
    t1 = 1
  const dx = bx - ax,
    dz = bz - az
  for (const [p, q] of [
    [-dx, ax - x0],
    [dx, x1 - ax],
    [-dz, az - z0],
    [dz, z1 - az],
  ]) {
    if (p === 0) {
      if (q < 0) return false
      continue
    }
    const r = q / p
    if (p < 0) {
      if (r > t1) return false
      if (r > t0) t0 = r
    } else {
      if (r < t0) return false
      if (r < t1) t1 = r
    }
  }
  return t0 < t1
}
