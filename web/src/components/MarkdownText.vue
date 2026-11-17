<script setup>
import { h } from 'vue'
import MermaidDiagram from '@/components/MermaidDiagram.vue'
import AppButton from '@/components/ui/AppButton.vue'
import { parseInline, parseMarkdown } from '@/domain/labels.js'

const props = defineProps({
  text: { type: String, default: '' },
  editableDiagrams: { type: Boolean, default: false },
  // Diagrams changed but not yet part of this document, by number: shown in place of what it says.
  diagramOverrides: { type: Object, default: () => ({}) },
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

// Numbered as the backend numbers them: the nth Mermaid fence of the document, valid or not.
const diagram = (written, index) => {
  const source = props.diagramOverrides[index] ?? written
  const drawn = h(MermaidDiagram, { source })
  if (!props.editableDiagrams) return drawn
  return h('div', { class: 'diagram-block' }, [
    h('div', { class: 'diagram-block-actions' }, [
      h(
        AppButton,
        { variant: 'secondary', size: 'sm', onClick: () => emit('edit-diagram', { index, source }) },
        () => 'Edit diagram',
      ),
    ]),
    drawn,
  ])
}

const block = (b, diagrams) => {
  switch (b.type) {
    case 'heading':
      return h(`h${Math.min(b.level + 3, 6)}`, inline(b.text))
    case 'code':
      return h('pre', { class: 'code-block' }, b.text)
    case 'mermaid':
      return diagram(b.text, diagrams.next++)
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
  const diagrams = { next: 0 }
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
