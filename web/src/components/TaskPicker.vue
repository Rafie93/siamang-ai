<script setup>
import { computed } from 'vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const items = computed(() => [
  { title: 'Pilih task', value: '' },
  ...office.tasks.map(t => ({ title: t.title || t.description.slice(0, 60), value: t.task_id })),
])
</script>

<template>
  <AppSelect
    label="Task"
    :model-value="office.selectedId"
    :items="items"
    @update:model-value="office.pickTask"
  />
</template>
