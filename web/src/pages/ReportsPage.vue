<script setup>
import { ChevronDown } from '@lucide/vue'
import EvidencePanel from '@/components/EvidencePanel.vue'
import TaskPicker from '@/components/TaskPicker.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppCard from '@/components/ui/AppCard.vue'
import { evidenceNote } from '@/domain/evidence.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const SECTIONS = ['plan', 'analysis', 'patch', 'qa', 'review', 'loop']
</script>

<template>
  <AppCard title="Laporan proposal">
    <TaskPicker class="mb" />
    <AppAlert role="note" tone="info" class="mb">Status branch dan PR ada di detail task.</AppAlert>
    <p v-if="!office.reports.length" class="muted mb-0">Belum ada laporan untuk task ini.</p>
    <article v-for="r in office.reports" :key="r.run_id" class="mb">
      <h3 class="section-title">Run {{ r.run_id }}</h3>
      <AppAlert class="mb evidence-note" role="note" :tone="evidenceNote(r, office.cap?.execution).type">
        {{ evidenceNote(r, office.cap?.execution).text }}
      </AppAlert>
      <EvidencePanel :report="r" />
      <div class="collapse-list">
        <details v-for="key in SECTIONS" :key="key" class="collapse-item">
          <summary class="collapse-head">
            <span class="collapse-title">{{ key.toUpperCase() }} · {{ r[key]?.status || 'detail' }}</span>
            <ChevronDown class="collapse-chevron" :size="16" aria-hidden="true" />
          </summary>
          <div class="collapse-body">
            <pre class="code-block">{{
              r[key] ? JSON.stringify(r[key], null, 2) : 'Artefak belum tersedia'
            }}</pre>
          </div>
        </details>
      </div>
    </article>
  </AppCard>
</template>
