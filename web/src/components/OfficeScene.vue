<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
const props = defineProps({
  team: Object,
  motion: Boolean,
  quality: String,
  focus: String,
  bubble: Object,
  subject: String,
})
const emit = defineEmits(['select', 'toggle-demo'])
const host = ref(),
  error = ref(false),
  follow = ref(false)
let office,
  alive = true
function lost() {
  error.value = true
  office?.dispose()
  office = null
}
onMounted(async () => {
  try {
    const { createOffice } = await import('../three/office.js')
    if (alive) {
      office = createOffice(host.value, {
        team: props.team,
        motion: props.motion,
        quality: props.quality,
        onSelect: id => emit('select', id),
      })
      host.value.addEventListener('office-lost', lost)
      office.setDetail(props.bubble || null)
    }
  } catch {
    error.value = true
  }
})
watch(
  () => props.team,
  team => office?.setTeam(team),
)
watch(
  () => [props.motion, props.quality, follow.value],
  () => office?.update({ motion: props.motion, quality: props.quality, follow: follow.value }),
)
watch(
  () => props.focus,
  id => office?.focus(id || null),
)
watch(
  () => props.bubble,
  value => office?.setDetail(value || null),
)
onBeforeUnmount(() => {
  alive = false
  host.value?.removeEventListener('office-lost', lost)
  office?.dispose()
})

function resetView() {
  office?.reset()
  emit('select', null)
}
</script>

<template>
  <div class="scene-wrap">
    <div ref="host" class="scene"></div>
    <div v-if="error" class="scene-fallback">
      <span>◇</span>
      <h3>Office dalam mode 2D</h3>
      <p>WebGL tidak tersedia. Semua fungsi task tetap dapat digunakan.</p>
    </div>
    <div class="scene-top">
      <span class="pill">SIAMANG OFFICE · 6 AGEN</span>
      <span class="pill" :class="{ warn: team?.simulated }">{{
        team?.simulated ? 'MODE DEMO' : 'LIVE · BACKEND'
      }}</span>
    </div>
    <div v-if="team?.simulated" class="sim-banner" role="note">
      SIMULASI — animasi demo, bukan aktivitas backend
    </div>
    <div class="scene-controls">
      <button type="button" :aria-pressed="!!team?.simulated" @click="emit('toggle-demo')">
        {{ team?.simulated ? '■ Keluar demo' : '▶ Mode demo' }}
      </button>
      <button type="button" :aria-pressed="follow" :disabled="error" @click="follow = !follow">
        {{ follow ? '◉ Mengikuti' : '◎ Ikuti aktivitas' }}
      </button>
      <button
        type="button"
        class="camera-reset"
        :disabled="error"
        aria-label="Reset kamera"
        @click="resetView"
      >
        ↺ Reset view
      </button>
    </div>
    <div class="scene-caption">
      {{ team?.label || 'Kantor SIAMANG' }}
      <small v-if="subject">{{ subject }}</small>
      <small>Klik karakter untuk melihat prosesnya · geser untuk orbit · scroll untuk zoom</small>
    </div>
  </div>
</template>
