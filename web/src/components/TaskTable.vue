<script setup>
import { ArrowLeft, ArrowRight, ArrowUpRight } from '@lucide/vue'
import { computed } from 'vue'
import StatusChip from '@/components/StatusChip.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const statusItems = computed(() => [
  { title: 'Semua', value: '' },
  ...(office.cap?.task_statuses || []).map(s => ({ title: s, value: s })),
])
</script>

<template>
  <AppCard title="Tasks">
    <template #actions>
      <div class="status-filter">
        <AppSelect
          aria-label="Status"
          :model-value="office.filter"
          :items="statusItems"
          @update:model-value="office.setFilter"
        />
      </div>
    </template>

    <p v-if="office.loading" class="mb-0">Memuat task…</p>
    <p v-else-if="!office.tasks.length" class="muted mb-0">
      Belum ada task pada halaman ini. Kirim instruksi untuk memulai.
    </p>
    <div v-else class="table-wrap">
      <table class="table task-table">
        <thead>
          <tr>
            <th>Tugas</th>
            <th>Status</th>
            <th>Tahap teramati</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="t in office.tasks"
            :key="t.task_id"
            :class="{ 'task-selected': t.task_id === office.selectedId }"
          >
            <td class="task-title">
              <div class="truncate">{{ t.title || t.description }}</div>
              <small class="block truncate faint">{{ t.task_id }}</small>
            </td>
            <td><StatusChip :status="t.status" /></td>
            <td style="white-space: nowrap">{{ office.visualStatus(t, office.connected).label }}</td>
            <td style="text-align: end">
              <AppButton variant="secondary" size="sm" @click="office.choose(t)">
                Detail <ArrowUpRight :size="14" />
              </AppButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="row-between mt">
      <AppButton
        variant="ghost"
        size="sm"
        :disabled="office.offset === 0"
        @click="office.setOffset(Math.max(0, office.offset - 20))"
      >
        <ArrowLeft :size="14" /> Sebelumnya
      </AppButton>
      <span class="small">{{ office.offset + 1 }}–{{ office.offset + office.tasks.length }}</span>
      <AppButton
        variant="ghost"
        size="sm"
        :disabled="office.tasks.length < 20"
        @click="office.setOffset(office.offset + 20)"
      >
        Berikutnya <ArrowRight :size="14" />
      </AppButton>
    </div>
  </AppCard>
</template>
