<template>
  <div class="user-shell">
    <div class="user-header">
      <div class="brand">校园知识库问答</div>
      <div style="display: flex; align-items: center; gap: 16px">
        <router-link to="/chat" class="nav-item">问答</router-link>
        <router-link to="/feedback" class="nav-item">我的反馈</router-link>
        <el-badge :value="unread" :hidden="unread === 0" :max="99">
          <span class="nav-item" style="cursor: pointer" @click="openNotifications">通知</span>
        </el-badge>
        <el-dropdown>
          <span class="nav-item" style="cursor: pointer">设置</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="auth.isAdmin" @click="gotoAdmin">返回管理端</el-dropdown-item>
              <el-dropdown-item @click="passwordDialog = true">修改密码</el-dropdown-item>
              <el-dropdown-item divided @click="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
    <div class="user-body">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </div>

    <button
      class="theme-fab"
      type="button"
      :title="isDark ? '切换到浅色模式' : '切换到深色模式'"
      @click="isDark = !isDark"
    >
      <el-icon :size="18"><component :is="isDark ? Sunny : Moon" /></el-icon>
    </button>

    <el-drawer v-model="notificationDrawer" title="站内通知" direction="rtl" size="380px">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
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
        <div class="muted" style="font-size: 12px; margin-top: 4px">{{ formatTime(item.created_at) }}</div>
      </div>
    </el-drawer>

    <PasswordDialog v-model="passwordDialog" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Moon, Sunny } from '@element-plus/icons-vue'
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

function formatTime(value) {
  return formatNotificationTime(value)
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

function gotoAdmin() {
  window.open(import.meta.env.VITE_ADMIN_URL || 'http://127.0.0.1:5174', '_blank')
}
</script>

<style scoped>
.nav-item {
  color: #fff;
  text-decoration: none;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  font-size: 16px;
  line-height: 1;
}

.theme-fab {
  position: fixed;
  left: 12px;
  bottom: 12px;
  z-index: 30;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
  color: #606266;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
  transition: background-color 0.2s, color 0.2s;
}

.theme-fab:hover {
  background: #f2f6fc;
  color: #1f6fd0;
}

.notification-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 8px;
  background: #f5f7fa;
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
</style>
