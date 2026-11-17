<script setup>
import { computed } from 'vue'
import { totalsFor, usageNotes } from '@/domain/usage.js'

const props = defineProps({ usage: { type: Object, default: null } })
const totals = computed(() => totalsFor(props.usage))
const notes = computed(() => usageNotes(props.usage))
</script>

<template>
  <dl class="project-stats usage-totals">
    <div v-for="t in totals" :key="t.key" :title="t.title || undefined">
      <dd>{{ t.value }}</dd>
      <dt>{{ t.label }}</dt>
    </div>
  </dl>
  <p v-for="n in notes" :key="n" class="small muted mt mb-0">{{ n }}</p>
</template>
