import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'
import MyFeedback from '../views/MyFeedback.vue'
import UserLayout from '../layouts/UserLayout.vue'

const routes = [
  { path: '/login', name: 'login', component: Login },
  {
    path: '/',
    component: UserLayout,
    redirect: '/chat',
    children: [
      { path: 'chat', name: 'user-chat', component: Chat, meta: { title: '问答' } },
      { path: 'feedback', name: 'user-feedback', component: MyFeedback, meta: { title: '我的反馈' } }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' }
]

export default routes
