<script setup>
defineProps({ messages: { type: Array, default: () => [] } })
</script>

<template>
  <div class="conversation">
    <article v-for="m in messages" :key="m.position" :data-role="m.role">
      <b>{{ m.role === 'lead' ? 'Amang' : 'Anda' }}</b>
      <p class="mb-0">{{ m.message }}</p>
      <ol v-if="m.questions?.length">
        <li v-for="q in m.questions" :key="q.question">
          {{ q.question }}
          <small v-if="q.options?.length"> Pilihan: {{ q.options.join(' · ') }}</small>
        </li>
      </ol>
    </article>
  </div>
</template>
