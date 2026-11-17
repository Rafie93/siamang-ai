<script setup>
import { h } from 'vue'
import MermaidDiagram from '@/components/MermaidDiagram.vue'
import TokenSwatches from '@/components/TokenSwatches.vue'
import WireframeFrame from '@/components/WireframeFrame.vue'
import AppButton from '@/components/ui/AppButton.vue'
import { parseTokens } from '@/domain/design.js'
import { parseInline, parseMarkdown } from '@/domain/labels.js'

const props = defineProps({
  text: { type: String, default: '' },
  editableDiagrams: { type: Boolean, default: false },
  // Diagrams changed but not yet part of this document, by number: shown in place of what it says.
  diagramOverrides: { type: Object, default: () => ({}) },
  // The fenced languages this document draws and numbers as blocks, from the backend catalog (Spec.blocks).
  blocks: { type: Array, default: () => ['mermaid'] },
  // The design tokens as CSS variables, for the wireframes.
  tokensCss: { type: String, default: '' },
  // With a prefix, headings and blocks get ids `${anchor}-h<n>` and `${anchor}-b<n>`, as outlineOf numbers them.
  anchor: { type: String, default: '' },
})
const emit = defineEmits(['edit-diagram'])

const inline = text =>
  parseInline(text).map(part => {
    if (part.type === 'strong') return h('strong', part.text)
    if (part.type === 'code') return h('code', part.text)
    if (part.type === 'link')
      return h('a', { href: part.href, target: '_blank', rel: 'noopener noreferrer' }, part.text)

    return part.text
  })

const EDIT_LABEL = { mermaid: 'Edit diagram', html: 'Edit wireframe', json: 'Edit token' }
const draw = (lang, source) => {
  if (lang === 'mermaid') return h(MermaidDiagram, { source })
  if (lang === 'html') return h(WireframeFrame, { html: source, css: props.tokensCss })
  const tokens = parseTokens(source)
  if (tokens) return h(TokenSwatches, { tokens })
  return h('div', [
    h('p', { class: 'muted' }, 'JSON token belum valid.'),
    h('pre', { class: 'code-block' }, source),
  ])
}

// Numbered as the backend numbers them: the nth fence of the document in one of its block languages, valid or not.
const diagram = (lang, written, index) => {
  const source = props.diagramOverrides[index] ?? written
  const drawn = draw(lang, source)
  const id = props.anchor ? `${props.anchor}-b${index}` : undefined
  if (!props.editableDiagrams) return id ? h('div', { id, class: 'md-block' }, [drawn]) : drawn
  return h('div', { id, class: ['diagram-block', id && 'md-block'] }, [
    h('div', { class: 'diagram-block-actions' }, [
      h(
        AppButton,
        { variant: 'secondary', size: 'sm', onClick: () => emit('edit-diagram', { index, source, lang }) },
        () => EDIT_LABEL[lang],
      ),
    ]),
    drawn,
  ])
}

const block = (b, diagrams) => {
  switch (b.type) {
    case 'heading': {
      const n = diagrams.heading++
      return h(
        `h${Math.min(b.level + 3, 6)}`,
        { id: props.anchor ? `${props.anchor}-h${n}` : undefined },
        inline(b.text),
      )
    }
    case 'code':
      return b.lang !== 'mermaid' && props.blocks.includes(b.lang)
        ? diagram(b.lang, b.text, diagrams.next++)
        : h('pre', { class: 'code-block' }, b.text)
    case 'mermaid':
      return diagram('mermaid', b.text, diagrams.next++)
    case 'list':
      return h(
        b.ordered ? 'ol' : 'ul',
        b.items.map(item => h('li', inline(item))),
      )
    case 'quote':
      return h('blockquote', inline(b.text))
    case 'table':
      return h(
        'div',
        { class: 'markdown-table' },
        h('table', [
          h(
            'thead',
            h(
              'tr',
              b.head.map(c => h('th', inline(c))),
            ),
          ),
          h(
            'tbody',
            b.rows.map(r =>
              h(
                'tr',
                r.map(c => h('td', inline(c))),
              ),
            ),
          ),
        ]),
      )
    default:
      return h('p', inline(b.text))
  }
}

const Rendered = () => {
  const diagrams = { next: 0, heading: 0 }
  return h(
    'div',
    { class: 'markdown' },
    parseMarkdown(props.text).map(b => block(b, diagrams)),
  )
}
</script>

<template>
  <Rendered />
</template>
