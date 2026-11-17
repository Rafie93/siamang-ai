<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  label: { type: String, required: true },
  // With a description the switch becomes a settings row: text on the left, toggle on the right.
  description: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <label class="switch" :class="{ 'switch-row': description, disabled }">
    <input
      type="checkbox"
      role="switch"
      :checked="modelValue"
      :disabled="disabled"
      @change="$emit('update:modelValue', $event.target.checked)"
    />
    <span v-if="description" class="switch-text">
      <span class="switch-label">{{ label }}</span>
      <small>{{ description }}</small>
    </span>
    <span v-else>{{ label }}</span>
  </label>
</template>
