<script setup>
import { ArrowUpRight } from '@lucide/vue'
import { computed } from 'vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { AGENTS } from '@/domain/team.js'
import { useOfficeStore } from '@/stores/office'

// The roster beside the office. In the conversational view the conversation has its own column, so the log is left out.
defineProps({ withLog: { type: Boolean, default: true } })

const office = useOfficeStore()
const agentLabel = computed(() => office.agentName(office.focusAgent || 'amang'))
</script>

<template>
  <AppCard class="team-card" aria-label="Tim agen" title="Tim agen">
    <template #actions>
      <AppTag tone="primary">{{ AGENTS.length }}</AppTag>
    </template>
    <AppAlert v-if="office.team.simulated" role="note" tone="warning" class="sim-note">
      Mode demo aktif — status di bawah adalah simulasi.
    </AppAlert>
    <ul class="team-list">
      <li v-for="a in AGENTS" :key="a.id">
        <button
          type="button"
          :class="{ focused: office.focusAgent === a.id }"
          :data-status="office.team.agents[a.id].status"
          :aria-label="`${a.name}, ${a.role}: ${office.team.agents[a.id].label}`"
          @click="office.openAgent(a.id)"
        >
          <span class="swatch" :style="{ background: a.color }" />
          <span class="who">
            <strong>{{ a.name }}</strong>
            <small>{{ a.role }}</small>
          </span>
          <span class="state">
            {{ office.statusText[office.team.agents[a.id].status] || office.team.agents[a.id].status }}
          </span>
        </button>
      </li>
    </ul>
    <div class="status-label">
      <i class="status-dot" :class="{ online: !!office.team.activeAgent }" />{{ office.team.label }}
    </div>
    <section v-if="withLog" class="team-log-wrap">
      <h3 class="section-title">Komunikasi tim</h3>
      <ol class="team-log" aria-live="polite">
        <li v-for="h in office.teamLog" :key="h.key">
          <b>{{ office.agentName(h.from) }} → {{ office.agentName(h.to) }}</b
          >{{ h.say }}
          <small>
            {{ office.localTime(h.at) }}{{ h.iteration ? ` · iterasi ${h.iteration}` : ''
            }}{{ office.team.simulated ? ' · simulasi' : '' }}
          </small>
        </li>
      </ol>
      <p v-if="!office.teamLog.length" class="muted small mb-0">
        Belum ada serah-terima tercatat untuk task ini.
      </p>
    </section>
    <div class="team-actions">
      <AppButton variant="secondary" size="sm" @click="office.go('command')">
        Beri tim tugas <ArrowUpRight :size="14" />
      </AppButton>
      <AppButton variant="secondary" size="sm" @click="office.showDocuments(office.focusAgent)">
        Artifacts {{ agentLabel }} <ArrowUpRight :size="14" />
      </AppButton>
      <AppButton
        v-if="!office.demo && office.stage.task?.status === 'queued'"
        size="sm"
        :disabled="!office.can('run') || !office.cap?.model_configured"
        @click="office.ask('run', office.stage.task.task_id)"
      >
        Jalankan task
      </AppButton>
    </div>
    <small class="faint team-note">Model: {{ office.model }}</small>
  </AppCard>
</template>
