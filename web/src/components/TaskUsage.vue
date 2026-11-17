<script setup>
import AgentUsageTable from '@/components/AgentUsageTable.vue'
import UsageTotals from '@/components/UsageTotals.vue'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
</script>

<template>
  <section class="mt mb" aria-label="Pemakaian token">
    <h3 class="section-title">Pemakaian token</h3>
    <p v-if="office.taskUsage === null" class="muted mb-0">Memuat pemakaian token…</p>
    <p v-else-if="!office.taskUsage" class="muted mb-0">Pemakaian token belum bisa dibaca dari backend.</p>
    <template v-else>
      <UsageTotals :usage="office.taskUsage" />
      <AgentUsageTable class="mt" :agents="office.taskUsage.agents" />
    </template>
  </section>
</template>
