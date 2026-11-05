<script setup>
// A native modal <dialog>: the browser traps focus, makes the page behind it inert, handles Escape, and
// returns focus to whatever opened it.
import { onMounted, ref, useId, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  width: { type: Number, default: 560 },
  persistent: { type: Boolean, default: false }, // cannot be dismissed, for example while saving
})

const emit = defineEmits(['close'])

const el = ref(null)
const titleId = useId()

function sync() {
  const dialog = el.value
  if (!dialog) return
  if (props.open && !dialog.open) dialog.showModal?.()
  if (!props.open && dialog.open) dialog.close?.()
}

function dismiss() {
  if (!props.persistent) emit('close')
}

// A browser may close the dialog on a second Escape even when the first was refused.
function closedByBrowser() {
  if (!props.open) return
  if (props.persistent) el.value?.showModal?.()
  else emit('close')
}

onMounted(sync)
watch(() => props.open, sync, { flush: 'post' })
</script>

<template>
  <dialog
    ref="el"
    class="dialog"
    :style="{ maxWidth: `min(${width}px, calc(100vw - 32px))` }"
    :aria-labelledby="titleId"
    @cancel.prevent="dismiss"
    @close="closedByBrowser"
    @click.self="dismiss"
  >
    <div v-if="open" class="dialog-inner">
      <header class="dialog-head">
        <slot name="icon" />
        <div style="flex: 1; min-width: 0">
          <h2 :id="titleId" class="dialog-title">{{ title }}</h2>
          <p v-if="subtitle" class="dialog-subtitle">{{ subtitle }}</p>
        </div>
        <slot name="aside" />
      </header>
      <div class="dialog-body"><slot /></div>
      <footer v-if="$slots.actions" class="dialog-actions"><slot name="actions" /></footer>
    </div>
  </dialog>
</template>
