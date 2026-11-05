// A separate file because the gateway CSP (script-src 'self') blocks inline scripts.
// Runs before the first paint, so a dark-theme operator never sees a white flash.
try {
  const saved = localStorage.getItem('siamang.theme')
  const dark = saved === 'dark' || (saved !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
} catch {
  document.documentElement.dataset.theme = 'light'
}
