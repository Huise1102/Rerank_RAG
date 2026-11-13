import Login from '../views/Login.vue'
import WecomCallback from '../views/WecomCallback.vue'
import Chat from '../views/Chat.vue'
import MyFeedback from '../views/MyFeedback.vue'
import MobileLayout from '../layouts/MobileLayout.vue'

const routes = [
  { path: '/login', name: 'mobile-login', component: Login },
  { path: '/wecom/callback', name: 'mobile-wecom-callback', component: WecomCallback },
  {
    path: '/',
    component: MobileLayout,
    redirect: '/chat',
    children: [
      { path: 'chat', name: 'mobile-chat', component: Chat, meta: { title: '问答' } },
      {
        path: 'feedback',
        name: 'mobile-feedback',
        component: MyFeedback,
        meta: { title: '我的反馈' }
      }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' }
]

export default routes
