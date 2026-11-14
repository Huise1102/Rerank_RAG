<template>
  <div class="mobile-shell">
    <header class="mobile-topbar">
      <div class="mobile-brand">校园知识库</div>
      <div class="mobile-actions">
        <button
          class="icon-button"
          type="button"
          :aria-label="isDark ? '切换到浅色模式' : '切换到深色模式'"
          @click="isDark = !isDark"
        >
          <el-icon :size="20"><component :is="isDark ? Sunny : Moon" /></el-icon>
        </button>
        <el-badge :value="unread" :hidden="unread === 0" :max="99">
          <button class="icon-button" aria-label="通知" @click="openNotifications">
            <el-icon :size="20"><Bell /></el-icon>
          </button>
        </el-badge>
        <el-dropdown trigger="click">
          <button class="icon-button" aria-label="设置">
            <el-icon :size="20"><Setting /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="passwordDialog = true">修改密码</el-dropdown-item>
              <el-dropdown-item divided @click="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <div v-if="offline" class="offline-bar">当前处于离线状态，页面可浏览，问答需联网后重试</div>

    <main class="mobile-content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>

    <nav class="mobile-tabbar">
      <router-link to="/chat" class="tab-link">
        <el-icon :size="20"><ChatDotRound /></el-icon>
        <span>问答</span>
      </router-link>
      <router-link to="/feedback" class="tab-link">
        <el-icon :size="20"><Document /></el-icon>
        <span>我的反馈</span>
      </router-link>
    </nav>

    <el-drawer v-model="notificationDrawer" title="站内通知" direction="rtl" size="100%">
      <div class="notification-toolbar">
        <span class="muted">未读 {{ unread }} 条</span>
        <el-button link :disabled="unread === 0" @click="markAllRead">全部已读</el-button>
      </div>
      <div v-if="notifications.length === 0" class="muted">暂无通知</div>
      <div
        v-for="item in notifications"
        :key="item.id"
        class="notification-item"
        :class="{ unread: !item.read }"
        @click="openNotification(item)"
      >
        <div class="notification-title">{{ item.title }}</div>
        <div class="notification-content">{{ item.content }}</div>
        <div class="muted notification-time">{{ formatNotificationTime(item.created_at) }}</div>
      </div>
    </el-drawer>

    <PasswordDialog v-model="passwordDialog" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChatDotRound, Document, Moon, Setting, Sunny } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { logout as logoutApi } from '../api/auth'
import {
  formatNotificationTime,
  useNotifications
} from '../composables/useNotifications'
import PasswordDialog from '../components/PasswordDialog.vue'
import { useTheme } from '../composables/useTheme'

const auth = useAuthStore()
const router = useRouter()
const { isDark } = useTheme()
const passwordDialog = ref(false)
const notificationDrawer = ref(false)
const { notifications, unread, loadNotifications, markAllRead, readOne } = useNotifications()
const offline = ref(!navigator.onLine)

function syncOnlineState() {
  offline.value = !navigator.onLine
}

onMounted(() => {
  window.addEventListener('online', syncOnlineState)
  window.addEventListener('offline', syncOnlineState)
})

onBeforeUnmount(() => {
  window.removeEventListener('online', syncOnlineState)
  window.removeEventListener('offline', syncOnlineState)
})

async function openNotifications() {
  notificationDrawer.value = true
  await loadNotifications()
}

async function openNotification(item) {
  await readOne(item)
  if (item.link) {
    notificationDrawer.value = false
    router.push(item.link)
  }
}

async function logout() {
  try {
    await logoutApi()
  } catch {
    // 退出失败时仍然清理本地登录态
  }
  auth.clear()
  router.replace('/login')
}
</script>

<style scoped>
.mobile-shell {
  height: 100%;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #f5f7fa;
}

.mobile-topbar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 52px;
  padding: env(safe-area-inset-top) 12px 0;
  background: #1f3b73;
  color: #fff;
}

.mobile-brand {
  font-size: 16px;
  font-weight: 600;
}

.mobile-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.icon-button {
  width: 44px;
  height: 44px;
  border: none;
  background: transparent;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.offline-bar {
  flex: none;
  padding: 6px 12px;
  font-size: 12px;
  text-align: center;
  color: #8a5a00;
  background: #fdf6ec;
  border-bottom: 1px solid #faecd8;
}

.mobile-content {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.mobile-content > * {
  flex: 1;
  min-width: 0;
}

.mobile-tabbar {
  flex: none;
  display: flex;
  min-height: 56px;
  padding-bottom: env(safe-area-inset-bottom);
  background: #fff;
  border-top: 1px solid #e4e7ed;
}

.tab-link {
  flex: 1;
  min-height: 56px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  font-size: 12px;
  color: #606266;
  text-decoration: none;
}

.tab-link.router-link-active {
  color: #1f6fd0;
  font-weight: 600;
}

.notification-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.notification-item {
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 8px;
  background: #f5f7fa;
  cursor: pointer;
}

.notification-item.unread {
  background: #ecf5ff;
}

.notification-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.notification-content {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
}

.notification-time {
  font-size: 12px;
  margin-top: 4px;
}
</style>
