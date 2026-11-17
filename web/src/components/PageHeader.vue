<script setup>
import { Plus } from '@lucide/vue'
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
// Pages that show one task's work pick it beside the project.
const withTask = computed(() => ['documents', 'reports'].includes(route.name))
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
    <AppButton v-if="isProjects" @click="office.openProjectDialog"><Plus :size="16" /> Proyek baru</AppButton>
    <AppButton v-else-if="route.name !== 'command'" @click="office.go('command')">
      <Plus :size="16" /> Task baru
    </AppButton>
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

  <div v-if="!isProjects && !isOffice" class="row-between mb page-filters">
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
      <div v-if="withTask" class="project-select task-select">
        <TaskPicker />
      </div>
    </div>
    <span class="muted small">
      Terakhir diperbarui {{ office.localTime(office.updated) }}
      {{ !office.connected && office.updated ? '· stale' : '' }}
    </span>
  </div>
</template>
