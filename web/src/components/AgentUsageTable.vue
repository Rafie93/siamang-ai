<script setup>
import {
  agentLabel,
  agentRole,
  callsText,
  timeText,
  tokenCell,
  tokenExact,
  tokenTitle,
} from '@/domain/usage.js'

defineProps({ agents: { type: Array, default: () => [] } })
</script>

<template>
  <p v-if="!agents.length" class="muted mb-0">Belum ada pekerjaan agent yang tercatat.</p>
  <div v-else class="table-wrap">
    <table class="table usage-table" aria-label="Pemakaian per agent">
      <thead>
        <tr>
          <th>Agent</th>
          <th class="num">Input</th>
          <th class="num">Output</th>
          <th class="num">Total</th>
          <th class="num" title="Lama agent bekerja; pekerjaan paralel dihitung sekali">Durasi</th>
          <th>Panggilan model</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="a in agents" :key="a.agent">
          <td>
            <strong>{{ agentLabel(a.agent) }}</strong>
            <small class="block faint">{{ a.models.join(', ') || agentRole(a.agent) }}</small>
          </td>
          <td class="num" :title="tokenTitle(a)">{{ tokenCell(a, 'input_tokens') }}</td>
          <td class="num" :title="tokenExact(a.output_tokens)">{{ tokenCell(a, 'output_tokens') }}</td>
          <td class="num" :title="tokenExact(a.total_tokens)">
            <strong>{{ tokenCell(a, 'total_tokens') }}</strong>
          </td>
          <td class="num">{{ timeText(a.active_ms) }}</td>
          <td>
            <small :class="{ 'text-warning': a.failed_calls }">{{ callsText(a) }}</small>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
