<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import AgentUsageTable from '@/components/AgentUsageTable.vue'
import StatusChip from '@/components/StatusChip.vue'
import UsageTotals from '@/components/UsageTotals.vue'
import { timeText, tokenCell, tokenExact, tokenTitle } from '@/domain/usage.js'
import { useOfficeStore } from '@/stores/office'

const props = defineProps({ projectId: { type: String, required: true } })
const office = useOfficeStore()
const usage = computed(() =>
  office.projectUsage?.project_id === props.projectId ? office.projectUsage : null,
)
const unavailable = computed(() => office.projectUsage === false)
const outside = computed(() => usage.value?.outside_tasks)

let release = null
onMounted(() => {
  release = office.watchProjectUsage()
})
onBeforeUnmount(() => release?.())

const openTask = t => {
  office.pickTask(t.task_id)
  office.go('tasks')
}
</script>

<template>
  <p v-if="unavailable" class="muted mb-0">Pemakaian token belum bisa dibaca dari backend.</p>
  <p v-else-if="!usage" class="muted mb-0">Memuat pemakaian token…</p>
  <template v-else>
    <UsageTotals :usage="usage" />

    <h3 class="section-title mt">Per agent</h3>
    <AgentUsageTable :agents="usage.agents" />

    <h3 class="section-title mt">Per task</h3>
    <p v-if="!usage.tasks.length && !outside?.calls" class="muted mb-0">Belum ada task.</p>
    <div v-else class="table-wrap">
      <table class="table usage-table" aria-label="Pemakaian per task">
        <thead>
          <tr>
            <th>Task</th>
            <th class="num">Input</th>
            <th class="num">Output</th>
            <th class="num">Total</th>
            <th class="num">Durasi kerja</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in usage.tasks" :key="t.task_id">
            <td class="task-title">
              <button type="button" class="link-row usage-task" @click="openTask(t)">
                <span class="truncate">{{ t.title }}</span>
                <StatusChip :status="t.status" />
              </button>
            </td>
            <td class="num" :title="tokenTitle(t)">{{ tokenCell(t, 'input_tokens') }}</td>
            <td class="num" :title="tokenExact(t.output_tokens)">{{ tokenCell(t, 'output_tokens') }}</td>
            <td class="num" :title="tokenExact(t.total_tokens)">
              <strong>{{ tokenCell(t, 'total_tokens') }}</strong>
            </td>
            <td class="num">{{ timeText(t.work_ms) }}</td>
          </tr>
          <tr v-if="outside?.calls">
            <td>
              <strong>Di luar task</strong>
              <small class="block faint"
                >Percakapan spesifikasi dengan Amang dan draft yang tidak jadi task</small
              >
            </td>
            <td class="num" :title="tokenTitle(outside)">{{ tokenCell(outside, 'input_tokens') }}</td>
            <td class="num" :title="tokenExact(outside.output_tokens)">
              {{ tokenCell(outside, 'output_tokens') }}
            </td>
            <td class="num" :title="tokenExact(outside.total_tokens)">
              <strong>{{ tokenCell(outside, 'total_tokens') }}</strong>
            </td>
            <td class="num">{{ timeText(outside.active_ms) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
