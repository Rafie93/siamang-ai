<script setup>
import { h } from 'vue'
import MermaidDiagram from '@/components/MermaidDiagram.vue'
import { parseInline, parseMarkdown } from '@/domain/labels.js'

const props = defineProps({ text: { type: String, default: '' } })

const inline = text =>
  parseInline(text).map(part => {
    if (part.type === 'strong') return h('strong', part.text)
    if (part.type === 'code') return h('code', part.text)
    if (part.type === 'link')
      return h('a', { href: part.href, target: '_blank', rel: 'noopener noreferrer' }, part.text)

    return part.text
  })

const block = b => {
  switch (b.type) {
    case 'heading':
      return h(`h${Math.min(b.level + 3, 6)}`, inline(b.text))
    case 'code':
      return h('pre', { class: 'code-block' }, b.text)
    case 'mermaid':
      return h(MermaidDiagram, { source: b.text })
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

const Rendered = () => h('div', { class: 'markdown' }, parseMarkdown(props.text).map(block))
</script>

<template>
  <Rendered />
</template>
