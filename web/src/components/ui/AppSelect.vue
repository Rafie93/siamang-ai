<script setup>
import { computed, useId } from 'vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: String, default: null },
  label: { type: String, default: '' },
  // Plain strings, or { title, value, disabled }.
  items: { type: Array, default: () => [] },
  placeholder: { type: String, default: '' },
  hint: { type: String, default: '' },
})

defineEmits(['update:modelValue'])

const id = useId()
const options = computed(() =>
  props.items.map(item => (typeof item === 'string' ? { title: item, value: item } : item)),
)
const unset = computed(() => !options.value.some(o => o.value === props.modelValue))
</script>

<template>
  <div class="field">
    <label v-if="label" :for="id" class="field-label">{{ label }}</label>
    <select
      v-bind="$attrs"
      :id="id"
      class="field-control"
      :value="modelValue ?? ''"
      @change="$emit('update:modelValue', $event.target.value)"
    >
      <option v-if="unset" value="" disabled selected>{{ placeholder }}</option>
      <option v-for="o in options" :key="o.value" :value="o.value" :disabled="o.disabled">
        {{ o.title }}
      </option>
    </select>
    <span v-if="hint" class="field-note">{{ hint }}</span>
  </div>
</template>
