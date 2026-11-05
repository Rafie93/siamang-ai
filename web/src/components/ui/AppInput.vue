<script setup>
import { useId } from 'vue'

defineOptions({ inheritAttrs: false })
defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, default: '' },
  error: { type: String, default: '' },
  hint: { type: String, default: '' },
})
defineEmits(['update:modelValue'])

const id = useId()
</script>

<template>
  <div class="field">
    <label v-if="label" :for="id" class="field-label">{{ label }}</label>
    <input
      v-bind="$attrs"
      :id="id"
      class="field-control"
      :value="modelValue"
      :aria-invalid="error ? 'true' : null"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <span v-if="error" class="field-error">{{ error }}</span>
    <span v-else-if="hint" class="field-note">{{ hint }}</span>
  </div>
</template>
