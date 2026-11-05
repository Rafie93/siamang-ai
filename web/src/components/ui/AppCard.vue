<script setup>
defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  inset: { type: Boolean, default: false }, // a card inside a card: no shadow
})
</script>

<template>
  <section class="card" :class="{ 'card-inset': inset }">
    <header v-if="title || subtitle || $slots.head || $slots.actions" class="card-head">
      <slot name="head">
        <div>
          <h3 v-if="title" class="card-title">{{ title }}</h3>
          <p v-if="subtitle" class="card-subtitle">{{ subtitle }}</p>
        </div>
      </slot>
      <slot name="actions" />
    </header>
    <div class="card-body"><slot /></div>
  </section>
</template>
