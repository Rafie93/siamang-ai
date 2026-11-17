import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { createKit } from './kit.js'
import { buildOffice } from './environment.js'
import { createCharacter } from './character.js'
import { createDirector } from './director.js'
import { AGENTS, teamState } from '../domain/team.js'

const HOME = { pos: [8.4, 10.5, 12.5], target: [-1.9, 0.6, -1.6] }
export function createOffice(
  container,
  { motion = true, quality = 'high', team = teamState({ connected: false }), onSelect = () => {} } = {},
) {
  const options = { motion, quality, follow: false }
  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#dfe8e2')
  scene.fog = new THREE.Fog('#dfe8e2', 26, 48)
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 90)
  camera.position.set(...HOME.pos)
  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  container.appendChild(renderer.domElement)
  renderer.domElement.setAttribute(
    'aria-label',
    'Kantor virtual 3D: tujuh agen SIAMANG. Status tiap agen juga tersedia sebagai daftar teks.',
  )
  renderer.domElement.setAttribute('role', 'img')
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.target.set(...HOME.target)
  controls.minDistance = 5
  controls.maxDistance = 26
  controls.maxPolarAngle = Math.PI / 2.15
  controls.enablePan = true
  controls.screenSpacePanning = false
  controls.enableDamping = true
  controls.update()

  scene.add(new THREE.HemisphereLight(0xfdfbf3, 0x8a9a86, 1.6))
  const sun = new THREE.DirectionalLight(0xfff0d8, 2.6)
  sun.position.set(6, 14, -9)
  sun.target.position.set(-1, 0, -1)
  scene.add(sun, sun.target)
  Object.assign(sun.shadow.camera, { left: -13, right: 13, top: 10, bottom: -10, near: 1, far: 40 })
  sun.shadow.bias = -0.0006
  const fill = new THREE.DirectionalLight(0xdfe9ff, 0.7)
  fill.position.set(-8, 6, 10)
  scene.add(fill)

  const kit = createKit(),
    office = buildOffice(scene, kit)
  const cast = {}
  for (const a of AGENTS) {
    cast[a.id] = createCharacter(a.id, kit, { color: a.color })
    scene.add(cast[a.id].root)
  }
  const director = createDirector(cast)

  // HTML overlay for crisp name tags and speech bubbles (decorative; the accessible team list lives in Vue).
  const overlay = document.createElement('div')
  overlay.className = 'office-overlay'
  overlay.setAttribute('aria-hidden', 'true')
  container.appendChild(overlay)
  const tags = {}
  for (const a of AGENTS) {
    const tag = document.createElement('div')
    tag.className = 'agent-tag'
    tag.style.setProperty('--agent', a.color)
    const name = document.createElement('b')
    name.textContent = a.name
    const dot = document.createElement('i')
    tag.append(dot, name)
    const bubble = document.createElement('div')
    bubble.className = 'agent-bubble'
    bubble.hidden = true
    overlay.append(tag, bubble)
    tags[a.id] = { tag, bubble, name, last: '' }
  }

  const detailBox = document.createElement('div')
  detailBox.className = 'agent-detail-bubble'
  detailBox.hidden = true
  overlay.append(detailBox)
  let detail = null,
    detailKey = ''
  function setDetail(next) {
    detail = next && cast[next.id] ? next : null
    const key = detail ? JSON.stringify(detail) : ''
    if (key !== detailKey) {
      detailKey = key
      detailBox.replaceChildren()
      if (detail) {
        const title = document.createElement('b')
        title.textContent = detail.title
        detailBox.append(
          title,
          ...detail.lines.map(text => {
            const line = document.createElement('span')
            line.textContent = text
            return line
          }),
        )
      }
    }
    if (!options.motion) render()
  }
  let selected = null,
    frame = null,
    timeout = null,
    disposed = false,
    last = performance.now(),
    t = 0
  const tmp = new THREE.Vector3()
  function applyQuality() {
    renderer.setPixelRatio(Math.min(devicePixelRatio, options.quality === 'high' ? 1.75 : 1))
    renderer.shadowMap.enabled = options.quality === 'high'
    sun.castShadow = options.quality === 'high'
    sun.shadow.mapSize.set(2048, 2048)
    scene.traverse(o => {
      if (o.material) o.material.needsUpdate = true
    })
  }
  let userMoved = false
  // Pull the camera back on narrow/tall viewports so the whole floor (incl. Amang's kiosk) stays in frame.
  function homeView() {
    const fit = Math.max(1, Math.pow(1.5 / camera.aspect, 0.7))
    controls.target.set(...HOME.target)
    camera.position.set(...HOME.pos.map((v, i) => HOME.target[i] + (v - HOME.target[i]) * fit))
    controls.maxDistance = Math.max(26, 22 * fit)
    controls.update()
  }
  function resize() {
    if (disposed) return
    const w = container.clientWidth,
      h = Math.max(container.clientHeight, 1)
    camera.aspect = w / h
    camera.fov = w < 640 ? 44 : 36
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
    if (!userMoved && !selected) homeView()
    render()
  }
  function placeOverlay() {
    const w = container.clientWidth,
      h = container.clientHeight
    for (const a of AGENTS) {
      const c = cast[a.id],
        { tag, bubble } = tags[a.id],
        p = c.headPosition(tmp).project(camera)
      const visible = p.z < 1 && Math.abs(p.x) < 1.1 && Math.abs(p.y) < 1.1,
        x = (p.x * 0.5 + 0.5) * w,
        y = (-p.y * 0.5 + 0.5) * h
      tag.style.display = visible ? '' : 'none'
      tag.style.transform = `translate(-50%,-100%) translate(${x}px,${y}px)`
      const s = team.agents[a.id]?.status || 'standby',
        count = team.agents[a.id]?.count || 0,
        shown = count > 1 ? `${a.name} ×${count}` : a.name
      if (tags[a.id].name.textContent !== shown) tags[a.id].name.textContent = shown
      tag.dataset.status = s
      tag.classList.toggle('selected', selected === a.id)
      const b = detail?.id === a.id ? null : director.bubbles[a.id]
      if (detail?.id === a.id) {
        detailBox.hidden = !visible
        if (visible) {
          const bw = detailBox.offsetWidth,
            bh = detailBox.offsetHeight,
            cx = Math.min(Math.max(x, bw / 2 + 8), w - bw / 2 - 8),
            above = y - 34 - bh >= (team.simulated ? 92 : 8),
            cy = above ? y - 34 : y + 18 + bh
          detailBox.dataset.side = above ? 'above' : 'below'
          detailBox.style.setProperty('--tail', `${Math.round(x - cx)}px`)
          detailBox.style.transform = `translate(-50%,-100%) translate(${cx}px,${cy}px)`
        }
      }
      if (b && visible) {
        if (tags[a.id].last !== b.text) {
          bubble.textContent = b.text
          tags[a.id].last = b.text
        }
        bubble.dataset.tone = b.tone
        bubble.hidden = false
        bubble.style.transform = `translate(-50%,-100%) translate(${x}px,${y - 30}px)`
      } else bubble.hidden = true
    }
    if (!detail) detailBox.hidden = true
  }
  function render() {
    if (disposed) return
    renderer.render(scene, camera)
    placeOverlay()
  }
  function follow(dt) {
    const id =
      selected ||
      (options.follow
        ? Object.values(director.actors).find(a => a.script === 'handoff' && a.c.busy)?.c.id ||
          team.activeAgent
        : null)
    if (!id || (!options.follow && !focusing)) return
    const pos = cast[id].root.position,
      goal = tmp.set(pos.x, 0.8, pos.z).sub(controls.target),
      k = 1 - Math.exp(-dt * 3)
    controls.target.addScaledVector(goal, k)
    camera.position.addScaledVector(goal, k)
    if (focusing && goal.length() < 0.05) focusing = false
  }
  let focusing = false
  function step(dt) {
    t += options.motion ? dt : 0
    director.update(dt)
    for (const c of Object.values(cast)) c.update(options.motion ? dt : 1, t)
    office.update(t, team)
    follow(options.motion ? dt : 1)
    controls.update()
  }
  function tick(now = performance.now()) {
    frame = timeout = null
    if (disposed) return
    const dt = Math.min(0.05, (now - last) / 1000)
    last = now
    if (!document.hidden) {
      step(options.motion ? dt : 0.5)
      render()
    }
    if (document.hidden) return
    if (options.motion) frame = requestAnimationFrame(tick)
    else timeout = setTimeout(() => tick(performance.now()), 500)
  }
  function wake() {
    cancelAnimationFrame(frame)
    clearTimeout(timeout)
    last = performance.now()
    tick()
  }

  // Click a character to select and focus it.
  const ray = new THREE.Raycaster(),
    pointer = new THREE.Vector2()
  let down = null
  const onDown = e => {
    down = [e.clientX, e.clientY]
  }
  const onUp = e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return
    down = null
    const r = renderer.domElement.getBoundingClientRect()
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, (-(e.clientY - r.top) / r.height) * 2 + 1)
    ray.setFromCamera(pointer, camera)
    const hit = ray
      .intersectObjects(
        Object.values(cast).map(c => c.root),
        true,
      )
      .find(h => h.object.userData.agentId)
    select(hit?.object.userData.agentId || null)
    onSelect(selected)
  }
  function select(id) {
    selected = id && cast[id] ? id : null
    focusing = !!selected
    for (const c of Object.values(cast)) c.highlight(false)
    render()
  }
  renderer.domElement.addEventListener('pointerdown', onDown)
  renderer.domElement.addEventListener('pointerup', onUp)
  controls.addEventListener('change', () => {
    if (!options.motion) render()
  })
  controls.addEventListener('start', () => {
    userMoved = true
  })
  document.addEventListener('visibilitychange', wake)
  const observer = new ResizeObserver(resize)
  observer.observe(container)
  const lost = e => {
    e.preventDefault()
    container.dispatchEvent(new CustomEvent('office-lost'))
  }
  renderer.domElement.addEventListener('webglcontextlost', lost)

  function setTeam(next) {
    team = next
    director.setTeam(next)
    office.setTeam(next)
  }
  setTeam(team)
  director.setMotion(options.motion)
  applyQuality()
  resize()
  wake()
  return {
    reset() {
      select(null)
      userMoved = false
      homeView()
      render()
    },
    update(next) {
      const quality = next.quality !== undefined && next.quality !== options.quality
      Object.assign(options, Object.fromEntries(Object.entries(next).filter(([k]) => k in options)))
      director.setMotion(options.motion)
      if (next.team) setTeam(next.team)
      if (quality) {
        applyQuality()
        resize()
      }
      wake()
    },
    setTeam(next) {
      setTeam(next)
      if (!options.motion) render()
    },
    focus(id) {
      select(id)
    },
    setDetail,
    get director() {
      return director
    },
    dispose() {
      disposed = true
      cancelAnimationFrame(frame)
      clearTimeout(timeout)
      observer.disconnect()
      document.removeEventListener('visibilitychange', wake)
      renderer.domElement.removeEventListener('pointerdown', onDown)
      renderer.domElement.removeEventListener('pointerup', onUp)
      controls.dispose()
      renderer.domElement.removeEventListener('webglcontextlost', lost)
      office.dispose()
      for (const c of Object.values(cast)) c.dispose()
      scene.traverse(o => {
        if (
          o.geometry &&
          o.geometry.type !== 'BoxGeometry' &&
          o.geometry.type !== 'SphereGeometry' &&
          o.geometry.type !== 'CylinderGeometry' &&
          o.geometry.type !== 'CapsuleGeometry'
        )
          o.geometry.dispose()
        if (o.material)
          for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
            m.map?.dispose()
            m.dispose()
          }
      })
      kit.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
      overlay.remove()
    },
  }
}
