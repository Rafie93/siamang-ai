<script setup>
defineProps({
  messages: { type: Array, default: () => [] },
  // Who answers the owner here; the Lead unless the thread is with another agent.
  speaker: { type: String, default: 'Amang' },
})
</script>

<template>
  <div class="conversation">
    <article v-for="m in messages" :key="m.position" :data-role="m.role">
      <b>{{ m.role === 'owner' ? 'Anda' : speaker }}</b>
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
