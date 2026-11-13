import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'
import MyFeedback from '../views/MyFeedback.vue'
import AdminLayout from '../layouts/AdminLayout.vue'
import Knowledge from '../views/admin/Knowledge.vue'
import Strategy from '../views/admin/Strategy.vue'
import ModelConfig from '../views/admin/ModelConfig.vue'
import Logs from '../views/admin/Logs.vue'
import FeedbackAdmin from '../views/admin/FeedbackAdmin.vue'
import Users from '../views/admin/Users.vue'

const routes = [
  { path: '/login', name: 'login', component: Login },
  {
    path: '/',
    component: AdminLayout,
    redirect: '/chat',
    children: [
      { path: 'chat', name: 'admin-chat', component: Chat, meta: { title: '问答' } },
      { path: 'knowledge', name: 'admin-knowledge', component: Knowledge, meta: { title: '知识库' } },
      { path: 'strategy', name: 'admin-strategy', component: Strategy, meta: { title: '检索策略' } },
      { path: 'model', name: 'admin-model', component: ModelConfig, meta: { title: '模型配置' } },
      { path: 'logs', name: 'admin-logs', component: Logs, meta: { title: '日志' } },
      { path: 'feedback', name: 'admin-feedback', component: FeedbackAdmin, meta: { title: '反馈处理' } },
      { path: 'users', name: 'admin-users', component: Users, meta: { title: '用户与权限' } }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' }
]

export default routes
