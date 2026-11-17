import { it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const request = vi.fn(),
  post = vi.fn()
vi.mock('../src/services/api.js', () => ({ request: (...a) => request(...a), post: (...a) => post(...a) }))
vi.mock('../src/stores/office', () => ({
  useOfficeStore: () => store,
}))
let store
const { default: AgentChat } = await import('../src/components/AgentChat.vue')
const { reactive } = await import('vue')

beforeEach(() => {
  setActivePinia(createPinia())
  store = reactive({ can: () => true, streaming: true, agentChatChange: null })
  vi.clearAllMocks()
})

const thread = (items, extra = {}) => ({
  agent: 'designer',
  items,
  thinking: false,
  available: true,
  ...extra,
})

it('asks the agent of a task, names it, and shows a failed answer as a failure', async () => {
  request.mockResolvedValueOnce(
    thread([
      { position: 1, role: 'owner', message: 'Kenapa sidebar?' },
      { position: 2, role: 'agent', error: 'timeout' },
    ]),
  )
  const wrapper = mount(AgentChat, { props: { agent: 'designer', projectId: 'prj_a', taskId: 'tsk_b' } })
  await flushPromises()
  expect(request).toHaveBeenCalledWith('/tasks/tsk_b/agents/designer/chat')
  expect(wrapper.text()).toContain('Desainer')
  expect(wrapper.text()).toContain('Gagal menjawab: Model terlalu lama menjawab.')

  post.mockResolvedValueOnce({ agent: 'designer', state: 'thinking' })
  request.mockResolvedValueOnce(thread([{ position: 3, role: 'owner', message: 'Lagi' }], { thinking: true }))
  await wrapper.find('textarea').setValue('Lagi')
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  expect(post).toHaveBeenCalledWith('/tasks/tsk_b/agents/designer/chat', { content: 'Lagi' })
  expect(wrapper.text()).toContain('Desainer sedang menjawab')
  expect(wrapper.find('button[type=submit]').attributes('disabled')).toBeDefined()
})

it('reloads only when its own thread changes, and asks about the project without a task', async () => {
  request.mockResolvedValue(thread([]))
  mount(AgentChat, { props: { agent: 'analyst', projectId: 'prj_a' } })
  await flushPromises()
  expect(request).toHaveBeenLastCalledWith('/projects/prj_a/agents/analyst/chat')
  store.agentChatChange = { project_id: 'prj_a', task_id: 'tsk_other' }
  await flushPromises()
  expect(request).toHaveBeenCalledTimes(1)
  store.agentChatChange = { project_id: 'prj_a', task_id: null }
  await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
})

it('offers no question box to an agent without a model', async () => {
  request.mockResolvedValueOnce(thread([], { available: false }))
  const wrapper = mount(AgentChat, { props: { agent: 'qa', projectId: 'prj_a', taskId: 'tsk_b' } })
  await flushPromises()
  expect(wrapper.find('form').exists()).toBe(false)
  expect(wrapper.text()).toContain('QA belum diberi model')
})
