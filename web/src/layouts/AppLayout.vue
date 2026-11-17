<script setup>
import {
  LaptopMinimal,
  Menu,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Sun,
} from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import AppTag from '@/components/ui/AppTag.vue'
import { useTheme } from '@/composables/useTheme'
import { pages } from '@/router'
import { useOfficeStore } from '@/stores/office'

const office = useOfficeStore()
const route = useRoute()
const { mode, cycle } = useTheme()

const navOpen = ref(false)
// On a wide screen the sidebar can shrink to its icons. Remembered per browser; a phone always uses the drawer.
const NAV_KEY = 'siamang.nav.collapsed'
const readCollapsed = () => {
  try {
    return localStorage.getItem(NAV_KEY) === '1'
  } catch {
    return false
  }
}
const collapsed = ref(readCollapsed())
const toggleCollapsed = () => {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(NAV_KEY, collapsed.value ? '1' : '0')
  } catch {
    // Storage may be blocked; the sidebar still toggles for this visit.
  }
}
const navPages = pages.filter(p => p.icon && !p.hidden)
const themeIcon = computed(() => ({ light: Sun, dark: Moon, system: Monitor })[mode.value])
const themeLabel = computed(
  () => ({ light: 'Tema terang', dark: 'Tema gelap', system: 'Tema mengikuti sistem' })[mode.value],
)
const connection = computed(() => {
  if (office.loading) return { tone: '', text: 'Menghubungkan…' }
  return office.connected
    ? { tone: 'success', text: 'Backend tersambung' }
    : { tone: 'error', text: 'Backend tidak tersedia' }
})
const updates = computed(() =>
  office.streaming
    ? { text: 'Live', title: 'Perubahan dari backend tampil saat terjadi' }
    : { text: 'Polling', title: 'Tanpa stream: halaman menanyakan perubahan secara berkala' },
)

watch(
  () => route.fullPath,
  () => {
    navOpen.value = false
  },
)
</script>

<template>
  <aside class="sidebar" :class="{ open: navOpen, collapsed }">
    <RouterLink class="brand" :to="{ name: 'office' }">
      <img src="/logo.png" alt="" width="34" height="34" />
      <span><strong>Siamang</strong><small>Tim engineering AI</small></span>
    </RouterLink>

    <nav class="nav" aria-label="Workspace">
      <RouterLink
        v-for="p in navPages"
        :key="p.name"
        class="nav-item"
        :class="{ 'nav-parent-active': route.meta.parent === p.name }"
        :to="{ name: p.name }"
        :title="collapsed ? p.title : undefined"
      >
        <component :is="p.icon" :size="17" aria-hidden="true" />
        <span class="nav-label">{{ p.title }}</span>
        <span v-if="p.name === 'approvals' && office.approvals.length" class="nav-badge">
          {{ office.approvals.length }}
        </span>
      </RouterLink>
    </nav>

    <div class="local-note">
      <strong><LaptopMinimal :size="14" aria-hidden="true" /> Berjalan di laptop Anda</strong>
      Token tetap di server lokal, dengan database terisolasi.
    </div>
  </aside>
  <div v-if="navOpen" class="scrim" @click="navOpen = false" />

  <div class="main" :class="{ 'nav-collapsed': collapsed }">
    <header class="topbar">
      <button type="button" class="icon-btn topbar-menu" aria-label="Buka menu" @click="navOpen = true">
        <Menu :size="20" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="icon-btn topbar-collapse"
        :aria-label="collapsed ? 'Lebarkan sidebar' : 'Ciutkan sidebar'"
        :title="collapsed ? 'Lebarkan sidebar' : 'Ciutkan sidebar'"
        :aria-expanded="!collapsed"
        @click="toggleCollapsed"
      >
        <component :is="collapsed ? PanelLeftOpen : PanelLeftClose" :size="18" aria-hidden="true" />
      </button>
      <span class="topbar-crumb truncate"
        >Workspace / <b>{{ route.meta.title }}</b></span
      >
      <span class="topbar-spacer" />
      <AppTag :tone="connection.tone">{{ connection.text }}</AppTag>
      <AppTag v-if="office.connected" class="topbar-updates" :title="updates.title">{{
        updates.text
      }}</AppTag>
      <button
        type="button"
        class="icon-btn"
        aria-label="Refresh data"
        :disabled="office.loading"
        @click="office.refresh"
      >
        <RefreshCw :size="16" aria-hidden="true" />
      </button>
      <button type="button" class="icon-btn" :aria-label="themeLabel" :title="themeLabel" @click="cycle">
        <component :is="themeIcon" :size="16" aria-hidden="true" />
      </button>
    </header>

    <main class="content">
      <PageHeader />
      <RouterView />
    </main>
    <ConfirmDialog />
  </div>
</template>
