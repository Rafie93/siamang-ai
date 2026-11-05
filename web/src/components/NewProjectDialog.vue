<script setup>
import { computed, watch } from 'vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppInput from '@/components/ui/AppInput.vue'
import { errorText, repositorySlug } from '@/domain/labels.js'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const form = computed(() => office.newProject)
const slug = computed(() => repositorySlug(form.value.repository))
const showRepoError = computed(() => form.value.repository.trim() && !slug.value)
let suggested = ''

// The repository usually names the project: fill it in until the owner writes their own.
watch(slug, value => {
  if (form.value.name.trim() && form.value.name !== suggested) return
  suggested = value ? value.split('/')[1] : ''
  form.value.name = suggested
})

const close = () => {
  if (!office.busy) office.projectDialog = false
}
</script>

<template>
  <AppDialog
    :open="office.projectDialog"
    :persistent="office.busy"
    title="Proyek baru"
    subtitle="Tautkan repo GitHub. Amang membaca isinya, lalu bertanya tentang kebutuhan Anda sebelum tim bekerja."
    @close="close"
  >
    <form id="new-project" class="stack" @submit.prevent="office.createProject">
      <AppAlert v-if="office.projectError" tone="error">{{ errorText(office.projectError) }}</AppAlert>
      <AppInput
        v-model="form.repository"
        autofocus
        label="Repository GitHub"
        maxlength="200"
        placeholder="https://github.com/pemilik/nama-repo"
        :error="showRepoError ? 'Pakai bentuk https://github.com/pemilik/nama-repo' : ''"
      />
      <AppInput v-model="form.name" label="Nama proyek" maxlength="120" placeholder="Landing page saya" />
      <p class="muted small mb-0">
        Akun <code>gh</code> Anda harus bisa menulis ke repo itu. Hasil kerja hanya masuk lewat branch
        <code>siamang/*</code> dan PR. Repo yang masih kosong akan diberi satu commit README di
        <code>main</code>
        sebagai dasar PR.
      </p>
    </form>
    <template #actions>
      <AppButton variant="ghost" :disabled="office.busy" @click="close">Batal</AppButton>
      <AppButton
        type="submit"
        form="new-project"
        :disabled="!office.can('submit') || !form.name.trim() || !slug"
      >
        {{ office.busy ? 'Memproses…' : 'Buat proyek' }}
      </AppButton>
    </template>
  </AppDialog>
</template>
