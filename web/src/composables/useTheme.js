import { computed, ref } from 'vue'

const KEY = 'siamang.theme'
const MODES = ['light', 'dark', 'system']

function saved() {
  try {
    const value = localStorage.getItem(KEY)
    return MODES.includes(value) ? value : 'system'
  } catch {
    return 'system'
  }
}

const systemQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null
const mode = ref(saved())
const systemDark = ref(!!systemQuery?.matches)

const dark = computed(() => (mode.value === 'system' ? systemDark.value : mode.value === 'dark'))

function apply() {
  document.documentElement.dataset.theme = dark.value ? 'dark' : 'light'
}

systemQuery?.addEventListener?.('change', event => {
  systemDark.value = event.matches
  apply()
})

export function setThemeMode(value) {
  mode.value = value
  try {
    localStorage.setItem(KEY, value)
  } catch {
    // Storage can be unavailable; the choice still lasts for this tab.
  }
  apply()
}

export function useTheme() {
  const cycle = () => setThemeMode(MODES[(MODES.indexOf(mode.value) + 1) % MODES.length])

  return { mode, dark, cycle }
}
