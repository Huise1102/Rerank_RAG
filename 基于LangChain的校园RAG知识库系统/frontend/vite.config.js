import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'

export default defineConfig(() => {
  const mode = ['admin', 'mobile'].includes(process.env.VITE_APP_MODE)
    ? process.env.VITE_APP_MODE
    : 'user'
  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@app-routes': fileURLToPath(new URL(`./src/router/routes.${mode}.js`, import.meta.url))
      }
    },
    define: {
      'import.meta.env.VITE_APP_MODE': JSON.stringify(mode)
    },
    server: {
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8004',
          changeOrigin: true
        }
      }
    }
  }
})
