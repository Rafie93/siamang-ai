<script setup>
import { RouterLink } from 'vue-router'

defineProps({
  variant: { type: String, default: 'primary' }, // primary | secondary | ghost | danger
  size: { type: String, default: 'md' }, // md | sm
  type: { type: String, default: 'button' },
  to: { type: Object, default: null },
  disabled: { type: Boolean, default: false },
})
</script>

<template>
  <RouterLink v-if="to" :to="to" class="btn" :class="[`btn-${variant}`, { 'btn-sm': size === 'sm' }]">
    <slot />
  </RouterLink>
  <button
    v-else
    :type="type"
    :disabled="disabled"
    class="btn"
    :class="[`btn-${variant}`, { 'btn-sm': size === 'sm' }]"
  >
    <slot />
  </button>
</template>
