import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import routes from '@app-routes'

const adminMode = import.meta.env.VITE_APP_MODE === 'admin'

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.path === '/login') {
    if (auth.token && auth.user) {
      return { path: '/chat' }
    }
    return true
  }
  if (to.path === '/wecom/callback') {
    return true
  }
  if (!auth.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (!auth.user) {
    try {
      await auth.refresh()
    } catch {
      auth.clear()
      return { path: '/login' }
    }
  }
  if (adminMode && auth.user?.role !== 'admin') {
    ElMessage.error('普通用户不能进入管理端')
    window.location.href = `${import.meta.env.VITE_USER_URL || 'http://127.0.0.1:5175'}/login`
    return false
  }
  return true
})

export default router
