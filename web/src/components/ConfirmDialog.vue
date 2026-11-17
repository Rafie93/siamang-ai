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
  ['approve', 'reject', 'design-approve', 'design-reject', 'design-send'].includes(action.value),
)
const reasonRequired = computed(() => ['reject', 'design-reject'].includes(action.value))

const asksAboutEdits = computed(() => ['design-approve', 'design-send'].includes(action.value))
const edits = computed(() => (asksAboutEdits.value ? office.designEdits?.items || [] : []))
const revision = computed(() => action.value === 'design-send' && office.editMode === 'revision')
const heading = computed(() =>
  edits.value.length
    ? `Ada ${edits.value.length} diagram berubah. ${revision.value ? 'Buat task revisi?' : 'Kirim ke Programmer?'}`
    : `Konfirmasi ${action.value}`,
)
const loadingEdits = computed(() => asksAboutEdits.value && office.designEditsLoading)
const confirmLabel = computed(() =>
  revision.value ? 'Buat task revisi' : edits.value.length ? 'Kirim ke Programmer' : 'Konfirmasi',
)
const titleOf = kind => edits.value.find(e => e.kind === kind)?.title || kind
const redivideFirst = async () => {
  office.confirm = null
  await office.redivide()
}

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
    case 'design-send':
      return revision.value
        ? 'Task baru dibuat dan langsung berjalan: tim menulis desain baru memakai diagram Anda, lalu berhenti lagi di persetujuan desain bila policy menahannya. Kode dan dokumen task ini tidak berubah, dan kuota model ikut terpakai.'
        : 'Programmer mengerjakan ulang semua paket kerja memakai diagram ini, termasuk yang sudah lulus, dan kuota model ikut terpakai. Perubahan ini tercatat sebagai perubahan atas desain yang sudah disetujui.'
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
    :title="heading"
    @close="office.confirm = null"
  >
    <AppAlert v-if="office.error" tone="error" class="mb"
      >{{ errorText(office.error) }} · Aksi belum berhasil.</AppAlert
    >
    <p>{{ message }}</p>
    <template v-if="edits.length">
      <ul class="design-edits">
        <li v-for="e in edits" :key="`${e.kind}-${e.diagram}`">
          <strong>{{ e.title }}</strong
          >, diagram {{ e.diagram + 1 }}
          <span class="muted small">+{{ e.added }} / −{{ e.removed }} baris · {{ e.edited_by }}</span>
        </li>
      </ul>
      <p v-if="!revision" class="muted small">
        Jika diagram ini bertentangan dengan dokumen lain, Programmer diminta mengikuti diagram.
      </p>
      <AppAlert v-if="office.designEdits.clipped.length" tone="warning" class="mb">
        Terlalu panjang untuk masukan Programmer, jadi bisa terpotong:
        {{ office.designEdits.clipped.map(titleOf).join(', ') }}.
      </AppAlert>
      <AppAlert v-if="action === 'design-approve' && office.designEdits.divided" tone="info" class="mb">
        Pembagian kerja dibuat sebelum diagram diubah. Bagi ulang dulu jika perubahannya menyentuh pembagian
        file.
      </AppAlert>
    </template>
    <AppAlert v-else-if="office.designEdits?.failed" tone="warning" class="mb">
      Ringkasan perubahan diagram gagal dimuat. Hasil edit yang tersimpan tetap ikut dikirim.
    </AppAlert>
    <AppTextarea
      v-if="needsReason"
      v-model="office.reason"
      :label="action === 'design-send' ? 'Catatan untuk tim (opsional)' : 'Alasan'"
      rows="3"
      maxlength="300"
      :required="reasonRequired"
    />
    <template #actions>
      <AppButton variant="secondary" :disabled="office.busy" @click="office.confirm = null"
        >Kembali</AppButton
      >
      <AppButton
        v-if="action === 'design-approve' && edits.length && office.designEdits.divided"
        variant="secondary"
        :disabled="!office.can('run')"
        @click="redivideFirst"
      >
        Bagi ulang dulu
      </AppButton>
      <AppButton
        :disabled="office.busy || loadingEdits || (reasonRequired && !office.reason.trim())"
        @click="office.act"
      >
        {{ office.busy ? 'Memproses…' : confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>
