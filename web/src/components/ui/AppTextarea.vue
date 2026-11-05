<script setup>
import { useId } from 'vue'

defineOptions({ inheritAttrs: false })
defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, default: '' },
})
defineEmits(['update:modelValue'])

const id = useId()
</script>

<template>
  <div class="field">
    <label v-if="label" :for="id" class="field-label">{{ label }}</label>
    <textarea
      v-bind="$attrs"
      :id="id"
      class="field-control"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
    />
  </div>
</template>
