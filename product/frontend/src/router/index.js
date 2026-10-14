import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

// 四个业务页面：问答 / 知识库 / 检索策略 / 日志（无登录页、无设置页、无评估页）
const routes = [
  {
    path: '/',
    component: MainLayout,
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/ChatQuery.vue'),
        meta: { title: '问答' },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgeBase.vue'),
        meta: { title: '知识库' },
      },
      {
        path: 'strategy',
        name: 'strategy',
        component: () => import('@/views/RetrievalStrategy.vue'),
        meta: { title: '检索策略' },
      },
      {
        path: 'logs',
        name: 'logs',
        component: () => import('@/views/Logs.vue'),
        meta: { title: '日志' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const title = to.meta?.title
  document.title = title
    ? `${title} · 基于混合检索与Rerank的领域知识库系统`
    : '基于混合检索与Rerank的领域知识库系统'
})

export default router
