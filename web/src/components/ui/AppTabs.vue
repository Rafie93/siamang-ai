<script setup>
defineProps({
  modelValue: { type: String, default: '' },
  // { value, label, badge?, note? }
  tabs: { type: Array, default: () => [] },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <div class="tabs" role="tablist">
    <button
      v-for="t in tabs"
      :key="t.value"
      type="button"
      role="tab"
      class="tab"
      :aria-selected="modelValue === t.value"
      @click="$emit('update:modelValue', t.value)"
    >
      {{ t.label }}
      <small v-if="t.note" class="faint">{{ t.note }}</small>
      <span v-if="t.badge" class="tag" data-tone="warning">{{ t.badge }}</span>
    </button>
  </div>
</template>
