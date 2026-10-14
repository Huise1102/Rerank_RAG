import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/assets/theme.css'

import App from '@/App.vue'
import router from '@/router'

// 挂载前先应用主题偏好，避免首屏浅色闪一下再切到深色
if (localStorage.getItem('app-theme') === 'dark') {
  document.documentElement.classList.add('dark')
}

const app = createApp(App)

// 注册 Element Plus 自带图标，避免引入第三方图标库
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
