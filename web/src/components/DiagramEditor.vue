<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import MermaidDiagram from '@/components/MermaidDiagram.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import { errorText } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const props = defineProps({
  kind: { type: String, required: true },
  title: { type: String, required: true },
  index: { type: Number, required: true },
  source: { type: String, required: true },
  base: { type: String, required: true },
})
const emit = defineEmits(['close'])

const office = useOfficeStore()
const PREVIEW_DELAY_MS = 250
const code = ref(props.source)
const preview = ref(props.source)
const drawn = ref(null) // what the preview last reported, for the text it was given
const failure = ref('')
const leaving = ref(false)
let timer

const changed = computed(() => code.value !== props.source)
const settled = computed(() => drawn.value?.source === code.value)
const valid = computed(() => settled.value && !drawn.value.error)
const canSave = computed(() => changed.value && valid.value && !office.busy && office.can('approve'))
const status = computed(() => {
  if (!settled.value) return 'Menggambar…'
  if (drawn.value.error) return 'Kode belum valid, jadi belum bisa disimpan.'
  return changed.value ? 'Valid. Perubahan belum disimpan.' : 'Belum ada perubahan.'
})

watch(code, value => {
  leaving.value = false
  clearTimeout(timer)
  timer = setTimeout(() => (preview.value = value), PREVIEW_DELAY_MS)
})
onBeforeUnmount(() => clearTimeout(timer))

const close = () => {
  if (changed.value && !leaving.value) leaving.value = true
  else emit('close')
}
const save = async () => {
  failure.value = ''
  try {
    await office.saveDiagram(props.kind, props.index, code.value, props.base)
    emit('close')
  } catch (e) {
    failure.value = e.code || 'diagram_save_failed'
  }
}
</script>

<template>
  <AppDialog
    open
    class="diagram-editor"
    :width="1440"
    :persistent="office.busy"
    :title="`Edit diagram: ${title}`"
    :subtitle="
      office.editMode === 'revision'
        ? 'Task ini sudah terbit, jadi perubahan disimpan sebagai usulan untuk task revisi. Dokumen aslinya tidak berubah.'
        : 'Tulis kode Mermaid di kiri. Preview di kanan mengikuti saat Anda mengetik.'
    "
    @close="close"
  >
    <div class="diagram-editor-panes">
      <div class="diagram-editor-pane">
        <label class="diagram-editor-label" for="diagram-editor-code">Kode Mermaid</label>
        <textarea
          id="diagram-editor-code"
          v-model="code"
          class="diagram-editor-code"
          spellcheck="false"
          autocapitalize="off"
          autocomplete="off"
          maxlength="20000"
        />
      </div>
      <div class="diagram-editor-pane">
        <span class="diagram-editor-label">Preview</span>
        <div class="diagram-editor-preview">
          <MermaidDiagram :source="preview" live @result="drawn = $event" />
        </div>
        <pre v-if="settled && drawn.error" class="diagram-editor-error" role="alert">{{ drawn.error }}</pre>
      </div>
    </div>
    <template #actions>
      <AppAlert v-if="failure" tone="error" style="flex-basis: 100%">{{ errorText(failure) }}</AppAlert>
      <AppAlert v-if="leaving" tone="warning" style="flex-basis: 100%">
        Perubahan belum disimpan. Tekan "Buang perubahan" untuk menutup tanpa menyimpan.
      </AppAlert>
      <p class="diagram-editor-status muted" role="status" style="margin-inline-end: auto">{{ status }}</p>
      <AppButton variant="secondary" :disabled="office.busy" @click="close">
        {{ leaving ? 'Buang perubahan' : 'Batal' }}
      </AppButton>
      <AppButton :disabled="!canSave" @click="save">{{ office.busy ? 'Menyimpan…' : 'Simpan' }}</AppButton>
    </template>
  </AppDialog>
</template>
