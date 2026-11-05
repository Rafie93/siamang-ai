<script setup>
import { ArrowUpRight, Play, Send } from '@lucide/vue'
import { computed, ref } from 'vue'
import ConversationThread from '@/components/ConversationThread.vue'
import LeadQuestions from '@/components/LeadQuestions.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import { errorText, openQuestions, withoutOpenQuestions } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const PRIORITIES = ['low', 'normal', 'high', 'urgent']
const reply = ref('')

const kindItems = computed(() => [
  { title: 'Task baru (branch baru)', value: '' },
  ...office.revisable.map(t => ({
    title: `Revisi: ${t.title || t.description.slice(0, 50)}`,
    value: t.task_id,
  })),
])

const featureItems = computed(() => [
  { title: 'Tanpa fitur', value: '' },
  ...office.features.map(f => ({ title: f.title, value: f.feature_id })),
])

const draft = computed(() => office.draft)
const questions = computed(() =>
  draft.value?.state === 'need_answers' ? openQuestions(draft.value.messages) : [],
)
const canSend = computed(
  () => office.can('submit') && !!office.projectId && !!office.description.trim() && office.specApproved,
)
const subtitle = computed(() =>
  office.leadAvailable
    ? 'Amang bertanya dulu kalau ada yang belum jelas, dan task baru dibuat setelah Anda setuju.'
    : 'Lead belum diberi model, jadi task langsung dibuat dari instruksi Anda.',
)

const sendReply = async () => {
  await office.sendDraft(reply.value)
  reply.value = ''
}
</script>

<template>
  <AppCard
    class="command-card"
    :title="draft ? 'Amang memastikan instruksi Anda' : 'Apa yang ingin Anda kerjakan?'"
    :subtitle="`Proyek ${office.project?.name || '—'}. ${subtitle}`"
  >
    <AppAlert
      v-if="!office.specApproved"
      role="status"
      tone="warning"
      class="mb"
      title="Spesifikasi proyek belum disetujui"
    >
      <p>Selesaikan tanya jawab dengan Amang dulu, baru task bisa dibuat.</p>
      <AppButton variant="secondary" :to="{ name: 'project-detail', params: { id: office.projectId } }">
        Buka percakapan <ArrowUpRight :size="14" />
      </AppButton>
    </AppAlert>

    <template v-if="draft">
      <ConversationThread :messages="withoutOpenQuestions(draft.messages)" aria-live="polite" />
      <AppAlert v-if="draft.state === 'thinking'" role="status" tone="info" class="mb">
        <span class="row"
          ><span class="spinner" aria-hidden="true" /> Amang sedang memahami instruksi Anda…</span
        >
      </AppAlert>
      <AppAlert v-if="draft.state === 'failed'" tone="error" class="mb">{{
        errorText(draft.error)
      }}</AppAlert>

      <LeadQuestions
        v-if="questions.length"
        :key="draft.messages.length"
        class="mb"
        :questions="questions"
        :disabled="!office.can('submit')"
        submit-label="Kirim ke Amang"
        @answer="office.sendDraft"
      />

      <AppCard
        v-if="draft.state === 'ready'"
        inset
        class="mb"
        aria-label="Yang dipahami Amang"
        :title="draft.title"
        subtitle="Yang akan dikerjakan tim"
      >
        <MarkdownText :text="draft.brief" />
        <AppButton class="mt" :disabled="!office.can('submit')" @click="office.confirmDraft">
          Setuju, buat task dan mulai <Play :size="14" />
        </AppButton>
      </AppCard>

      <form v-if="['ready', 'failed'].includes(draft.state)" class="stack" @submit.prevent="sendReply">
        <AppTextarea
          v-model="reply"
          :label="draft.state === 'ready' ? 'Ada yang kurang tepat? Koreksi di sini' : 'Kirim lagi ke Amang'"
          rows="3"
          maxlength="4000"
        />
        <div>
          <AppButton type="submit" variant="secondary" :disabled="!office.can('submit') || !reply.trim()">
            Kirim ke Amang
          </AppButton>
        </div>
      </form>

      <div class="mt">
        <AppButton
          variant="ghost"
          size="sm"
          :disabled="!office.can('submit') || draft.state === 'thinking'"
          @click="office.discardDraft"
        >
          Buang percakapan ini
        </AppButton>
      </div>
    </template>

    <form v-else class="stack" @submit.prevent="office.leadAvailable ? office.sendDraft() : office.submit()">
      <AppSelect
        v-if="office.hasRepository"
        v-model="office.revisesId"
        label="Jenis task"
        :items="kindItems"
      />
      <AppTextarea
        v-model="office.description"
        label="Instruksi"
        maxlength="4000"
        required
        rows="8"
        placeholder="Tulis dengan kata-kata Anda sendiri. Kalau belum lengkap, Amang akan bertanya."
      />
      <div class="grid grid-4" style="align-items: end">
        <AppSelect v-model="office.featureId" label="Fitur" :items="featureItems" />
        <AppSelect v-model="office.priority" label="Prioritas" :items="PRIORITIES" />
        <span class="muted small" style="text-align: end">{{ office.description.length }} / 4000</span>
      </div>
      <div class="row" style="justify-content: flex-end">
        <AppButton variant="secondary" :disabled="!canSend" @click="office.submit">
          {{ office.busy ? 'Mengirim…' : 'Kirim task langsung' }}
        </AppButton>
        <AppButton v-if="office.leadAvailable" type="submit" :disabled="!canSend">
          Kirim ke Amang <Send :size="14" />
        </AppButton>
      </div>
    </form>
    <p class="muted small mt mb-0">
      Revisi masuk ke PR yang masih terbuka; kalau PR sudah di-merge, revisi memakai branch baru.
    </p>
  </AppCard>
</template>
