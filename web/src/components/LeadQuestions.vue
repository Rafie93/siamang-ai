<script setup>
import { Send } from '@lucide/vue'
import { computed, ref } from 'vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import { composeAnswers } from '@/domain/labels.js'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  submitLabel: { type: String, default: 'Kirim jawaban' },
})

const emit = defineEmits(['answer'])

const picked = ref({})
const notes = ref({})
const extra = ref('')

const toggle = (index, option) => {
  const current = picked.value[index] || []

  picked.value[index] = current.includes(option) ? current.filter(o => o !== option) : [...current, option]
}

const isPicked = (index, option) => (picked.value[index] || []).includes(option)
const answered = computed(
  () =>
    Object.values(picked.value).some(p => p.length) ||
    Object.values(notes.value).some(n => n.trim()) ||
    !!extra.value.trim(),
)
const send = () => emit('answer', composeAnswers(props.questions, picked.value, notes.value, extra.value))
</script>

<template>
  <form class="lead-questions" aria-label="Pertanyaan dari Amang" @submit.prevent="send">
    <fieldset v-for="(q, i) in questions" :key="q.question">
      <legend>{{ i + 1 }}. {{ q.question }}</legend>
      <div v-if="q.options?.length" class="row mb">
        <button
          v-for="option in q.options"
          :key="option"
          type="button"
          class="chip"
          :aria-pressed="isPicked(i, option)"
          @click="toggle(i, option)"
        >
          {{ option }}
        </button>
      </div>
      <AppInput
        v-model="notes[i]"
        :aria-label="`Jawaban sendiri untuk pertanyaan ${i + 1}`"
        placeholder="Atau tulis jawaban sendiri…"
        maxlength="600"
      />
    </fieldset>
    <AppTextarea v-model="extra" label="Catatan tambahan (opsional)" rows="2" maxlength="1000" />
    <div class="row">
      <AppButton type="submit" :disabled="disabled || !answered"
        >{{ submitLabel }} <Send :size="14"
      /></AppButton>
      <small class="muted">Pertanyaan yang dikosongkan diserahkan ke Amang.</small>
    </div>
  </form>
</template>
