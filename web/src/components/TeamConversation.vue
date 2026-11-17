<script setup>
import { nextTick, ref, watch } from 'vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { AGENTS } from '@/domain/team.js'
import { useOfficeStore } from '@/stores/office'

// The dashboard without 3D: the team's handoffs read as a chat, newest at the bottom.
const office = useOfficeStore()
const feed = ref()
const color = id => AGENTS.find(a => a.id === id)?.color || 'var(--color-text-3)'
const initial = id => office.agentName(id).slice(0, 1).toUpperCase()

watch(
  () => office.teamConversation.length,
  async () => {
    await nextTick()
    feed.value?.scrollTo({ top: feed.value.scrollHeight, behavior: office.motion ? 'smooth' : 'auto' })
  },
  { immediate: true },
)
</script>

<template>
  <AppCard class="convo-card" aria-label="Percakapan tim">
    <template #head>
      <div class="convo-head">
        <div style="min-width: 0">
          <h3 class="card-title">Percakapan tim</h3>
          <p class="card-subtitle truncate">{{ office.stageSubject || 'Belum ada task yang dipantau' }}</p>
        </div>
        <AppTag :tone="office.team.simulated ? 'warning' : office.team.activeAgent ? 'primary' : ''">
          {{ office.team.simulated ? 'Simulasi' : office.team.label }}
        </AppTag>
      </div>
    </template>
    <ol ref="feed" class="convo-feed" aria-live="polite">
      <li v-if="!office.teamConversation.length" class="convo-empty muted">
        Belum ada serah-terima tercatat. Percakapan antar agen muncul di sini saat tim bekerja.
      </li>
      <li v-for="h in office.teamConversation" :key="h.key" class="convo-turn" :data-kind="h.kind">
        <button
          type="button"
          class="convo-avatar"
          :style="{ background: color(h.from) }"
          :aria-label="`Detail ${office.agentName(h.from)}`"
          @click="office.openAgent(h.from)"
        >
          {{ initial(h.from) }}
        </button>
        <div class="convo-body">
          <div class="convo-meta">
            <strong>{{ office.agentName(h.from) }}</strong>
            <span class="faint">→ {{ office.agentName(h.to) }}</span>
            <small class="faint"
              >{{ office.localTime(h.at) }}{{ h.iteration ? ` · iterasi ${h.iteration}` : '' }}</small
            >
          </div>
          <p class="convo-bubble">{{ h.say }}</p>
          <p v-if="h.reply" class="convo-reply">
            <i :style="{ background: color(h.to) }" aria-hidden="true" />
            <b>{{ office.agentName(h.to) }}:</b> {{ h.reply }}
          </p>
        </div>
      </li>
    </ol>
  </AppCard>
</template>
