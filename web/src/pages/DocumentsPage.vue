<script setup>
import { ArrowLeft, ChevronRight, Search } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import DesignEditsNotice from '@/components/DesignEditsNotice.vue'
import DiagramEditor from '@/components/DiagramEditor.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { tokenCss, tokensFrom } from '@/domain/design.js'
import {
  SOURCE_TEXT,
  STATUS_TEXT,
  blockSummary,
  documentType,
  outlineOf,
  searchHit,
} from '@/domain/documents.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const DOC_TONES = { generated: 'success', failed: 'error' }
const DESIGN_STAGES = ['briefing', 'planning', 'analysis', 'designing']
const FILTERS = [
  { id: 'all', label: 'Semua' },
  { id: 'diagram', label: 'Diagram' },
  { id: 'wireframe', label: 'Desain UI' },
  { id: 'text', label: 'Teks' },
]
const OUTLINE_SEARCH_FROM = 8 // a table of contents this long gets its own filter

const query = ref('')
const filter = ref('all')
const folded = ref(new Set())
const reading = ref(false) // on a phone the list and the reader take turns
const outlineQuery = ref('')
const reader = ref()
const editing = ref(null)

// Each document once, with what the list and the reader need from its text.
const groups = computed(() =>
  office.documentGroups.map(g => ({
    ...g,
    documents: g.documents.map(d => {
      const outline = d.content ? outlineOf(d.content, d.blocks || ['mermaid']) : []
      return {
        ...d,
        agent: g.id,
        agentName: g.name,
        outline,
        summary: blockSummary(outline),
        type: documentType(outline),
      }
    }),
  })),
)
const everything = computed(() => groups.value.flatMap(g => g.documents))
const counts = computed(() => {
  const made = everything.value.filter(d => d.content)
  return Object.fromEntries(
    FILTERS.map(f => [f.id, f.id === 'all' ? made.length : made.filter(d => d.type === f.id).length]),
  )
})
const searching = computed(() => !!query.value.trim() || filter.value !== 'all')
const shown = computed(() =>
  groups.value
    .map(g => ({
      ...g,
      documents: g.documents
        .filter(d => filter.value === 'all' || (d.content && d.type === filter.value))
        .map(d => ({ ...d, hit: searchHit(d, query.value) }))
        .filter(d => d.hit.match),
    }))
    .filter(g => g.documents.length || !searching.value),
)
const found = computed(() => shown.value.reduce((n, g) => n + g.documents.length, 0))

// The one being read: the owner's choice, else the first written document of the agent they came from.
const current = computed(() => {
  const all = everything.value
  return (
    all.find(d => d.kind === office.documentKind) ||
    all.find(d => d.agent === office.documentAgent && d.content) ||
    all.find(d => d.content) ||
    null
  )
})
const open = d => {
  office.documentKind = d.kind
  reading.value = true
  outlineQuery.value = ''
  nextTick(() => reader.value?.scrollTo?.({ top: 0 }))
}
const toggleGroup = id => {
  const next = new Set(folded.value)
  next.has(id) ? next.delete(id) : next.add(id)
  folded.value = next
}
watch(
  () => office.selectedId,
  () => (reading.value = false),
)

const anchor = computed(() => (current.value ? `doc-${current.value.kind}` : ''))
const outline = computed(() => current.value?.outline || [])
const blocksIn = computed(() => outline.value.filter(e => e.type === 'block').length)
// Twenty sequence diagrams need no label saying so on each line; a mix of kinds does.
const mixedKinds = computed(
  () => new Set(outline.value.filter(e => e.type === 'block').map(e => e.kind)).size > 1,
)
const lowest = computed(() => Math.min(...outline.value.map(e => e.level), 9))
const matches = (e, q) => {
  const term = q.trim().toLowerCase()
  return !!term && [e.label, e.kind, e.source].some(v => (v || '').toLowerCase().includes(term))
}
const shownOutline = computed(() =>
  outlineQuery.value.trim() ? outline.value.filter(e => matches(e, outlineQuery.value)) : outline.value,
)
const jump = e => {
  document.getElementById(`${anchor.value}-${e.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Owner edits, as before: only design documents, only while the task allows it.
const canEdit = d =>
  office.editMode && office.can('approve') && d.status === 'generated' && DESIGN_STAGES.includes(d.stage)
const proposals = d =>
  office.editMode === 'revision' ? (office.designEdits?.items || []).filter(e => e.kind === d.kind) : []
const overrides = d => Object.fromEntries(proposals(d).map(e => [e.diagram, e.source]))
const tokensCss = computed(() =>
  tokenCss(tokensFrom(everything.value.find(d => d.kind === 'design_tokens')?.content)),
)
const openEditor = (d, { index, source, lang }) => {
  editing.value = { kind: d.kind, title: d.title, index, source, lang, base: d.content_sha256 }
}
</script>

<template>
  <p v-if="!office.selectedId" class="muted">Pilih task di atas untuk melihat hasil kerja tim.</p>
  <template v-else>
    <DesignEditsNotice class="mb" />
    <div class="doc-browser" :class="{ reading }">
      <aside class="doc-nav" aria-label="Daftar dokumen">
        <div class="doc-nav-tools">
          <label class="doc-search">
            <Search :size="15" aria-hidden="true" />
            <input
              v-model="query"
              type="search"
              placeholder="Cari judul atau isi"
              aria-label="Cari dokumen"
            />
          </label>
          <div class="doc-filters" role="group" aria-label="Jenis">
            <button
              v-for="f in FILTERS"
              :key="f.id"
              type="button"
              class="doc-filter"
              :aria-pressed="filter === f.id"
              @click="filter = f.id"
            >
              {{ f.label }} <span>{{ counts[f.id] }}</span>
            </button>
          </div>
        </div>
        <div class="doc-groups">
          <p v-if="searching && !found" class="muted small doc-empty">Tidak ada yang cocok.</p>
          <section v-for="g in shown" :key="g.id" class="doc-group">
            <button
              type="button"
              class="doc-group-head"
              :aria-expanded="searching || !folded.has(g.id)"
              @click="toggleGroup(g.id)"
            >
              <ChevronRight :size="14" class="doc-group-chevron" aria-hidden="true" />
              <span class="doc-group-name">{{ g.name }}</span>
              <span class="doc-group-count">{{ g.generated }}/{{ g.documents.length }}</span>
            </button>
            <ul v-show="searching || !folded.has(g.id)" class="doc-rows">
              <li v-for="d in g.documents" :key="d.kind">
                <button
                  type="button"
                  class="doc-row"
                  :class="{ quiet: !d.content }"
                  :aria-current="current?.kind === d.kind ? 'true' : undefined"
                  :title="STATUS_TEXT[d.status] || d.status"
                  @click="open(d)"
                >
                  <span class="doc-dot" :data-status="d.status" aria-hidden="true" />
                  <span class="doc-row-title">{{ d.title }}</span>
                  <span v-if="d.summary" class="doc-row-count">{{ d.summary }}</span>
                </button>
                <p v-if="d.hit.snippet" class="doc-snippet">{{ d.hit.snippet }}</p>
              </li>
            </ul>
          </section>
        </div>
      </aside>

      <section ref="reader" class="doc-reader" aria-label="Isi dokumen terpilih">
        <button type="button" class="doc-back" @click="reading = false">
          <ArrowLeft :size="15" aria-hidden="true" /> Daftar dokumen
        </button>
        <p v-if="!current" class="muted">Belum ada dokumen yang ditulis untuk task ini.</p>
        <template v-else>
          <header class="doc-reader-head">
            <div class="doc-reader-heading">
              <p class="doc-reader-meta">
                {{ current.agentName }} · {{ SOURCE_TEXT[current.source]
                }}{{ current.model_id ? ` · ${current.model_id}` : '' }}
              </p>
              <h2 class="doc-reader-title">{{ current.title }}</h2>
            </div>
            <div class="doc-reader-tags">
              <AppTag v-if="proposals(current).length" tone="warning">
                {{ proposals(current).length }} usulan revisi
              </AppTag>
              <AppTag v-if="current.edited_by" tone="info">Diedit {{ current.edited_by }}</AppTag>
              <AppTag :tone="DOC_TONES[current.status]" :data-status="current.status">
                {{ STATUS_TEXT[current.status] || current.status }}
              </AppTag>
            </div>
          </header>
          <p v-if="current.reason" class="doc-reason">{{ current.reason }}</p>
          <p v-else-if="!current.content" class="muted">
            {{ STATUS_TEXT[current.status] || 'Belum ada isi.' }}
          </p>
          <div v-if="current.content" class="doc-reader-body" :class="{ 'with-outline': outline.length > 1 }">
            <nav v-if="outline.length > 1" class="doc-outline" aria-label="Daftar isi">
              <p class="doc-outline-head">
                Daftar isi
                <span v-if="blocksIn">{{ blocksIn }} gambar</span>
              </p>
              <input
                v-if="outline.length >= OUTLINE_SEARCH_FROM"
                v-model="outlineQuery"
                type="search"
                class="doc-outline-search"
                placeholder="Saring daftar isi"
                aria-label="Saring daftar isi"
              />
              <ol class="doc-outline-list">
                <li v-for="e in shownOutline" :key="e.id">
                  <button
                    type="button"
                    class="doc-outline-item"
                    :class="{ block: e.type === 'block', hit: matches(e, query) }"
                    :style="{ paddingInlineStart: `${8 + (e.level - lowest) * 12}px` }"
                    @click="jump(e)"
                  >
                    <span class="doc-outline-label">{{ e.label }}</span>
                    <small v-if="e.type === 'block' && mixedKinds">{{ e.kind }}</small>
                  </button>
                </li>
              </ol>
              <p v-if="!shownOutline.length" class="muted small">Tidak ada yang cocok.</p>
            </nav>
            <div class="doc-content">
              <MarkdownText
                :text="current.content"
                :anchor="anchor"
                :editable-diagrams="!!canEdit(current)"
                :diagram-overrides="overrides(current)"
                :blocks="current.blocks || ['mermaid']"
                :tokens-css="tokensCss"
                @edit-diagram="openEditor(current, $event)"
              />
            </div>
          </div>
        </template>
      </section>
    </div>
    <DiagramEditor v-if="editing" v-bind="editing" :tokens-css="tokensCss" @close="editing = null" />
  </template>
</template>
