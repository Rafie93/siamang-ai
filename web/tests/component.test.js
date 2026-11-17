import { it, expect, vi, describe, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setThemeMode } from '../src/composables/useTheme.js'
import OfficeScene from '../src/components/OfficeScene.vue'
import MarkdownText from '../src/components/MarkdownText.vue'
import { teamState } from '../src/domain/team.js'
const dispose = vi.fn(),
  update = vi.fn(),
  setTeam = vi.fn(),
  focus = vi.fn()
let created
vi.mock('../src/three/office.js', () => ({
  createOffice: (el, opts) => {
    created = opts
    return { dispose, update, setTeam, focus, reset: vi.fn() }
  },
}))
it('cleans up scene when leaving office and forwards preferences', async () => {
  const wrapper = mount(OfficeScene, {
    props: { team: teamState({ connected: false }), motion: false, quality: 'low' },
  })
  await flushPromises()
  await wrapper.setProps({ quality: 'high' })
  expect(update).toHaveBeenCalled()
  wrapper.unmount()
  expect(dispose).toHaveBeenCalledTimes(1)
})
it('forwards team state, focus and selection; labels simulation explicitly', async () => {
  const wrapper = mount(OfficeScene, {
    props: { team: teamState({ connected: false }), motion: true, quality: 'high' },
  })
  await flushPromises()
  expect(wrapper.text()).toContain('LIVE · BACKEND')
  expect(wrapper.find('.sim-banner').exists()).toBe(false)
  await wrapper.setProps({ team: { ...teamState({ connected: true }), simulated: true } })
  expect(setTeam).toHaveBeenCalled()
  expect(wrapper.find('.sim-banner').text()).toContain('SIMULASI')
  expect(wrapper.text()).toContain('MODE DEMO')
  await wrapper.setProps({ focus: 'qa' })
  expect(focus).toHaveBeenCalledWith('qa')
  created.onSelect('planner')
  expect(wrapper.emitted('select')[0]).toEqual(['planner'])
  await wrapper.find('.scene-controls button').trigger('click')
  expect(wrapper.emitted('toggle-demo')).toHaveLength(1)
  wrapper.unmount()
})
describe('MermaidDiagram', () => {
  const render = vi.fn(),
    initialize = vi.fn()
  const show = text => mount(MarkdownText, { props: { text } })
  beforeEach(() => {
    setThemeMode('light')
    vi.clearAllMocks()
    vi.doMock('mermaid', () => ({ default: { initialize, render } }))
  })
  it('draws a Mermaid fence in strict mode and leaves other fences as code', async () => {
    render.mockResolvedValue({ svg: '<svg data-drawn="1"></svg>' })
    const wrapper = show('```mermaid\nflowchart TD\n```\n\n```\nflowchart TD\n```')
    await vi.dynamicImportSettled()
    await flushPromises()
    expect(wrapper.find('.mermaid-diagram svg').attributes('data-drawn')).toBe('1')
    expect(wrapper.findAll('pre')).toHaveLength(1)
    expect(initialize.mock.calls[0][0]).toMatchObject({
      securityLevel: 'strict',
      startOnLoad: false,
      suppressErrorRendering: true,
    })
    expect(render.mock.calls[0][1]).toBe('flowchart TD')
  })
  it('shows the source when a diagram cannot be drawn', async () => {
    render.mockRejectedValue(new Error('Parse error'))
    const wrapper = show('```mermaid\nbukan diagram\n```')
    await vi.dynamicImportSettled()
    await flushPromises()
    expect(wrapper.find('.mermaid-diagram').exists()).toBe(false)
    expect(wrapper.text()).toContain('tidak bisa digambar')
    expect(wrapper.find('pre').text()).toBe('bukan diagram')
  })
  it('redraws for a new source or theme, not for a refresh with equal text', async () => {
    render.mockResolvedValue({ svg: '<svg></svg>' })
    const wrapper = show('```mermaid\nflowchart TD\n```')
    await vi.dynamicImportSettled()
    await flushPromises()
    expect(render).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ text: '```mermaid\nflowchart TD\n```' })
    await flushPromises()
    expect(render).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ text: '```mermaid\nflowchart LR\n```' })
    await vi.dynamicImportSettled()
    await flushPromises()
    expect(render).toHaveBeenCalledTimes(2)
    setThemeMode('dark')
    await vi.dynamicImportSettled()
    await flushPromises()
    expect(render).toHaveBeenCalledTimes(3)
    expect(initialize.mock.lastCall[0].themeVariables.darkMode).toBe(true)
    setThemeMode('light')
  })
})

describe('editable diagrams', () => {
  const TEXT =
    '```python\nprint(1)\n```\n\n```mermaid\nflowchart TD\n  A --> B\n```\n\nTengah\n\n```MERMAID\nkedua\n```'
  it('offers no edit unless the page says so', () => {
    expect(
      mount(MarkdownText, { props: { text: TEXT } })
        .find('.diagram-block')
        .exists(),
    ).toBe(false)
  })
  it('offers a proposed diagram in place of what the document says', async () => {
    const wrapper = mount(MarkdownText, {
      props: { text: TEXT, editableDiagrams: true, diagramOverrides: { 1: 'usulan' } },
    })
    await wrapper.findAll('.diagram-block button')[1].trigger('click')
    expect(wrapper.emitted('edit-diagram')).toEqual([[{ index: 1, source: 'usulan', lang: 'mermaid' }]])
  })
  it('numbers diagrams past other code blocks, as the backend does', async () => {
    const wrapper = mount(MarkdownText, { props: { text: TEXT, editableDiagrams: true } })
    const buttons = wrapper.findAll('.diagram-block button')
    expect(buttons).toHaveLength(2)
    await buttons[1].trigger('click')
    await buttons[0].trigger('click')
    expect(wrapper.emitted('edit-diagram')).toEqual([
      [{ index: 1, source: 'kedua', lang: 'mermaid' }],
      [{ index: 0, source: 'flowchart TD\n  A --> B', lang: 'mermaid' }],
    ])
  })
})

describe('designer blocks', () => {
  const WIRES =
    '```html\n<main>Masuk</main>\n```\n\n```mermaid\nflowchart TD\n```\n\n```html\n<main>Beranda</main>\n```'
  it('draw HTML as code unless the document says it holds wireframes', () => {
    const wrapper = mount(MarkdownText, { props: { text: WIRES, editableDiagrams: true } })
    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.findAll('pre.code-block')).toHaveLength(2)
    expect(wrapper.findAll('.diagram-block')).toHaveLength(1)
  })
  it('draw wireframes in a frame that may do nothing, numbered with the diagrams as the backend does', async () => {
    const wrapper = mount(MarkdownText, {
      props: {
        text: WIRES,
        editableDiagrams: true,
        blocks: ['mermaid', 'html'],
        tokensCss: '--color-text: #111;',
      },
    })
    const frames = wrapper.findAll('iframe')
    expect(frames).toHaveLength(2)
    for (const frame of frames) {
      expect(frame.attributes('sandbox')).toBe('')
      expect(frame.attributes('srcdoc')).toContain("default-src 'none'")
    }
    expect(frames[1].attributes('srcdoc')).toContain('<main>Beranda</main>')
    expect(frames[0].attributes('srcdoc')).toContain('--color-text: #111;')
    const buttons = wrapper.findAll('.diagram-block-actions button')
    expect(buttons.map(b => b.text())).toEqual(['Edit wireframe', 'Edit diagram', 'Edit wireframe'])
    await buttons[2].trigger('click')
    expect(wrapper.emitted('edit-diagram')).toEqual([
      [{ index: 2, source: '<main>Beranda</main>', lang: 'html' }],
    ])
  })
  it('switch a wireframe between a desktop and a phone width', async () => {
    const wrapper = mount(MarkdownText, { props: { text: WIRES, blocks: ['mermaid', 'html'] } })
    expect(wrapper.find('iframe').attributes('width')).toBe('1280')
    await wrapper.findAll('.wireframe-device')[1].trigger('click')
    expect(wrapper.find('iframe').attributes('width')).toBe('390')
  })
  it('draw tokens as swatches, and an invalid token block as its text', () => {
    const tokens =
      '```json\n{"colors": {"text": "#111111"}, "spacing": {"sm": 8}}\n```\n\n```json\n{rusak\n```'
    const wrapper = mount(MarkdownText, { props: { text: tokens, blocks: ['mermaid', 'json'] } })
    expect(wrapper.find('.token-name').text()).toBe('text')
    expect(wrapper.find('.token-list').text()).toContain('--space-sm')
    expect(wrapper.text()).toContain('JSON token belum valid.')
  })
})

describe('anchors for the table of contents', () => {
  it('give headings and blocks the ids the outline jumps to', () => {
    const text =
      '# Satu\n\n```mermaid\nflowchart TD\n```\n\n## Dua\n\n```html\n<p/>\n```\n\n```mermaid\nkedua\n```'
    const wrapper = mount(MarkdownText, { props: { text, anchor: 'doc-x' } })
    expect(wrapper.findAll('[id^="doc-x-"]').map(e => e.attributes('id'))).toEqual([
      'doc-x-h0',
      'doc-x-b0',
      'doc-x-h1',
      'doc-x-b1',
    ])
    expect(mount(MarkdownText, { props: { text } }).find('[id]').exists()).toBe(false)
  })
})
