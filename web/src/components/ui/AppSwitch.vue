<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  label: { type: String, required: true },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <label class="switch">
    <input
      type="checkbox"
      role="switch"
      :checked="modelValue"
      @change="$emit('update:modelValue', $event.target.checked)"
    />
    <span>{{ label }}</span>
  </label>
</template>
