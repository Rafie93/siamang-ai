<script setup>
import { computed } from 'vue'
import MarkdownText from '@/components/MarkdownText.vue'
import TaskPicker from '@/components/TaskPicker.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppCollapse from '@/components/ui/AppCollapse.vue'
import AppTabs from '@/components/ui/AppTabs.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { SOURCE_TEXT, STATUS_TEXT } from '@/domain/documents.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const DOC_TONES = { generated: 'success', failed: 'error' }
const tabs = computed(() =>
  office.documentGroups.map(g => ({
    value: g.id,
    label: g.name,
    note: `${g.generated}/${g.documents.length}`,
  })),
)

const toggle = kind => {
  office.documentKinds = office.documentKinds.includes(kind)
    ? office.documentKinds.filter(k => k !== kind)
    : [...office.documentKinds, kind]
}

// This button sits at the bottom of a long document, so closing it must not strand the reader further down the list.
const closeDocument = (kind, event) => {
  event.currentTarget.closest('.collapse-item').scrollIntoView({ block: 'nearest' })
  toggle(kind)
}
</script>

<template>
  <AppCard title="Dokumen agen">
    <TaskPicker class="mb" />
    <AppAlert role="note" tone="info" class="mb">
      Dokumen "Tulisan model" adalah draf dari AI, bukan bukti. Hasil test hanya terisi dari eksekusi nyata.
    </AppAlert>
    <p v-if="!office.selectedId" class="muted mb-0">Pilih task untuk melihat dokumennya.</p>
    <template v-else>
      <AppTabs
        :model-value="office.documentAgent"
        :tabs="tabs"
        aria-label="Agen"
        class="mb"
        @update:model-value="office.pickDocumentAgent"
      />
      <div class="collapse-list document-list">
        <AppCollapse
          v-for="d in office.activeGroup?.documents || []"
          :key="d.kind"
          :open="office.documentKinds.includes(d.kind)"
          :disabled="!d.content && !d.reason"
          @toggle="toggle(d.kind)"
        >
          <template #title>
            <span class="document-heading">
              <span>{{ d.title }}</span>
              <small>{{ SOURCE_TEXT[d.source] }}{{ d.model_id ? ` · ${d.model_id}` : '' }}</small>
            </span>
          </template>
          <template #aside>
            <AppTag :tone="DOC_TONES[d.status]" class="doc-status" :data-status="d.status">
              {{ STATUS_TEXT[d.status] || d.status }}
            </AppTag>
          </template>
          <p v-if="d.reason">{{ d.reason }}</p>
          <MarkdownText v-if="d.content" :text="d.content" />
          <div class="mt" style="text-align: end">
            <AppButton variant="ghost" size="sm" @click="closeDocument(d.kind, $event)">Tutup</AppButton>
          </div>
        </AppCollapse>
      </div>
    </template>
  </AppCard>
</template>
