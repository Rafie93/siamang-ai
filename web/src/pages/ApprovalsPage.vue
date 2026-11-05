<script setup>
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { approvalChips } from '@/domain/evidence.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
</script>

<template>
  <AppCard title="Persetujuan proposal">
    <p>
      Periksa patch, QA, dan review sebelum memutuskan. Untuk proyek GitHub, persetujuan membuat commit di
      branch task dan membuka PR. Merge tetap Anda yang melakukan.
    </p>
    <p v-if="!office.approvals.length" class="muted">Tidak ada proposal menunggu keputusan.</p>
    <div class="stack">
      <AppCard
        v-for="a in office.approvals"
        :key="a.run_id"
        inset
        :title="a.task_id"
        :subtitle="`${a.wait_reason} · ${a.eligible ? 'Eligible' : 'Belum eligible untuk approve'}`"
      >
        <div class="row mb">
          <AppTag v-for="chip in approvalChips(a)" :key="chip.key" :tone="chip.tone" class="approval-chip">
            {{ chip.text }}
          </AppTag>
        </div>
        <div class="row">
          <AppButton variant="secondary" @click="office.readEvidence(a)">Baca bukti</AppButton>
          <AppButton
            :disabled="!office.can('approve') || !a.eligible"
            @click="office.ask('approve', a.run_id)"
          >
            Approve
          </AppButton>
          <AppButton
            variant="danger"
            :disabled="!office.can('approve')"
            @click="office.ask('reject', a.run_id)"
          >
            Reject
          </AppButton>
        </div>
      </AppCard>
    </div>
    <small class="faint block mt">Maksimum 100 proposal ditampilkan oleh API.</small>
  </AppCard>
</template>
