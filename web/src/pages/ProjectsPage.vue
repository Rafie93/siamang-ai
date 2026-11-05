<script setup>
import { Folder, FolderGit2, Plus } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import StatusChip from '@/components/StatusChip.vue'
import AppButton from '@/components/ui/AppButton.vue'
import { errorText, projectStep } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const total = p => Object.values(p.task_counts).reduce((a, b) => a + b, 0)
const waiting = p => Object.values(p.waiting).reduce((a, b) => a + b, 0)
const chipStatus = step =>
  step.error ? 'failed' : step.action ? 'waiting' : step.step === 4 ? 'done' : 'running'
</script>

<template>
  <section v-if="!office.portfolio.length && !office.loading" class="card project-empty">
    <span class="icon-box" style="width: 56px; height: 56px"
      ><FolderGit2 :size="28" aria-hidden="true"
    /></span>
    <h2 class="page-title mt" style="font-size: 17px">Belum ada proyek</h2>
    <p class="muted">
      Tautkan repo GitHub pertama Anda. Amang membaca isinya, menanyakan kebutuhan Anda, lalu tim bekerja
      lewat branch dan PR.
    </p>
    <AppButton @click="office.projectDialog = true"><Plus :size="16" /> Proyek baru</AppButton>
  </section>

  <div v-else class="grid grid-2" aria-label="Daftar proyek">
    <RouterLink
      v-for="p in office.portfolio"
      :key="p.project_id"
      class="card project-card card-body"
      :to="{ name: 'project-detail', params: { id: p.project_id } }"
    >
      <div class="project-head mb">
        <span class="icon-box">
          <FolderGit2 v-if="p.repository" :size="18" aria-hidden="true" />
          <Folder v-else :size="18" aria-hidden="true" />
        </span>
        <div>
          <h3 class="card-title">{{ p.name }}</h3>
          <p class="card-subtitle truncate">
            {{ p.repository?.repository || p.description || 'Proyek lokal' }}
          </p>
        </div>
      </div>
      <StatusChip :status="chipStatus(projectStep(p))" :text="projectStep(p).text" />
      <p v-if="projectStep(p).error" class="small text-error mt mb-0">
        {{ errorText(projectStep(p).error) }}
      </p>
      <dl class="project-stats">
        <div>
          <dd>{{ total(p) }}</dd>
          <dt>Task</dt>
        </div>
        <div>
          <dd>{{ p.task_counts.running || 0 }}</dd>
          <dt>Berjalan</dt>
        </div>
        <div>
          <dd :class="{ 'text-warning': waiting(p) }">{{ waiting(p) }}</dd>
          <dt>Menunggu Anda</dt>
        </div>
        <div>
          <dd>{{ p.features.length }}</dd>
          <dt>Fitur</dt>
        </div>
      </dl>
    </RouterLink>
  </div>
</template>
