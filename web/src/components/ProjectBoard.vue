<script setup>
import { ArrowUpRight, Eye, Folder, FolderGit2 } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import StatusChip from '@/components/StatusChip.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import { projectStep } from '@/domain/labels.js'
import { taskWait } from '@/domain/office.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const line = t => taskWait(t) || office.visualStatus(t, office.connected).label
</script>

<template>
  <div class="row-between mb">
    <h2 class="page-title" style="font-size: 16px">Papan proyek Amang</h2>
    <AppButton v-if="office.stagePin" variant="secondary" size="sm" @click="office.watchTask('')">
      <Eye :size="14" /> Ikuti task yang berjalan
    </AppButton>
  </div>
  <AppCard v-if="!office.feed.length">
    <span class="muted">Belum ada proyek. Buka Projects untuk menautkan repo GitHub.</span>
  </AppCard>
  <div v-else class="grid grid-2">
    <AppCard v-for="p in office.feed" :key="p.project_id">
      <template #head>
        <div class="project-head">
          <span class="icon-box">
            <FolderGit2 v-if="p.repository" :size="18" aria-hidden="true" />
            <Folder v-else :size="18" aria-hidden="true" />
          </span>
          <div>
            <RouterLink
              class="board-project card-title"
              :to="{ name: 'project-detail', params: { id: p.project_id } }"
            >
              {{ p.name }}
            </RouterLink>
            <p class="card-subtitle">{{ projectStep(p).text }}</p>
          </div>
        </div>
      </template>
      <p v-if="!p.tasks.length" class="muted small mb-0">Tidak ada task aktif.</p>
      <ul v-else class="board-tasks">
        <li v-for="t in p.tasks" :key="t.task_id" :class="{ 'board-on-stage': office.stageId === t.task_id }">
          <button
            type="button"
            class="board-task"
            :aria-pressed="office.stageId === t.task_id"
            title="Tampilkan di kantor"
            @click="office.watchTask(t.task_id)"
          >
            <span class="truncate">{{ t.title || t.description }}</span>
            <small>{{ line(t) }}</small>
          </button>
          <StatusChip :status="t.status" />
          <button
            type="button"
            class="icon-btn"
            :aria-label="`Buka detail ${t.title}`"
            @click="office.openTask(p.project_id, t.task_id)"
          >
            <ArrowUpRight :size="16" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </AppCard>
  </div>
</template>
