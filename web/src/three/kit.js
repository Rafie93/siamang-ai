import * as THREE from 'three'
// Shared unit geometries scaled per mesh + a material cache keep draw setup small.
export function createKit() {
  const materials = new Map(),
    geometries = new Map()
  const unitBox = new THREE.BoxGeometry(1, 1, 1),
    unitSphere = new THREE.SphereGeometry(0.5, 18, 14),
    unitCyl = new THREE.CylinderGeometry(0.5, 0.5, 1, 18)
  function mat(
    color,
    { rough = 0.75, metal = 0, emissive = 0, intensity = 1, transparent = false, opacity = 1 } = {},
  ) {
    const key = [color, rough, metal, emissive, intensity, transparent, opacity].join('|')
    if (!materials.has(key))
      materials.set(
        key,
        new THREE.MeshStandardMaterial({
          color,
          roughness: rough,
          metalness: metal,
          emissive,
          emissiveIntensity: intensity,
          transparent,
          opacity,
        }),
      )
    return materials.get(key)
  }
  function place(geo, material, [x, y, z], parent, { shadow = true, receive = true } = {}) {
    const m = new THREE.Mesh(geo, material)
    m.position.set(x, y, z)
    m.castShadow = shadow
    m.receiveShadow = receive
    parent?.add(m)
    return m
  }
  const box = (size, color, pos, parent, o = {}) => {
    const m = place(unitBox, o.material || mat(color, o), pos, parent, o)
    m.scale.set(...size)
    return m
  }
  const ball = (r, color, pos, parent, o = {}) => {
    const m = place(unitSphere, o.material || mat(color, o), pos, parent, o)
    m.scale.setScalar(r * 2)
    return m
  }
  const cyl = (r, h, color, pos, parent, o = {}) => {
    const m = place(unitCyl, o.material || mat(color, o), pos, parent, o)
    m.scale.set(r * 2, h, r * 2)
    return m
  }
  function capsule(r, len, color, parent, o = {}) {
    const key = `c${r}|${len}`
    if (!geometries.has(key)) {
      const g = new THREE.CapsuleGeometry(r, len, 4, 10)
      g.translate(0, -len / 2 - r, 0)
      geometries.set(key, g)
    }
    return place(geometries.get(key), o.material || mat(color, o), [0, 0, 0], parent, o)
  }
  function group(pos = [0, 0, 0], parent, rotY = 0) {
    const g = new THREE.Group()
    g.position.set(...pos)
    g.rotation.y = rotY
    parent?.add(g)
    return g
  }
  function dispose() {
    for (const m of materials.values()) m.dispose()
    for (const g of geometries.values()) g.dispose()
    unitBox.dispose()
    unitSphere.dispose()
    unitCyl.dispose()
  }
  return { mat, box, ball, cyl, capsule, group, dispose }
}
