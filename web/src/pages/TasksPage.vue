<script setup>
import DesignEditsNotice from '@/components/DesignEditsNotice.vue'
import { ArrowUpRight } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import ConversationThread from '@/components/ConversationThread.vue'
import LeadQuestions from '@/components/LeadQuestions.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import StatusChip from '@/components/StatusChip.vue'
import TaskTable from '@/components/TaskTable.vue'
import TaskUsage from '@/components/TaskUsage.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppTag from '@/components/ui/AppTag.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import {
  attemptText,
  CI_TEXT,
  errorText,
  failureText,
  fileSize,
  openQuestions,
  PACKAGE_TEXT,
  packageNote,
  PHASE_TEXT,
  phaseHistory,
  STOP_TEXT,
  WAIT_TEXT,
  withoutOpenQuestions,
} from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const canRetry = computed(
  () => office.selected?.status === 'failed' || (office.stopped && !office.clarificationPending),
)
const stop = computed(() => (office.stopped ? STOP_TEXT[office.selected.execution.wait_reason] : null))
const questions = computed(() => (office.clarificationPending ? openQuestions(office.taskMessages) : []))
const thread = computed(() =>
  questions.value.length ? withoutOpenQuestions(office.taskMessages) : office.taskMessages,
)
const division = computed(() => office.documents.find(d => d.kind === 'work_packages'))
const canRedivide = computed(
  () => office.designPending && division.value && division.value.status !== 'generated',
)
const failureCause = computed(() => failureText(office.selected?.last_error?.details))
const detail = ref(null)
const ci = computed(() => office.selected?.ci)
const ciTone = { watching: 'info', success: 'success', failed: 'error', timeout: 'warning' }
const packageTone = {
  passed: 'success',
  running: 'primary',
  waiting: 'warning',
  blocked: 'warning',
  failed: 'error',
}
const jobTone = j => (j.conclusion === 'success' ? 'success' : j.conclusion ? 'error' : 'info')
const artifacts = computed(() =>
  (ci.value?.runs || []).flatMap(r => r.artifacts.filter(a => !a.expired).map(a => ({ ...a, url: r.url }))),
)
const jobs = computed(() =>
  (ci.value?.runs || []).flatMap(r => r.jobs.map(j => ({ ...j, run: r.name, url: r.url }))),
)
const packageLine = p =>
  [
    [packageNote(p), p.owns.join(', ')].filter(Boolean).join(' · '),
    p.files ? `${p.files} file` : '',
    p.state === 'running' && p.phase ? PHASE_TEXT[p.phase] || p.phase : '',
  ]
    .filter(Boolean)
    .join(' · ')

const reviseFromCi = () => {
  const failed = jobs.value
    .filter(j => j.conclusion && j.conclusion !== 'success' && j.conclusion !== 'skipped')
    .map(j => j.name)

  office.startRevision(
    `Perbaiki kegagalan CI pada job: ${failed.join(', ') || 'lihat log'}.\n\nLog terakhir:\n${(ci.value.log || '').slice(-2500)}`,
  )
}

// The detail opens below the list: without this, Detail looks as if it did nothing.
watch(
  () => office.detailRequest,
  async () => {
    await nextTick()

    const card = detail.value?.$el

    card?.scrollIntoView({ behavior: office.motion ? 'smooth' : 'auto', block: 'start' })
    card?.focus({ preventScroll: true })
  },
)
</script>

<template>
  <TaskTable />

  <AppCard
    v-if="office.selected"
    ref="detail"
    tabindex="-1"
    class="mt task-detail"
    aria-label="Detail task"
    :title="office.selected.title || 'Detail task'"
  >
    <template #actions>
      <StatusChip :status="office.selected.status" />
    </template>

    <MarkdownText :text="office.selected.description" />
    <p class="muted">{{ office.status.label }}</p>

    <dl v-if="office.selected.branch" class="facts">
      <div>
        <dt>Branch</dt>
        <dd>
          <code>{{ office.selected.branch.branch }}</code>
        </dd>
      </div>
      <div v-if="office.selected.branch.revises_task_id">
        <dt>Revisi dari</dt>
        <dd>{{ office.selected.branch.revises_task_id }}</dd>
      </div>
      <div>
        <dt>Git</dt>
        <dd>
          <template
            v-if="office.selected.branch.publication === 'published' && !office.selected.branch.pr_url"
            >Digabung ke <code>main</code> (lokal) · commit
            <code>{{ office.selected.branch.commit_sha?.slice(0, 12) }}</code></template
          >
          <a
            v-else-if="office.selected.branch.publication === 'published'"
            :href="office.selected.branch.pr_url"
            target="_blank"
            rel="noopener noreferrer"
            >Buka PR #{{ office.selected.branch.pr_number }} ↗</a
          >
          <template v-else-if="office.selected.branch.publication === 'publishing'"
            >Sedang dimasukkan ke Git…</template
          >
          <template v-else-if="office.selected.branch.publication === 'failed'">
            Gagal: {{ office.selected.branch.error }}
            <AppButton
              variant="secondary"
              size="sm"
              :disabled="!office.can('approve')"
              @click="office.retryPublish"
            >
              Coba lagi
            </AppButton>
          </template>
          <template v-else>Belum ada commit</template>
        </dd>
      </div>
    </dl>

    <section v-if="office.selected.packages?.length" class="mt mb" aria-label="Paket kerja">
      <h3 class="section-title">Paket kerja</h3>
      <ul class="plain-list boxed-list">
        <li v-for="p in office.selected.packages" :key="p.key">
          <div style="min-width: 0">
            <strong>{{ p.title }}</strong>
            <small class="block muted">{{ packageLine(p) }}</small>
            <small v-if="p.error" class="block text-error">{{ errorText(p.error) }}</small>
          </div>
          <AppTag :tone="packageTone[p.state]">{{ PACKAGE_TEXT[p.state] || p.state }}</AppTag>
        </li>
      </ul>
    </section>

    <section v-if="ci" class="mt mb" aria-label="CI">
      <h3 class="section-title row">
        CI di GitHub
        <AppTag :tone="ciTone[ci.state]">{{ CI_TEXT[ci.state] || ci.state }}</AppTag>
      </h3>
      <ul v-if="jobs.length" class="plain-list boxed-list">
        <li v-for="j in jobs" :key="`${j.run}/${j.name}`">
          <a :href="j.url" target="_blank" rel="noopener noreferrer">{{ j.run }} / {{ j.name }} ↗</a>
          <AppTag :tone="jobTone(j)">{{ j.conclusion || j.status }}</AppTag>
        </li>
      </ul>
      <template v-if="artifacts.length">
        <p class="mt" style="margin-bottom: 4px; font-weight: 500">Artefak build</p>
        <ul class="plain-list boxed-list">
          <li v-for="a in artifacts" :key="a.name">
            <a :href="a.url" target="_blank" rel="noopener noreferrer">{{ a.name }} ↗</a>
            <small class="muted">{{ fileSize(a.size) }}</small>
          </li>
        </ul>
      </template>
      <template v-if="ci.state === 'failed'">
        <pre v-if="ci.log" class="code-block mt">{{ ci.log }}</pre>
        <AppButton class="mt" variant="secondary" :disabled="!office.can('submit')" @click="reviseFromCi">
          Buat task revisi dari kegagalan ini
        </AppButton>
      </template>
    </section>

    <ConversationThread v-if="office.taskMessages.length" :messages="thread" aria-label="Percakapan task" />

    <AppAlert
      v-if="office.selected.status === 'failed'"
      role="status"
      tone="error"
      class="mt mb"
      title="Task gagal"
    >
      {{ failureCause }} Antrekan ulang untuk mencoba dari awal.
    </AppAlert>

    <AppAlert v-if="stop" role="status" tone="warning" class="mt mb" :title="stop.title">{{
      stop.body
    }}</AppAlert>

    <AppAlert v-if="office.taskError" tone="error" class="mt mb" title="Belum terkirim">
      {{ errorText(office.taskError) }}
    </AppAlert>

    <LeadQuestions
      v-if="questions.length"
      :key="office.taskMessages.length"
      class="mt mb"
      :questions="questions"
      :disabled="!office.can('run')"
      submit-label="Kirim jawaban"
      @answer="office.sendTaskMessage"
    />
    <form v-else-if="office.stopped" class="stack mt mb" @submit.prevent="office.sendTaskMessage">
      <AppTextarea
        v-model="office.taskDraft"
        :label="office.clarificationPending ? 'Jawaban untuk Amang' : 'Arahan untuk tim (opsional)'"
        rows="3"
        maxlength="4000"
      />
      <div class="row">
        <AppButton type="submit" :disabled="!office.can('run') || !office.taskDraft.trim()">
          {{ office.busy ? 'Mengirim…' : 'Kirim dan lanjutkan' }}
        </AppButton>
        <AppButton
          v-if="!office.clarificationPending"
          variant="secondary"
          :disabled="!office.can('run')"
          @click="office.sendTaskMessage('Lanjutkan pekerjaan tanpa arahan tambahan.')"
        >
          Lanjutkan tanpa arahan
        </AppButton>
      </div>
    </form>

    <DesignEditsNotice class="mt mb" />
    <AppAlert
      v-if="office.designPending"
      role="status"
      tone="warning"
      class="mt mb"
      title="Desain menunggu persetujuan Anda"
    >
      <p>
        Periksa PRD, SRS, dan dokumen lainnya, dan edit diagramnya bila perlu. Coding baru dimulai setelah
        disetujui.
      </p>
      <div class="row">
        <AppButton variant="secondary" @click="office.showDocuments('analyst')">
          Baca atau edit desain <ArrowUpRight :size="14" />
        </AppButton>
        <AppButton
          :disabled="!office.can('approve')"
          @click="office.ask('design-approve', office.selectedId)"
        >
          Setujui desain
        </AppButton>
        <AppButton
          variant="danger"
          :disabled="!office.can('approve')"
          @click="office.ask('design-reject', office.selectedId)"
        >
          Tolak
        </AppButton>
      </div>
    </AppAlert>

    <div class="row mt mb">
      <AppButton
        v-if="office.selected.status === 'queued'"
        :disabled="!office.can('run') || !office.cap?.model_configured"
        @click="office.ask('run', office.selectedId)"
      >
        Jalankan proposal
      </AppButton>
      <AppButton
        v-if="['queued', 'running', 'waiting'].includes(office.selected.status)"
        variant="danger"
        :disabled="!office.can('cancel')"
        @click="office.ask('cancel', office.selectedId)"
      >
        Batalkan
      </AppButton>
      <AppButton
        v-if="canRetry"
        variant="secondary"
        :disabled="!office.can('retry')"
        @click="office.ask('retry', office.selectedId)"
      >
        Antrekan ulang
      </AppButton>
      <AppButton variant="secondary" @click="office.go('reports')">Lihat laporan</AppButton>
      <AppButton variant="secondary" @click="office.openTaskDocuments">Lihat dokumen</AppButton>
    </div>

    <AppAlert
      v-if="canRedivide"
      role="status"
      :tone="division.status === 'failed' ? 'warning' : 'info'"
      class="mb"
      title="Pekerjaan belum dibagi"
    >
      <p>{{ division.reason }}</p>
      <AppButton
        variant="secondary"
        size="sm"
        :disabled="!office.can('run') || office.busy"
        @click="office.redivide()"
      >
        Bagi ulang
      </AppButton>
    </AppAlert>

    <TaskUsage />

    <h3 class="section-title">Riwayat tahap</h3>
    <ol v-if="office.events.length" class="timeline">
      <li v-for="e in phaseHistory(office.events)" :key="e.position">
        <div style="font-weight: 500">{{ PHASE_TEXT[e.phase] || e.phase }}</div>
        <small class="faint">
          {{ office.localTime(e.timestamp) }} · {{ WAIT_TEXT[e.wait_reason] || e.wait_reason || e.state }}
        </small>
        <div v-if="attemptText(e)" class="small text-warning">{{ attemptText(e) }}</div>
      </li>
    </ol>
    <p v-else class="muted mb-0">Belum ada tahap eksekusi tercatat.</p>
  </AppCard>
</template>
