<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { wireframePage } from '@/domain/design.js'

// A wireframe drawn at a real screen width and scaled to fit. The frame is sandboxed with no permissions and the page
// carries a policy that loads nothing, so model-written HTML can neither run nor reach the network.
const props = defineProps({
  html: { type: String, default: '' },
  css: { type: String, default: '' },
})
const DEVICES = {
  desktop: { label: 'Desktop', width: 1280, height: 800 },
  phone: { label: 'Ponsel', width: 390, height: 780 },
}
const device = ref('desktop')
const host = ref()
const room = ref(0)
let observer

const size = computed(() => DEVICES[device.value])
const scale = computed(() => (room.value ? Math.min(1, room.value / size.value.width) : 1))
const page = computed(() => wireframePage(props.html, props.css))

onMounted(() => {
  const measure = () => (room.value = host.value?.clientWidth || 0)
  measure()
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(measure)
    observer.observe(host.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div class="wireframe">
    <div class="wireframe-devices" role="group" aria-label="Lebar layar">
      <button
        v-for="(d, id) in DEVICES"
        :key="id"
        type="button"
        class="wireframe-device"
        :aria-pressed="device === id"
        @click="device = id"
      >
        {{ d.label }} · {{ d.width }}px
      </button>
    </div>
    <div ref="host" class="wireframe-stage" :style="{ height: `${Math.ceil(size.height * scale)}px` }">
      <iframe
        class="wireframe-frame"
        title="Pratinjau wireframe"
        sandbox=""
        referrerpolicy="no-referrer"
        loading="lazy"
        :srcdoc="page"
        :width="size.width"
        :height="size.height"
        :style="{ transform: `scale(${scale})` }"
      />
    </div>
  </div>
</template>
