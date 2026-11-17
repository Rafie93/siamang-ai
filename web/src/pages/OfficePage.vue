<script setup>
import { Bell, CircleCheckBig, FolderGit2, ListChecks } from '@lucide/vue'
import { computed } from 'vue'
import AgentDetailDialog from '@/components/AgentDetailDialog.vue'
import OfficeScene from '@/components/OfficeScene.vue'
import ProjectBoard from '@/components/ProjectBoard.vue'
import TeamConversation from '@/components/TeamConversation.vue'
import TeamPanel from '@/components/TeamPanel.vue'
import AppCard from '@/components/ui/AppCard.vue'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
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
  <div class="grid grid-4 office-stats">
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

  <div class="office-grid" :class="{ conversational: !office.scene3d }">
    <OfficeScene
      v-if="office.scene3d"
      :team="office.team"
      :motion="office.motion"
      :quality="office.quality"
      :focus="office.focusAgent"
      :bubble="office.agentBubble"
      :subject="office.stageSubject"
      @select="office.selectCharacter"
      @toggle-demo="office.toggleDemo"
    />
    <TeamConversation v-else />
    <TeamPanel :with-log="office.scene3d" />
  </div>

  <ProjectBoard />
  <AgentDetailDialog />
  <div class="visually-hidden" role="status">
    {{ office.agentBubble ? `${office.agentBubble.title}: ${office.agentBubble.lines.join('. ')}` : '' }}
  </div>
</template>
