<script setup>
// The owner asks one agent about a task, or about the whole project. Asking changes nothing in the work:
// the backend answers with the agent's own model, beside the run and never on its worker.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ConversationThread from '@/components/ConversationThread.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import { DOCUMENT_AGENTS } from '@/domain/documents.js'
import { errorText } from '@/domain/labels.js'
import { post, request } from '@/services/api.js'
import { useOfficeStore } from '@/stores/office'

const props = defineProps({
  // The backend's agent id: lead, planner, analyst, designer, programmer, qa, reviewer.
  agent: { type: String, required: true },
  projectId: { type: String, default: '' },
  // Empty: the thread about the whole project.
  taskId: { type: String, default: '' },
})

const office = useOfficeStore()
const thread = ref(null)
const draft = ref('')
const sending = ref(false)
const error = ref('')
const name = computed(() => DOCUMENT_AGENTS.find(a => a.id === props.agent)?.name || props.agent)
const path = computed(() =>
  props.taskId
    ? `/tasks/${props.taskId}/agents/${props.agent}/chat`
    : props.projectId
      ? `/projects/${props.projectId}/agents/${props.agent}/chat`
      : '',
)
// A failed turn is kept in the thread, shown as what went wrong instead of as words the agent said.
const messages = computed(() =>
  (thread.value?.items || []).map(m =>
    m.error ? { ...m, message: `Gagal menjawab: ${errorText(m.error)}` } : m,
  ),
)
const thinking = computed(() => !!thread.value?.thinking)
const unavailable = computed(() => thread.value?.available === false)

let poll
async function load() {
  const at = path.value
  if (!at) return (thread.value = null)
  try {
    const got = await request(at)
    if (at === path.value) thread.value = got
  } catch (e) {
    if (at === path.value) error.value = errorText(e.code)
  }
}
// The stream says when a thread changed. While an answer is awaited, a slow poll covers a stream that is down.
watch(thinking, on => {
  clearInterval(poll)
  if (on) poll = setInterval(() => !office.streaming && load(), 3000)
})
onBeforeUnmount(() => clearInterval(poll))
watch(
  path,
  () => {
    thread.value = null
    error.value = ''
    load()
  },
  { immediate: true },
)
watch(
  () => office.agentChatChange,
  change => {
    if (change && change.project_id === props.projectId && (change.task_id || '') === (props.taskId || ''))
      load()
  },
)

async function send() {
  const content = draft.value.trim()
  if (!content || sending.value || thinking.value) return
  sending.value = true
  error.value = ''
  try {
    await post(path.value, { content })
    draft.value = ''
    await load()
  } catch (e) {
    error.value = errorText(e.code)
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <div class="agent-chat stack">
    <p v-if="!messages.length && !thinking" class="small muted mb-0">
      Tanyakan apa saja kepada {{ name }} tentang {{ taskId ? 'task ini' : 'proyek ini' }}. Bertanya tidak
      mengubah pekerjaan. Untuk meminta perubahan, kirim arahan lewat Amang.
    </p>
    <ConversationThread
      v-if="messages.length"
      :messages="messages"
      :speaker="name"
      aria-live="polite"
      :aria-label="`Percakapan dengan ${name}`"
    />
    <p v-if="thinking" class="small muted row" role="status">
      <span class="spinner" aria-hidden="true" />{{ name }} sedang menjawab…
    </p>
    <AppAlert v-if="unavailable" tone="warning" role="status">
      {{ name }} belum diberi model. Pilih di halaman Settings.
    </AppAlert>
    <AppAlert v-if="error" tone="error" title="Belum terkirim">{{ error }}</AppAlert>
    <form v-if="!unavailable" class="stack" @submit.prevent="send">
      <AppTextarea
        v-model="draft"
        :label="`Pertanyaan untuk ${name}`"
        rows="2"
        maxlength="4000"
        @keydown.enter.exact.meta.prevent="send"
        @keydown.enter.exact.ctrl.prevent="send"
      />
      <div class="row">
        <AppButton
          type="submit"
          :disabled="!office.can(taskId ? 'run' : 'submit') || !draft.trim() || sending || thinking"
        >
          {{ sending ? 'Mengirim…' : 'Tanya' }}
        </AppButton>
      </div>
    </form>
  </div>
</template>
