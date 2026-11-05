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
