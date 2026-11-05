<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import AppLayout from '@/layouts/AppLayout.vue'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()

onMounted(office.start)
onBeforeUnmount(office.stop)
</script>

<template>
  <AppLayout />
</template>
