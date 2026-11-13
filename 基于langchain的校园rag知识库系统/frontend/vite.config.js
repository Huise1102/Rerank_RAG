import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(() => {
  const mode = process.env.VITE_APP_MODE === 'admin' ? 'admin' : 'user'
  return {
    plugins: [vue()],
    define: {
      'import.meta.env.VITE_APP_MODE': JSON.stringify(mode)
    },
    server: {
      host: '127.0.0.1',
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8004',
          changeOrigin: true
        }
      }
    }
  }
})
