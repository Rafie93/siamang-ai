<script setup>
import { onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useTheme } from '@/composables/useTheme'

const props = defineProps({
  source: { type: String, required: true },
  // An editor's preview: a mistake keeps the last good picture on screen and is reported, not shown here.
  live: { type: Boolean, default: false },
})
const emit = defineEmits(['result'])

const { dark } = useTheme()
const id = `mermaid-${useId()}`
const svg = ref('')
const failed = ref(false)
let latest = 0

const draw = async () => {
  const request = ++latest
  try {
    // Loaded on first use: Mermaid is large, and most pages never show a diagram.
    const { default: mermaid } = await import('mermaid')

    // A model wrote the source. Strict mode sanitizes the SVG and drops click handlers, and a diagram cannot override it.
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      theme: 'base',
      themeVariables: {
        darkMode: dark.value,
        background: dark.value ? '#1B2320' : '#FFFFFF',
        primaryColor: dark.value ? '#24493A' : '#DDF1E7',
        primaryBorderColor: dark.value ? '#3DBA84' : '#1B7F52',
        lineColor: dark.value ? '#A9B8B0' : '#5B6B63',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      },
    })

    // Mermaid removes any element with the id it is given, which would take the picture on screen with it.
    const result = await mermaid.render(`${id}-${request}`, props.source)
    if (request !== latest) return
    svg.value = result.svg
    failed.value = false
    emit('result', { source: props.source, error: '' })
  } catch (e) {
    if (request !== latest) return
    if (!props.live) svg.value = ''
    failed.value = true
    emit('result', { source: props.source, error: e?.message || 'Diagram tidak bisa digambar.' })
  }
}

// A document can hold twenty diagrams: each is drawn only as it comes near the screen. An editor draws at once.
const root = ref()
const near = ref(props.live || typeof IntersectionObserver !== 'function')
let observer
onMounted(() => {
  if (near.value) return
  observer = new IntersectionObserver(
    entries => {
      if (!entries.some(e => e.isIntersecting)) return
      near.value = true
      observer.disconnect()
    },
    { rootMargin: '600px 0px' },
  )
  observer.observe(root.value)
})

// Polling replaces the documents with equal text, which must not redraw the diagram.
watch([() => props.source, dark, near], () => near.value && draw(), { immediate: true })
onBeforeUnmount(() => {
  latest++
  observer?.disconnect()
})
</script>

<template>
  <div ref="root" class="mermaid-frame">
    <div v-if="svg" class="mermaid-diagram" v-html="svg" />
    <p v-else-if="failed && live" class="muted">Belum ada gambar: perbaiki kodenya dulu.</p>
    <div v-else-if="failed">
      <p class="muted small">Diagram ini tidak bisa digambar. Berikut sumbernya:</p>
      <pre class="code-block">{{ source }}</pre>
    </div>
    <p v-else class="muted">Menggambar diagram…</p>
  </div>
</template>
