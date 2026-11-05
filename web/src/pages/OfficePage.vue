<script setup>
import { ArrowUpRight, Bell, CircleCheckBig, FolderGit2, ListChecks } from '@lucide/vue'
import { computed } from 'vue'
import AgentDetailDialog from '@/components/AgentDetailDialog.vue'
import OfficeScene from '@/components/OfficeScene.vue'
import ProjectBoard from '@/components/ProjectBoard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { AGENTS } from '@/domain/team.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const agentLabel = computed(() => office.agentName(office.focusAgent || 'amang'))
const stats = computed(() => [
  { title: 'Proyek', value: office.feed.length, note: 'Semua dipegang Amang', icon: FolderGit2 },
  {
    title: 'Task',
    value: office.taskCount,
    extra: `/ ${office.runningCount} berjalan`,
    note: 'Dari semua proyek',
    icon: ListChecks,
    tone: 'info',
  },
  {
    title: 'Menunggu Anda',
    value: office.needsOwner.length,
    note: 'Jawaban atau persetujuan',
    icon: Bell,
    tone: 'warning',
  },
  {
    title: 'Hasil siap disetujui',
    value: office.approvalCount,
    note: 'Menjadi commit dan PR',
    icon: CircleCheckBig,
    tone: 'success',
  },
])
</script>

<template>
  <div class="office-grid">
    <OfficeScene
      :team="office.team"
      :motion="office.motion"
      :quality="office.quality"
      :focus="office.focusAgent"
      :bubble="office.agentBubble"
      :subject="office.stageSubject"
      @select="office.selectCharacter"
      @toggle-demo="office.toggleDemo"
    />
    <AppCard class="team-card" aria-label="Tim agen" title="Tim agen">
      <template #actions>
        <AppTag tone="primary">{{ AGENTS.length }}</AppTag>
      </template>
      <div class="stack" style="gap: 12px">
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
        <div>
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
        </div>
        <div class="row">
          <AppButton variant="secondary" size="sm" style="flex: 1" @click="office.go('command')">
            Beri tim tugas <ArrowUpRight :size="14" />
          </AppButton>
          <AppButton
            variant="secondary"
            size="sm"
            style="flex: 1"
            @click="office.showDocuments(office.focusAgent)"
          >
            Dokumen {{ agentLabel }} <ArrowUpRight :size="14" />
          </AppButton>
          <AppButton
            v-if="!office.demo && office.stage.task?.status === 'queued'"
            size="sm"
            style="flex: 1"
            :disabled="!office.can('run') || !office.cap?.model_configured"
            @click="office.ask('run', office.stage.task.task_id)"
          >
            Jalankan task
          </AppButton>
        </div>
        <small class="faint">
          Model: {{ office.model }} · animasi kerja hanya tampil saat backend melaporkan aktivitas segar.
        </small>
      </div>
    </AppCard>
  </div>

  <div class="grid grid-4" style="margin: 16px 0 24px">
    <AppCard v-for="s in stats" :key="s.title">
      <div class="row" style="flex-wrap: nowrap; gap: 12px">
        <span class="icon-box" :data-tone="s.tone"
          ><component :is="s.icon" :size="18" aria-hidden="true"
        /></span>
        <div style="min-width: 0">
          <div class="muted small">{{ s.title }}</div>
          <div class="truncate" style="font-size: 18px; font-weight: 600; line-height: 1.3">
            {{ s.value }}
            <small v-if="s.extra" class="muted small" style="font-weight: 400">{{ s.extra }}</small>
          </div>
          <small class="faint">{{ s.note }}</small>
        </div>
      </div>
    </AppCard>
  </div>

  <ProjectBoard />
  <AgentDetailDialog />
  <div class="visually-hidden" role="status">
    {{ office.agentBubble ? `${office.agentBubble.title}: ${office.agentBubble.lines.join('. ')}` : '' }}
  </div>
</template>
