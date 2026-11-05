<script setup>
import { computed, onMounted, ref } from 'vue'
import { version } from '../../package.json'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import { errorText } from '@/domain/labels.js'
import { agentName } from '@/domain/team.js'
import { post, request } from '@/services/api.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const QUALITY = [
  { title: 'Tinggi', value: 'high' },
  { title: 'Hemat GPU', value: 'low' },
]
const AGENT_TITLES = { lead: agentName('amang') }
const PROVIDER_TITLES = { ollama: 'Ollama', claude_cli: 'Claude CLI' }
const catalog = ref(null)
const catalogError = ref('')
const saving = ref('')
const saved = ref('')

const key = (provider, model) => `${provider}/${model}`

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

onMounted(load)
</script>

<template>
  <div class="stack">
    <div class="grid grid-2">
      <AppCard title="Preferensi workspace">
        <div class="stack">
          <AppSwitch v-model="office.motion" label="Aktifkan animasi tahap kerja" />
          <AppSwitch
            :model-value="office.demo"
            label="Mode demo kantor (simulasi berlabel, tidak memengaruhi data)"
            @update:model-value="office.toggleDemo"
          />
          <AppSelect v-model="office.quality" label="Kualitas 3D" :items="QUALITY" />
          <small class="faint"
            >Preferensi berlaku selama tab ini terbuka; credential tidak disimpan di browser.</small
          >
        </div>
      </AppCard>
      <AppCard title="Koneksi lokal">
        <p>Browser → gateway lokal → SIAMANG → model</p>
        <p>
          Gateway memegang token acak di server dan memakai database terisolasi. Jangan expose layanan ke
          jaringan publik.
        </p>
        <p>Permission backend: {{ office.permissions.join(', ') || 'Belum diketahui' }}</p>
        <p class="mb-0">Antarmuka web {{ version }} · HTTP /v0 · pembaruan polling</p>
      </AppCard>
    </div>
    <AppCard title="Model per agen">
      <div class="stack">
        <AppAlert v-if="catalogError" tone="error">{{ errorText(catalogError) }}</AppAlert>
        <AppAlert v-if="catalog?.unreachable.length" tone="warning">
          Ollama tidak bisa dihubungi, jadi modelnya tidak bisa dipilih. Jalankan ollama serve lalu buka lagi
          halaman ini.
        </AppAlert>
        <p v-if="!catalog && !catalogError" class="muted mb-0">Memuat daftar model…</p>
        <div v-if="catalog" class="grid grid-2">
          <AppSelect
            v-for="row in catalog.agents"
            :key="row.agent"
            :model-value="row.provider ? key(row.provider, row.provider_model) : null"
            :label="AGENT_TITLES[row.agent] || agentName(row.agent)"
            :items="choices"
            placeholder="Belum ada model"
            :disabled="!office.can('configure') || !!saving"
            :hint="hint(row)"
            @update:model-value="choose(row, $event)"
          />
        </div>
        <small class="faint">
          Tersimpan ke siamang.toml dan berlaku di panggilan model berikutnya, tanpa restart. Model cloud
          mengirim isi kode proyek ke luar komputer ini.
        </small>
      </div>
    </AppCard>
  </div>
</template>
