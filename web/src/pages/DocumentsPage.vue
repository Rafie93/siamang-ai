<script setup>
import { computed, ref } from 'vue'
import DiagramEditor from '@/components/DiagramEditor.vue'
import DesignEditsNotice from '@/components/DesignEditsNotice.vue'
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

const DESIGN_STAGES = ['briefing', 'planning', 'analysis']
const editing = ref(null)
const canEdit = d =>
  office.editMode && office.can('approve') && d.status === 'generated' && DESIGN_STAGES.includes(d.stage)
// A published task's document stays what shipped: its open proposals are drawn over it.
const proposals = d =>
  office.editMode === 'revision' ? (office.designEdits?.items || []).filter(e => e.kind === d.kind) : []
const overrides = d => Object.fromEntries(proposals(d).map(e => [e.diagram, e.source]))
const openEditor = (d, { index, source }) => {
  editing.value = { kind: d.kind, title: d.title, index, source, base: d.content_sha256 }
}

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
      <DesignEditsNotice class="mb" />
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
            <AppTag v-if="proposals(d).length" tone="warning" class="doc-status">
              {{ proposals(d).length }} usulan revisi
            </AppTag>
            <AppTag v-if="d.edited_by" tone="info" class="doc-status">Diedit {{ d.edited_by }}</AppTag>
            <AppTag :tone="DOC_TONES[d.status]" class="doc-status" :data-status="d.status">
              {{ STATUS_TEXT[d.status] || d.status }}
            </AppTag>
          </template>
          <p v-if="d.reason">{{ d.reason }}</p>
          <MarkdownText
            v-if="d.content"
            :text="d.content"
            :editable-diagrams="!!canEdit(d)"
            :diagram-overrides="overrides(d)"
            @edit-diagram="openEditor(d, $event)"
          />
          <div class="mt" style="text-align: end">
            <AppButton variant="ghost" size="sm" @click="closeDocument(d.kind, $event)">Tutup</AppButton>
          </div>
        </AppCollapse>
      </div>
    </template>
    <DiagramEditor v-if="editing" v-bind="editing" @close="editing = null" />
  </AppCard>
</template>
