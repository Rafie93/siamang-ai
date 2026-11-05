<script setup>
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from '@lucide/vue'
import { computed } from 'vue'

const props = defineProps({
  tone: { type: String, default: 'info' }, // info | success | warning | error
  title: { type: String, default: '' },
  closeLabel: { type: String, default: '' }, // set it to make the alert closable
})

defineEmits(['close'])

const ICONS = { info: Info, success: CircleCheck, warning: TriangleAlert, error: CircleAlert }
const icon = computed(() => ICONS[props.tone] || Info)
</script>

<template>
  <div class="alert" role="alert" :data-tone="tone">
    <component :is="icon" class="alert-icon" :size="16" aria-hidden="true" />
    <div class="alert-content">
      <strong v-if="title" class="alert-title">{{ title }}</strong>
      <slot />
    </div>
    <div v-if="$slots.actions" class="alert-actions"><slot name="actions" /></div>
    <button v-if="closeLabel" type="button" class="icon-btn" :aria-label="closeLabel" @click="$emit('close')">
      <X :size="15" aria-hidden="true" />
    </button>
  </div>
</template>
