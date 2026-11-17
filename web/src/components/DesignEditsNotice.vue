<script setup>
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
</script>

<template>
  <!-- At the gate, approving the design is what sends the edits, so only the other two states need this. -->
  <AppAlert
    v-if="office.editMode !== 'gate' && office.designEdits?.pending"
    role="status"
    tone="warning"
    :title="`${office.designEdits.items.length} diagram berubah dan belum dikirim`"
  >
    <p>
      {{
        office.editMode === 'revision'
          ? 'Task ini sudah terbit, jadi perubahan ini menjadi task revisi. Kode lama tidak tertimpa.'
          : 'Programmer belum melihat perubahan ini. Edit diagram lain dulu bila perlu, lalu kirim sekali.'
      }}
    </p>
    <AppButton :disabled="!office.can('approve')" @click="office.ask('design-send', office.selectedId)">
      {{ office.editMode === 'revision' ? 'Buat task revisi' : 'Kirim ke Programmer' }}
    </AppButton>
  </AppAlert>
</template>
