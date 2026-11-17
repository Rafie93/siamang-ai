<script setup>
import { computed, ref, watch } from 'vue'
import StatusChip from '@/components/StatusChip.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { callsText, timeText, tokenCell, tokenExact, tokenTitle, usageAgentFor } from '@/domain/usage.js'
import { request } from '@/services/api.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const detail = computed(() => (office.agentPopup ? office.agentInfo(office.agentPopup) : null))

// What this agent used on the task in front of it. Read when the dialog opens and again as the agent moves to another
// stage: that follows it closely enough without a request for every model call. Each source is compared on its own,
// so the detail being rebuilt every second by the clock reads nothing again.
const spent = ref(null)
watch(
  [() => detail.value?.id, () => detail.value?.task_id, () => detail.value?.label],
  async ([agent, taskId], before) => {
    if (!before || agent !== before[0] || taskId !== before[1]) spent.value = null
    if (!agent || !taskId || detail.value?.simulated) return
    const usage = await request(`/tasks/${taskId}/usage`).catch(() => null)
    if (!usage || detail.value?.id !== agent || detail.value?.task_id !== taskId) return
    spent.value = usage.agents.find(a => a.agent === usageAgentFor(agent)) || {
      calls: 0,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      active_ms: 0,
    }
  },
  { immediate: true },
)
const close = () => {
  office.agentPopup = ''
}
const openProject = id => {
  close()
  office.openProject(id)
}
const openDocuments = () => {
  office.showDocuments(detail.value.id)
  close()
}
const statusColor = {
  working: 'running',
  waiting: 'waiting',
  blocked: 'failed',
  done: 'done',
  offline: 'cancelled',
}
const docColor = { generated: 'success', failed: 'error', not_run: 'warning' }
const subtitle = computed(() =>
  detail.value ? `${detail.value.role}${detail.value.model ? ` · ${detail.value.model}` : ''}` : '',
)
</script>

<template>
  <AppDialog :open="!!detail" :width="640" :title="detail?.name" :subtitle="subtitle" @close="close">
    <template #icon>
      <span class="agent-initial" :style="{ background: detail.color }" aria-hidden="true">{{
        detail.name[0]
      }}</span>
    </template>
    <template #aside>
      <StatusChip
        :status="statusColor[detail.status] || detail.status"
        :text="office.statusText[detail.status] || detail.status"
      />
    </template>

    <div class="stack">
      <section aria-live="polite" class="agent-now" :data-working="detail.working">
        <div class="row">
          <span v-if="detail.working" class="spinner" aria-hidden="true" />
          <strong>{{ detail.doing }}</strong>
        </div>
        <p v-if="detail.task" class="small mb-0">
          {{ detail.project ? `${detail.project} · ` : '' }}{{ detail.task }}
        </p>
        <small v-if="detail.since" class="muted">Tahap ini berjalan {{ detail.since }}</small>
        <p v-if="detail.pending.length" class="small mb-0">Sedang ditulis: {{ detail.pending.join(', ') }}</p>
      </section>

      <section v-if="spent" aria-label="Pemakaian token agent ini">
        <h3 class="section-title">Pemakaian di task ini</h3>
        <dl class="project-stats usage-totals">
          <div :title="spent.calls ? tokenTitle(spent) : undefined">
            <dd>{{ tokenCell(spent, 'input_tokens') }}</dd>
            <dt>Input</dt>
          </div>
          <div :title="tokenExact(spent.output_tokens ?? 0)">
            <dd>{{ tokenCell(spent, 'output_tokens') }}</dd>
            <dt>Output</dt>
          </div>
          <div :title="tokenExact(spent.total_tokens ?? 0)">
            <dd>{{ tokenCell(spent, 'total_tokens') }}</dd>
            <dt>Total token</dt>
          </div>
          <div title="Lama agent ini bekerja; pekerjaan paralel dihitung sekali">
            <dd>{{ timeText(spent.active_ms) }}</dd>
            <dt>Durasi</dt>
          </div>
        </dl>
        <small class="muted">{{ callsText(spent) }}</small>
      </section>

      <section v-if="detail.projects.length">
        <h3 class="section-title">Proyek yang dipegang</h3>
        <ul class="agent-projects">
          <li v-for="p in detail.projects" :key="p.project_id">
            <button type="button" @click="openProject(p.project_id)">
              <strong>{{ p.name }}</strong>
              <small>{{ p.running ? `Berjalan: ${p.running}` : p.step }}</small>
              <small v-for="o in p.open" :key="o" class="text-warning">{{ o }}</small>
            </button>
          </li>
        </ul>
      </section>

      <section v-if="detail.design.rows.length">
        <h3 class="section-title">Pembagian tugas menulis</h3>
        <p v-for="n in detail.design.notes" :key="n" class="small">{{ n }}</p>
        <ul class="agent-docs">
          <li v-for="r in detail.design.rows" :key="r.key">
            <span>
              {{ r.label }}{{ r.attempt > 1 ? ` · percobaan ke-${r.attempt}` : '' }}
              <small class="block muted">{{ r.titles.join(', ') }}</small>
              <small v-if="r.reason" class="block text-error">{{ r.reason }}</small>
            </span>
            <AppTag>{{ r.stateText }}{{ r.took ? ` · ${r.took}` : '' }}</AppTag>
          </li>
        </ul>
      </section>

      <section v-if="detail.packages.length">
        <h3 class="section-title">Paket kerja</h3>
        <ul class="agent-docs">
          <li v-for="p in detail.packages" :key="p.key">
            <span>
              {{ p.title }}
              <small class="muted"> · {{ [p.note, p.owns.join(', ')].filter(Boolean).join(' · ') }}</small>
            </span>
            <AppTag>{{ p.phase || p.stateText }}</AppTag>
          </li>
        </ul>
      </section>

      <section v-if="detail.steps.length">
        <h3 class="section-title">Tahap yang dikerjakan</h3>
        <ol class="agent-steps">
          <li v-for="s in detail.steps" :key="s.position">
            <span
              >{{ s.text }}<small v-if="s.detail"> · {{ s.detail }}</small></span
            >
            <small class="muted">{{ office.localTime(s.at) }}{{ s.took ? ` · ${s.took}` : '' }}</small>
          </li>
        </ol>
      </section>

      <section v-if="detail.documents.length">
        <h3 class="section-title">Hasil kerja ({{ detail.generated }} dari {{ detail.documents.length }})</h3>
        <ul class="agent-docs">
          <li v-for="d in detail.documents" :key="d.kind">
            <span>{{ d.title }}</span>
            <AppTag :tone="docColor[d.status]">{{ d.statusText }}</AppTag>
          </li>
        </ul>
      </section>

      <small class="faint">
        Diperbarui {{ office.localTime(office.updated) }} dari backend. Isi tulisan model baru terlihat
        setelah tahapnya selesai.
        {{ detail.simulated ? 'Ini simulasi, bukan aktivitas backend.' : '' }}
      </small>
    </div>

    <template #actions>
      <AppButton variant="secondary" @click="openDocuments">Buka dokumen</AppButton>
      <AppButton @click="close">Tutup</AppButton>
    </template>
  </AppDialog>
</template>
