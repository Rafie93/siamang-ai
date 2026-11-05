<script setup>
import { onBeforeUnmount, ref, useId, watch } from 'vue'
import { useTheme } from '@/composables/useTheme'

const props = defineProps({ source: { type: String, required: true } })

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

    const result = await mermaid.render(id, props.source)
    if (request !== latest) return
    svg.value = result.svg
    failed.value = false
  } catch {
    if (request !== latest) return
    svg.value = ''
    failed.value = true
  }
}

// Polling replaces the documents with equal text, which must not redraw the diagram.
watch([() => props.source, dark], draw, { immediate: true })
onBeforeUnmount(() => {
  latest++
})
</script>

<template>
  <div v-if="svg" class="mermaid-diagram" v-html="svg" />
  <div v-else-if="failed">
    <p class="muted small">Diagram ini tidak bisa digambar. Berikut sumbernya:</p>
    <pre class="code-block">{{ source }}</pre>
  </div>
  <p v-else class="muted">Menggambar diagram…</p>
</template>
