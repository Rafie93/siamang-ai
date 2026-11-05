import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    chunkSizeWarningLimit: 3000,
    rollupOptions: { output: { manualChunks: { three: ['three'] } } },
  },
  // Clones live under .local and the agents write to them: that must never reload the operator's page.
  server: { watch: { ignored: ['**/.local/**', '**/dist/**', '**/test-results/**'] } },
  test: { environment: 'jsdom', include: ['tests/*.test.js'] },
})
