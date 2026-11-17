<script setup>
import { computed } from 'vue'
import { colorTokens, tokenVariables } from '@/domain/design.js'

// The design tokens as the owner sees them: colors as swatches, the rest as the CSS variables a wireframe uses.
const props = defineProps({ tokens: { type: Object, required: true } })
const colors = computed(() => colorTokens(props.tokens))
const others = computed(() => tokenVariables(props.tokens).filter(([name]) => !name.startsWith('--color-')))
</script>

<template>
  <div class="tokens">
    <ul v-if="colors.length" class="token-swatches" aria-label="Warna">
      <li v-for="[name, value] in colors" :key="name" class="token-swatch">
        <span class="token-chip" :style="{ background: value }" aria-hidden="true" />
        <span class="token-name">{{ name }}</span>
        <code>{{ value }}</code>
      </li>
    </ul>
    <dl v-if="others.length" class="token-list">
      <template v-for="[name, value] in others" :key="name">
        <dt>
          <code>{{ name }}</code>
        </dt>
        <dd>{{ value }}</dd>
      </template>
    </dl>
    <p v-if="!colors.length && !others.length" class="muted">
      Token ini belum berisi warna, font, spacing, atau radius.
    </p>
  </div>
</template>
