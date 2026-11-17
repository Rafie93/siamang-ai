<script setup>
import { ArrowLeft, ArrowUpRight, Check, Folder, FolderGit2, Plus } from '@lucide/vue'
import { computed, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import ConversationThread from '@/components/ConversationThread.vue'
import LeadQuestions from '@/components/LeadQuestions.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import ProjectUsage from '@/components/ProjectUsage.vue'
import StatusChip from '@/components/StatusChip.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppTabs from '@/components/ui/AppTabs.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import { errorText, openQuestions, projectStep, projectSteps, withoutOpenQuestions } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const route = useRoute()
const id = computed(() => route.params.id)
const project = computed(() => office.portfolio.find(p => p.project_id === id.value) || null)
const intake = computed(() => (office.projectId === id.value ? office.intake : null))
const step = computed(() => projectStep(project.value))
const local = computed(() => project.value?.repository?.kind === 'local')
const state = computed(() => intake.value?.spec?.state)
const messages = computed(() => intake.value?.messages || [])
const questions = computed(() => (state.value === 'gathering' ? openQuestions(messages.value) : []))
const thinking = computed(() => state.value === 'thinking' || intake.value?.repository?.state === 'cloning')
const failure = computed(() => {
  if (intake.value?.repository?.state === 'failed') return intake.value.repository.error
  return state.value === 'failed' ? intake.value?.spec?.error : null
})
const tab = ref(null)
const chosen = computed(
  () => tab.value || (['pending_approval', 'approved'].includes(state.value) ? 'spec' : 'talk'),
)
const tabs = computed(() => [
  { value: 'talk', label: 'Percakapan', badge: questions.value.length || null },
  { value: 'spec', label: 'Spesifikasi' },
  { value: 'tasks', label: `Task (${office.projectId === id.value ? office.tasks.length : 0})` },
  { value: 'features', label: `Fitur (${project.value?.features.length ?? 0})` },
  { value: 'usage', label: 'Pemakaian' },
])

const stepState = i => {
  if (i < step.value.step || step.value.step === 4) return 'done'
  if (i !== step.value.step) return 'todo'
  return step.value.action ? 'action' : 'active'
}

watchEffect(() => {
  if (id.value && office.projectId !== id.value && project.value) office.pickProject(id.value)
})

// The next thing to do is on screen: the tab follows the conversation until the owner picks one.
watch(state, () => {
  tab.value = null
})

const openTask = task => {
  office.choose(task)
  office.go('tasks')
}

const send = async text => {
  office.intakeDraft = text
  await office.sendIntake()
}
</script>

<template>
  <div v-if="!project" class="muted">
    <p>{{ office.loading ? 'Memuat proyek…' : 'Proyek tidak ditemukan.' }}</p>
    <AppButton variant="secondary" :to="{ name: 'projects' }"
      ><ArrowLeft :size="14" /> Kembali ke daftar proyek</AppButton
    >
  </div>

  <template v-else>
    <AppButton variant="ghost" size="sm" class="mb" :to="{ name: 'projects' }">
      <ArrowLeft :size="14" /> Semua proyek
    </AppButton>

    <AppCard class="mb">
      <template #head>
        <div class="project-head">
          <span class="icon-box">
            <FolderGit2 v-if="project.repository" :size="18" aria-hidden="true" />
            <Folder v-else :size="18" aria-hidden="true" />
          </span>
          <div>
            <h2 class="page-title" style="font-size: 17px">{{ project.name }}</h2>
            <p v-if="local" class="card-subtitle">
              Proyek lokal, tanpa GitHub · folder <code>workspaces/{{ project.project_id }}</code>
              <template v-if="project.repository.default_branch">
                · branch utama <code>{{ project.repository.default_branch }}</code>
              </template>
            </p>
            <p v-else-if="project.repository" class="card-subtitle">
              <a
                :href="`https://github.com/${project.repository.repository}`"
                target="_blank"
                rel="noopener noreferrer"
                >{{ project.repository.repository }} ↗</a
              >
              <template v-if="project.repository.default_branch">
                · branch utama <code>{{ project.repository.default_branch }}</code>
              </template>
            </p>
          </div>
        </div>
      </template>
      <template #actions>
        <AppButton :disabled="!office.specApproved" @click="office.go('command')"
          ><Plus :size="16" /> Task baru</AppButton
        >
      </template>
      <ol v-if="project.repository" class="project-steps" aria-label="Tahap penyiapan proyek">
        <li
          v-for="(name, i) in projectSteps(project)"
          :key="name"
          :data-state="stepState(i)"
          :aria-current="i === step.step ? 'step' : undefined"
        >
          <span class="project-step-dot">
            <Check v-if="stepState(i) === 'done'" :size="14" aria-hidden="true" />
            <template v-else>{{ i + 1 }}</template>
          </span>
          {{ name }}
        </li>
      </ol>
    </AppCard>

    <AppAlert v-if="failure" tone="error" class="mb" title="Penyiapan terhenti">
      {{ errorText(failure) }}
      <template v-if="intake?.repository?.state === 'failed'" #actions>
        <AppButton variant="danger" size="sm" @click="office.retryClone">{{
          local ? 'Coba siapkan lagi' : 'Coba clone lagi'
        }}</AppButton>
      </template>
    </AppAlert>

    <AppAlert v-if="thinking" role="status" tone="info" class="mb">
      <span class="row">
        <span class="spinner" aria-hidden="true" />
        {{ step.text }} Halaman ini diperbarui sendiri, jadi Anda bisa menunggu di sini.
      </span>
    </AppAlert>

    <section v-if="project.repository" class="card">
      <AppTabs :model-value="chosen" :tabs="tabs" @update:model-value="tab = $event" />

      <div v-if="chosen === 'talk'" class="card-body">
        <p v-if="!messages.length && !thinking" class="muted">Belum ada percakapan.</p>
        <ConversationThread :messages="withoutOpenQuestions(messages)" aria-live="polite" />
        <LeadQuestions
          v-if="questions.length"
          :key="messages.length"
          class="mt"
          :questions="questions"
          :disabled="!office.can('submit')"
          submit-label="Kirim ke Amang"
          @answer="send"
        />
        <form
          v-else-if="intake?.repository?.state === 'ready' && state !== 'thinking' && state !== 'approved'"
          class="stack mt"
          @submit.prevent="office.sendIntake"
        >
          <AppTextarea
            v-model="office.intakeDraft"
            :label="state === 'failed' ? 'Kirim lagi ke Amang' : 'Ada yang perlu diubah?'"
            rows="3"
            maxlength="4000"
          />
          <div>
            <AppButton
              type="submit"
              variant="secondary"
              :disabled="!office.can('submit') || !office.intakeDraft.trim()"
            >
              Kirim ke Amang
            </AppButton>
          </div>
        </form>
      </div>

      <div v-else-if="chosen === 'spec'" class="card-body">
        <AppAlert
          v-if="state === 'pending_approval'"
          role="status"
          tone="warning"
          class="mb"
          title="Periksa spesifikasi ini"
        >
          <p>
            Task baru bisa dibuat setelah Anda menyetujuinya. Kalau ada yang kurang tepat, koreksi lewat tab
            Percakapan.
          </p>
          <div class="row">
            <AppButton :disabled="!office.can('approve')" @click="office.approveSpec"
              >Setujui spesifikasi</AppButton
            >
            <AppButton variant="secondary" @click="tab = 'talk'">Minta perubahan</AppButton>
          </div>
        </AppAlert>
        <MarkdownText v-if="intake?.spec?.content" :text="intake.spec.content" />
        <p v-else class="muted mb-0">
          Spesifikasi belum ada. Amang menulisnya setelah pertanyaannya terjawab.
        </p>
      </div>

      <div v-else-if="chosen === 'tasks'" class="card-body">
        <p v-if="!office.tasks.length" class="muted mb-0">Belum ada task di proyek ini.</p>
        <ul v-else class="plain-list">
          <li v-for="t in office.tasks" :key="t.task_id">
            <button type="button" class="link-row" @click="openTask(t)">
              <span style="min-width: 0">
                <span class="block truncate">{{ t.title || t.description }}</span>
                <small class="muted">{{ office.visualStatus(t, office.connected).label }}</small>
              </span>
              <StatusChip :status="t.status" />
            </button>
          </li>
        </ul>
      </div>

      <div v-else-if="chosen === 'usage'" class="card-body">
        <ProjectUsage :project-id="project.project_id" />
      </div>

      <div v-else class="card-body">
        <ul v-if="project.features.length" class="plain-list mb">
          <li v-for="f in project.features" :key="f.feature_id">
            {{ f.title }} <small class="faint">{{ f.task_count }} task</small>
          </li>
        </ul>
        <form class="row" style="align-items: flex-end" @submit.prevent="office.createFeature">
          <div style="flex: 1; min-width: 200px">
            <AppInput
              v-model="office.featureTitle"
              label="Fitur baru"
              maxlength="200"
              placeholder="Nama fitur"
            />
          </div>
          <AppButton
            type="submit"
            variant="secondary"
            :disabled="!office.can('submit') || !office.featureTitle.trim()"
          >
            Tambah
          </AppButton>
        </form>
      </div>
    </section>

    <template v-else>
      <AppCard class="mb">
        <p>{{ project.description || 'Proyek lokal tanpa repo GitHub.' }}</p>
        <AppButton variant="secondary" @click="office.openProjectTasks(project)">
          Buka task <ArrowUpRight :size="14" />
        </AppButton>
      </AppCard>
      <AppCard title="Pemakaian token">
        <ProjectUsage :project-id="project.project_id" />
      </AppCard>
    </template>
  </template>
</template>
