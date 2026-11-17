<script setup>
import { Box, MessagesSquare, Plus } from '@lucide/vue'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import NewProjectDialog from '@/components/NewProjectDialog.vue'
import TaskPicker from '@/components/TaskPicker.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import { errorText } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const route = useRoute()
const isOffice = computed(() => route.name === 'office')
const isProjects = computed(() => ['projects', 'project-detail'].includes(route.name))
// Pages that show one task's work pick it beside the project, in the header where the action button would be.
const withTask = computed(() => ['documents', 'reports'].includes(route.name))
// Settings are global (siamang.toml and this browser), so no project is picked there.
const projectFilter = computed(() => !isProjects.value && !isOffice.value && route.name !== 'settings')
const newTask = computed(() => !['command', 'settings'].includes(route.name) && !withTask.value)
const projectItems = computed(() =>
  office.projects.length
    ? office.projects.map(p => ({ title: p.name, value: p.project_id }))
    : [{ title: 'Belum ada proyek', value: '' }],
)
</script>

<template>
  <div class="page-header row-between">
    <div>
      <h1 class="page-title">{{ isOffice ? 'Selamat datang di kantor.' : route.meta.title }}</h1>
      <p class="page-subtitle">
        {{
          isOffice
            ? 'Amang memegang semua proyek. Klik agen untuk melihat apa yang sedang dikerjakan.'
            : route.meta.subtitle || 'Data dan tindakan bersumber dari SIAMANG lokal.'
        }}
      </p>
    </div>
    <div v-if="withTask" class="page-header-pickers">
      <div class="project-select">
        <AppSelect
          label="Project"
          :model-value="office.projectId"
          :items="projectItems"
          :disabled="!office.projects.length"
          @update:model-value="office.pickProject"
        />
      </div>
      <div class="project-select task-select">
        <TaskPicker />
      </div>
    </div>
    <div v-else class="row">
      <div v-if="isOffice" class="segmented" role="group" aria-label="Tampilan dashboard">
        <button type="button" :aria-pressed="office.scene3d" @click="office.scene3d = true">
          <Box :size="15" aria-hidden="true" /> 3D
        </button>
        <button type="button" :aria-pressed="!office.scene3d" @click="office.scene3d = false">
          <MessagesSquare :size="15" aria-hidden="true" /> Percakapan
        </button>
      </div>
      <AppButton v-if="isProjects" @click="office.openProjectDialog"
        ><Plus :size="16" /> Proyek baru</AppButton
      >
      <AppButton v-else-if="newTask" @click="office.go('command')"> <Plus :size="16" /> Task baru </AppButton>
    </div>
  </div>

  <!-- While a confirmation is open the page behind it is inert, so ConfirmDialog shows the error itself. -->
  <AppAlert v-if="office.error && !office.confirm" tone="error" class="mb">
    {{ errorText(office.error) }} ·
    {{ office.connected ? 'Aksi belum berhasil.' : 'Data tersimpan dapat kedaluwarsa.' }}
    <template #actions>
      <AppButton variant="ghost" size="sm" @click="office.refresh">Coba lagi</AppButton>
    </template>
  </AppAlert>
  <AppAlert v-if="office.backendOutdated" tone="warning" class="mb" title="Backend masih versi lama">
    Halaman ini sudah baru, tetapi server belum di-restart, jadi sebagian tombol akan ditolak. Di terminal
    tempat server berjalan: tekan Ctrl+C, lalu jalankan <code>npm run dev</code>.
  </AppAlert>
  <AppAlert
    v-if="office.notice"
    role="status"
    tone="success"
    class="mb"
    close-label="Tutup notifikasi"
    @close="office.notice = ''"
  >
    {{ office.notice }}
  </AppAlert>

  <NewProjectDialog />

  <div v-if="projectFilter && !withTask" class="row-between mb page-filters">
    <div class="page-filters-fields">
      <div class="project-select">
        <AppSelect
          label="Project"
          :model-value="office.projectId"
          :items="projectItems"
          :disabled="!office.projects.length"
          @update:model-value="office.pickProject"
        />
      </div>
    </div>
    <span class="muted small">
      Terakhir diperbarui {{ office.localTime(office.updated) }}
      {{ !office.connected && office.updated ? '· stale' : '' }}
    </span>
  </div>
</template>
