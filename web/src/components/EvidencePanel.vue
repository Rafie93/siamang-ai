<script setup>
// What the run executed: Programmer's sandbox work, the operator's checks, and recovery. All of it is read from
// the stored report; a section is absent when the run did not record it, never filled in.
import { CircleCheck, CircleDashed } from '@lucide/vue'
import { computed } from 'vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { evidenceView } from '@/domain/evidence.js'

const props = defineProps({ report: { type: Object, default: null } })
const view = computed(() => evidenceView(props.report))
const verdict = computed(() => {
  const v = view.value.verification
  if (v.passed) return 'LULUS'
  return v.status === 'failed' ? 'GAGAL' : 'TIDAK TERBUKTI'
})
</script>

<template>
  <section v-if="view.any" class="evidence-panel stack mb" aria-label="Bukti eksekusi">
    <h3 class="section-title mb-0">Eksekusi</h3>

    <AppAlert v-if="view.sandbox" class="evidence-sandbox" role="note" :tone="view.sandbox.tone">
      <strong>{{ view.sandbox.text }}</strong>
      <span v-if="view.sandbox.ran">
        · {{ view.sandbox.turns }} giliran · {{ view.sandbox.runs }} perintah ·
        {{ view.sandbox.verifiedText }}
      </span>
    </AppAlert>

    <AppCard v-if="view.verification" inset class="evidence-verification">
      <template #head>
        <div class="row">
          <AppTag :tone="view.verification.tone">{{ verdict }}</AppTag>
          <strong>{{ view.verification.text }}</strong>
        </div>
      </template>
      <p v-if="view.verification.note" class="muted">{{ view.verification.note }}</p>
      <div v-if="view.verification.checks.length" class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Pemeriksaan</th>
              <th>Hasil</th>
              <th>Exit</th>
              <th>Durasi</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in view.verification.checks" :key="c.name">
              <td>{{ c.name }}</td>
              <td>
                <AppTag :tone="c.tone">{{ c.text }}</AppTag>
              </td>
              <td>{{ c.exitCode ?? '—' }}</td>
              <td>{{ c.seconds === null ? '—' : c.seconds + ' dtk' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <details
        v-for="c in view.verification.checks.filter(x => x.output && x.status !== 'passed')"
        :key="c.name + '-out'"
        class="mt"
      >
        <summary>Output {{ c.name }} (ekor)</summary>
        <pre class="code-block">{{ c.output }}</pre>
      </details>
      <p class="small mt" style="margin-bottom: 4px">{{ view.verification.criteria.text }}</p>
      <ul v-if="view.verification.criteria.items.length" class="evidence-criteria">
        <li v-for="(item, i) in view.verification.criteria.items" :key="i">
          <CircleCheck v-if="item.verified" :size="15" class="text-success" aria-hidden="true" />
          <CircleDashed v-else :size="15" class="faint" aria-hidden="true" />
          {{ item.text }}
          <small v-if="item.verified" class="faint">· dibuktikan oleh {{ item.check }}</small>
          <small v-else class="faint">· belum dibuktikan</small>
        </li>
      </ul>
    </AppCard>

    <AppCard v-if="view.recovery" inset class="evidence-recovery">
      <template #head>
        <div class="row">
          <AppTag :tone="view.recovery.tone">RECOVERY</AppTag>
          <strong>{{ view.recovery.text }}</strong>
        </div>
      </template>
      <div class="row mb">
        <AppTag v-for="t in view.recovery.tiers" :key="t.tier" :tone="t.used ? 'warning' : ''">
          Tingkat {{ t.tier }} · {{ t.text }}{{ t.used ? ' · dipakai' : '' }}
        </AppTag>
      </div>
      <p v-if="view.recovery.reason" class="text-error">Berhenti karena: {{ view.recovery.reason }}</p>
      <p v-if="view.recovery.resumed" class="muted">
        Delivery yang sudah selesai dilanjutkan dari checkpoint, bukan dikerjakan ulang.
      </p>
      <ol class="evidence-attempts">
        <li v-for="(a, i) in view.recovery.attempts" :key="i">
          Percobaan {{ a.attempt }} · {{ a.tierText }} ·
          <strong :class="a.ok ? 'text-success' : 'text-error'">{{ a.ok ? 'berhasil' : 'gagal' }}</strong>
          <span v-if="a.failure" class="faint"> — {{ a.failure }}</span>
        </li>
      </ol>
      <template v-if="view.recovery.debt.length">
        <h4 class="section-title mt">Utang teknis (ikut ke deskripsi PR)</h4>
        <ul class="evidence-debt">
          <li v-for="(d, i) in view.recovery.debt" :key="i">
            <strong>{{ d.stage }}</strong> · {{ d.tierText }}: {{ d.note }}
          </li>
        </ul>
      </template>
    </AppCard>
  </section>
</template>
