<script setup>
import { ChevronDown } from '@lucide/vue'

defineProps({
  open: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
defineEmits(['toggle'])
</script>

<template>
  <div class="collapse-item">
    <button
      type="button"
      class="collapse-head"
      :aria-expanded="open"
      :disabled="disabled"
      @click="$emit('toggle')"
    >
      <span class="collapse-title"><slot name="title" /></span>
      <slot name="aside" />
      <ChevronDown class="collapse-chevron" :size="16" aria-hidden="true" />
    </button>
    <!-- Rendered only while open: a diagram cannot be measured inside a hidden element. -->
    <div v-if="open" class="collapse-body"><slot /></div>
  </div>
</template>
