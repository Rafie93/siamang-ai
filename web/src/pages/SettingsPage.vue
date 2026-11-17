<script setup>
import { Bot, FileText, LayoutDashboard } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { version } from '../../package.json'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import AppTabs from '@/components/ui/AppTabs.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { DOCUMENT_AGENTS } from '@/domain/documents.js'
import { errorText } from '@/domain/labels.js'
import { AGENTS, agentName } from '@/domain/team.js'
import { post, request } from '@/services/api.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const SECTIONS = [
  { id: 'settings-dashboard', title: 'Dashboard', icon: LayoutDashboard },
  { id: 'settings-models', title: 'Model per agen', icon: Bot },
  { id: 'settings-documents', title: 'Dokumen per agen', icon: FileText },
]
const QUALITY = [
  { title: 'Tinggi', value: 'high' },
  { title: 'Hemat GPU', value: 'low' },
]
const AGENT_TITLES = { lead: agentName('amang') }
const PROVIDER_TITLES = { ollama: 'Ollama', claude_cli: 'Claude CLI' }
const jump = id => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

// ── Model per agent ─────────────────────────────────────────────
const catalog = ref(null)
const catalogError = ref('')
const saving = ref('')
const saved = ref('')

const key = (provider, model) => `${provider}/${model}`
const officeIdOf = agent => DOCUMENT_AGENTS.find(a => a.id === agent)?.officeId || agent
const roleOf = agent => AGENTS.find(a => a.id === officeIdOf(agent))?.role || ''
const colorOf = agent => AGENTS.find(a => a.id === officeIdOf(agent))?.color || 'var(--color-text-3)'

const choices = computed(() =>
  (catalog.value?.options || []).map(o => ({
    title: [
      o.provider_model,
      PROVIDER_TITLES[o.provider_type] || o.provider_type,
      o.cloud ? 'cloud' : 'lokal',
      ...(o.available ? [] : ['tidak tersedia']),
    ].join(' · '),
    value: key(o.provider, o.provider_model),
    disabled: !o.available,
  })),
)

async function load() {
  try {
    catalog.value = await request('/settings/models')
    catalogError.value = ''
  } catch (e) {
    catalogError.value = e.code
  }
}

async function choose(row, value) {
  const option = catalog.value.options.find(o => key(o.provider, o.provider_model) === value)
  if (!option) return
  saving.value = row.agent
  saved.value = ''
  catalogError.value = ''
  try {
    await post('/settings/models', {
      agent: row.agent,
      provider: option.provider,
      provider_model: option.provider_model,
    })
    saved.value = row.agent
    await office.refresh()
  } catch (e) {
    catalogError.value = e.code
  } finally {
    saving.value = ''
    await load()
  }
}

const hint = row => {
  if (saving.value === row.agent) return 'Menyimpan…'
  return saved.value === row.agent ? 'Tersimpan' : ''
}

// ── Documents per agent ─────────────────────────────────────────
// A switched-off document is not asked of the model and not recorded.
const docSettings = ref(null)
const docError = ref('')
const docBusy = ref('')
const docAgent = ref(DOCUMENT_AGENTS[0].id)

const docGroups = computed(() =>
  DOCUMENT_AGENTS.map(a => {
    const items = (docSettings.value || []).filter(d => d.agent === a.id)
    return { ...a, items, on: items.filter(d => d.enabled).length }
  }).filter(g => g.items.length),
)
const docTabs = computed(() =>
  docGroups.value.map(g => ({ value: g.id, label: g.name, note: `${g.on}/${g.items.length}` })),
)
const docGroup = computed(() => docGroups.value.find(g => g.id === docAgent.value) || docGroups.value[0])
const docTotals = computed(() => {
  const all = docSettings.value || []
  return { on: all.filter(d => d.enabled).length, all: all.length }
})
const canSwitch = computed(() => office.can('configure') && !docBusy.value)

async function loadDocuments() {
  try {
    docSettings.value = (await request('/settings/documents')).items
    docError.value = ''
  } catch (e) {
    docError.value = e.code
  }
}

async function switchDocument(doc, enabled) {
  docBusy.value = doc.kind
  docError.value = ''
  try {
    docSettings.value = (await post('/settings/documents', { kind: doc.kind, enabled })).items
    await office.refresh()
  } catch (e) {
    docError.value = e.code
    await loadDocuments()
  } finally {
    docBusy.value = ''
  }
}

// One agent's documents at once, sent one by one because the backend takes one document per call.
async function switchGroup(group, pick) {
  const changes = group.items.filter(d => d.enabled !== pick(d))
  if (!changes.length) return
  docBusy.value = group.id
  docError.value = ''
  try {
    for (const d of changes) {
      docSettings.value = (await post('/settings/documents', { kind: d.kind, enabled: pick(d) })).items
    }
    await office.refresh()
  } catch (e) {
    docError.value = e.code
    await loadDocuments()
  } finally {
    docBusy.value = ''
  }
}
const changedFromDefault = g => g.items.some(d => d.enabled !== d.default_enabled)

onMounted(() => {
  load()
  loadDocuments()
})
</script>

<template>
  <div class="settings-layout">
    <nav class="settings-nav" aria-label="Bagian pengaturan">
      <button v-for="s in SECTIONS" :key="s.id" type="button" @click="jump(s.id)">
        <component :is="s.icon" :size="16" aria-hidden="true" /> {{ s.title }}
      </button>
    </nav>

    <div class="settings-sections">
      <section
        id="settings-dashboard"
        class="card settings-section"
        aria-labelledby="settings-dashboard-title"
      >
        <header class="settings-section-head">
          <h2 id="settings-dashboard-title">Dashboard</h2>
          <p>
            Cara halaman Office menampilkan tim. Tersimpan di browser ini dan langsung berlaku di dashboard.
          </p>
        </header>
        <div class="settings-rows">
          <AppSwitch
            v-model="office.scene3d"
            label="Tampilkan kantor 3D"
            description="Matikan untuk menampilkan dashboard sebagai percakapan tim. Lebih ringan untuk komputer tanpa GPU."
          />
          <div class="setting-row" :class="{ disabled: !office.scene3d }">
            <div class="setting-text">
              <span class="switch-label">Kualitas 3D</span>
              <small>Hemat GPU menurunkan resolusi dan bayangan kantor 3D.</small>
            </div>
            <div class="setting-control">
              <AppSelect
                v-model="office.quality"
                aria-label="Kualitas 3D"
                :items="QUALITY"
                :disabled="!office.scene3d"
              />
            </div>
          </div>
          <AppSwitch
            v-model="office.motion"
            label="Aktifkan animasi tahap kerja"
            description="Karakter bergerak saat backend melaporkan aktivitas. Matikan bila Anda lebih nyaman tanpa animasi."
          />
          <AppSwitch
            :model-value="office.demo"
            label="Mode demo kantor"
            description="Simulasi berlabel untuk melihat cara tim bekerja. Tidak memengaruhi data proyek."
            @update:model-value="office.toggleDemo"
          />
        </div>
      </section>

      <section id="settings-models" class="card settings-section" aria-labelledby="settings-models-title">
        <header class="settings-section-head">
          <h2 id="settings-models-title">Model per agen</h2>
          <p>
            Tersimpan ke siamang.toml dan berlaku di panggilan model berikutnya, tanpa restart. Model cloud
            mengirim isi kode proyek ke luar komputer ini.
          </p>
        </header>
        <AppAlert v-if="catalogError" tone="error" class="mb">{{ errorText(catalogError) }}</AppAlert>
        <AppAlert v-if="catalog?.unreachable.length" tone="warning" class="mb">
          Ollama tidak bisa dihubungi, jadi modelnya tidak bisa dipilih. Jalankan ollama serve lalu buka lagi
          halaman ini.
        </AppAlert>
        <p v-if="!catalog && !catalogError" class="muted mb-0">Memuat daftar model…</p>
        <div v-if="catalog" class="settings-rows">
          <div v-for="row in catalog.agents" :key="row.agent" class="setting-row">
            <div class="setting-text setting-agent">
              <span class="swatch" :style="{ background: colorOf(row.agent) }" aria-hidden="true" />
              <span>
                <span class="switch-label">{{ AGENT_TITLES[row.agent] || agentName(row.agent) }}</span>
                <small>{{ roleOf(row.agent) }}</small>
              </span>
            </div>
            <div class="setting-control setting-model">
              <AppSelect
                :model-value="row.provider ? key(row.provider, row.provider_model) : null"
                :aria-label="AGENT_TITLES[row.agent] || agentName(row.agent)"
                :items="choices"
                placeholder="Belum ada model"
                :disabled="!office.can('configure') || !!saving"
                :hint="hint(row)"
                @update:model-value="choose(row, $event)"
              />
            </div>
          </div>
        </div>
      </section>

      <section
        id="settings-documents"
        class="card settings-section"
        aria-labelledby="settings-documents-title"
      >
        <header class="settings-section-head row-between">
          <div>
            <h2 id="settings-documents-title">Dokumen per agen</h2>
            <p>
              Dokumen yang dimatikan tidak diminta ke model dan tidak dicatat, jadi menghemat token. Berlaku
              untuk semua proyek mulai run berikutnya.
            </p>
          </div>
          <AppTag v-if="docSettings" tone="primary">{{ docTotals.on }}/{{ docTotals.all }} aktif</AppTag>
        </header>
        <AppAlert v-if="docError" tone="error" class="mb">{{ errorText(docError) }}</AppAlert>
        <p v-if="!docSettings && !docError" class="muted mb-0">Memuat daftar dokumen…</p>
        <template v-if="docSettings && docGroup">
          <AppTabs v-model="docAgent" :tabs="docTabs" />
          <div class="doc-settings-toolbar">
            <span class="muted small">
              <b>{{ docGroup.name }}</b> · {{ docGroup.role }} — {{ docGroup.on }} dari
              {{ docGroup.items.length }}
              dokumen aktif
            </span>
            <div class="row">
              <AppButton
                variant="ghost"
                size="sm"
                :disabled="!canSwitch || docGroup.on === docGroup.items.length"
                @click="switchGroup(docGroup, () => true)"
              >
                Aktifkan semua
              </AppButton>
              <AppButton
                variant="ghost"
                size="sm"
                :disabled="!canSwitch || !changedFromDefault(docGroup)"
                @click="switchGroup(docGroup, d => d.default_enabled)"
              >
                Kembalikan bawaan
              </AppButton>
            </div>
          </div>
          <div class="settings-rows doc-settings-rows">
            <div v-for="d in docGroup.items" :key="d.kind" class="doc-setting">
              <AppSwitch
                :model-value="d.enabled"
                :label="d.title"
                :disabled="!canSwitch"
                @update:model-value="switchDocument(d, $event)"
              />
              <AppTag v-if="d.enabled !== d.default_enabled" tone="warning">Diubah</AppTag>
            </div>
          </div>
        </template>
      </section>

      <p class="faint small settings-footer">
        Siamang web {{ version }} · HTTP /v0 · pembaruan {{ office.streaming ? 'live (SSE)' : 'polling' }} ·
        berjalan lokal, token tetap di server
      </p>
    </div>
  </div>
</template>
