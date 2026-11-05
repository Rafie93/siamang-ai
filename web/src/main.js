import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from '@/App.vue'
import { router } from '@/router'
import '@/assets/theme.css'
import '@/assets/app.css'
import '@/assets/office.css'

createApp(App).use(createPinia()).use(router).mount('#app')
