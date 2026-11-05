<script setup>
import { computed } from 'vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import { errorText } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const action = computed(() => office.confirm?.action)
const needsReason = computed(() =>
  ['approve', 'reject', 'design-approve', 'design-reject'].includes(action.value),
)
const reasonRequired = computed(() => ['reject', 'design-reject'].includes(action.value))

const message = computed(() => {
  switch (action.value) {
    case 'approve':
      return office.selected?.branch
        ? `Perubahan akan di-commit ke branch ${office.selected.branch.branch} dan PR dibuka. Branch utama tidak disentuh, dan merge tetap Anda yang melakukan.`
        : 'Menyetujui proposal melalui Approval System. Patch tetap tidak diterapkan.'
    case 'run':
      return 'Model akan menyusun dokumen dan proposal, dan kuota langganan ikut terpakai. Tidak ada kode proyek dieksekusi.'
    case 'design-approve':
      return 'Programmer mulai menyusun patch berdasarkan desain ini, dan kuota model ikut terpakai.'
    case 'design-reject':
      return 'Task dibatalkan. Tidak ada kode yang disusun.'
    case 'retry':
      return 'Task akan diantrekan ulang, tanpa otomatis dijalankan.'
    default:
      return 'Backend akan memproses aksi ini. Cancel bersifat kooperatif.'
  }
})
</script>

<template>
  <AppDialog
    :open="!!office.confirm"
    :persistent="office.busy"
    :width="520"
    :title="`Konfirmasi ${action}`"
    @close="office.confirm = null"
  >
    <AppAlert v-if="office.error" tone="error" class="mb"
      >{{ errorText(office.error) }} · Aksi belum berhasil.</AppAlert
    >
    <p>{{ message }}</p>
    <AppTextarea
      v-if="needsReason"
      v-model="office.reason"
      label="Alasan"
      rows="3"
      maxlength="300"
      :required="reasonRequired"
    />
    <template #actions>
      <AppButton variant="secondary" :disabled="office.busy" @click="office.confirm = null"
        >Kembali</AppButton
      >
      <AppButton :disabled="office.busy || (reasonRequired && !office.reason.trim())" @click="office.act">
        {{ office.busy ? 'Memproses…' : 'Konfirmasi' }}
      </AppButton>
    </template>
  </AppDialog>
</template>
