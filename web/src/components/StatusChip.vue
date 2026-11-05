<script setup>
import AppTag from '@/components/ui/AppTag.vue'

defineProps({
  status: { type: String, default: '' },
  text: { type: String, default: '' },
})

const TONES = {
  running: 'primary',
  queued: 'info',
  waiting: 'warning',
  failed: 'error',
  completed: 'success',
  done: 'success',
  active: 'success',
}
</script>

<template>
  <AppTag :tone="TONES[status]">{{ text || status }}</AppTag>
</template>
