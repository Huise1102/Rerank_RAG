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
import Departments from '../views/admin/Departments.vue'
import Evaluation from '../views/admin/Evaluation.vue'
import CacheAdmin from '../views/admin/CacheAdmin.vue'
import AgentSettings from '../views/admin/AgentSettings.vue'
import Dashboard from '../views/admin/Dashboard.vue'
import KnowledgeGaps from '../views/admin/KnowledgeGaps.vue'

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
      { path: 'users', name: 'admin-users', component: Users, meta: { title: '用户与权限' } },
      {
        path: 'departments',
        name: 'admin-departments',
        component: Departments,
        meta: { title: '部门管理' }
      },
      { path: 'evaluation', name: 'admin-evaluation', component: Evaluation, meta: { title: '评估回归' } },
      { path: 'dashboard', name: 'admin-dashboard', component: Dashboard, meta: { title: '运营看板' } },
      { path: 'gaps', name: 'admin-gaps', component: KnowledgeGaps, meta: { title: '知识缺口' } },
      { path: 'cache', name: 'admin-cache', component: CacheAdmin, meta: { title: '热点缓存' } },
      { path: 'agent', name: 'admin-agent', component: AgentSettings, meta: { title: '深度问答' } }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' }
]

export default routes
