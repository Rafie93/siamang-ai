<script setup>
defineProps({ tone: { type: String, default: '' } }) // primary | success | info | warning | error
</script>

<template>
  <span class="tag" :data-tone="tone || null"><slot /></span>
</template>
