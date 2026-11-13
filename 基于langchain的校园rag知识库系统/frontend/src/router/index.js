import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'
import MyFeedback from '../views/MyFeedback.vue'
import AdminLayout from '../layouts/AdminLayout.vue'
import UserLayout from '../layouts/UserLayout.vue'
import Knowledge from '../views/admin/Knowledge.vue'
import Strategy from '../views/admin/Strategy.vue'
import ModelConfig from '../views/admin/ModelConfig.vue'
import Logs from '../views/admin/Logs.vue'
import FeedbackAdmin from '../views/admin/FeedbackAdmin.vue'
import Users from '../views/admin/Users.vue'

const mode = import.meta.env.VITE_APP_MODE
const adminMode = mode === 'admin'

const adminChildren = [
  { path: 'chat', name: 'admin-chat', component: Chat, meta: { title: '问答' } },
  { path: 'knowledge', name: 'admin-knowledge', component: Knowledge, meta: { title: '知识库' } },
  { path: 'strategy', name: 'admin-strategy', component: Strategy, meta: { title: '检索策略' } },
  { path: 'model', name: 'admin-model', component: ModelConfig, meta: { title: '模型配置' } },
  { path: 'logs', name: 'admin-logs', component: Logs, meta: { title: '日志' } },
  { path: 'feedback', name: 'admin-feedback', component: FeedbackAdmin, meta: { title: '反馈处理' } },
  { path: 'users', name: 'admin-users', component: Users, meta: { title: '用户与权限' } }
]

const userChildren = [
  { path: 'chat', name: 'user-chat', component: Chat, meta: { title: '问答' } },
  { path: 'feedback', name: 'user-feedback', component: MyFeedback, meta: { title: '我的反馈' } }
]

const routes = [
  { path: '/login', name: 'login', component: Login },
  {
    path: '/',
    component: adminMode ? AdminLayout : UserLayout,
    redirect: '/chat',
    children: adminMode ? adminChildren : userChildren
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' }
]

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
