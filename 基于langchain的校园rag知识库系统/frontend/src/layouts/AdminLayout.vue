<template>
  <el-container style="height: 100%">
    <el-aside width="210px" class="admin-aside">
      <div class="brand">校园 RAG 管理端</div>
      <el-menu
        :default-active="activePath"
        class="admin-menu"
        background-color="#1f2d3d"
        text-color="#c0c4cc"
        active-text-color="#ffffff"
        router
      >
        <el-menu-item v-if="auth.isAdmin" index="/chat">问答</el-menu-item>
        <el-menu-item index="/knowledge">知识库</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/strategy">检索策略</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/model">模型配置</el-menu-item>
        <el-menu-item index="/feedback">
          反馈处理
          <el-badge v-if="pendingCount > 0" :value="pendingCount" style="margin-left: 10px" />
        </el-menu-item>
        <el-menu-item index="/dashboard">运营看板</el-menu-item>
        <el-menu-item index="/gaps">知识缺口</el-menu-item>
        <el-menu-item index="/evaluation">评估回归</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/cache">热点缓存</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/agent">深度问答</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/users">用户权限</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/departments">部门管理</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/logs">系统日志</el-menu-item>
      </el-menu>
      <div class="aside-footer">
        <button
          class="theme-toggle"
          type="button"
          :title="isDark ? '切换到浅色模式' : '切换到深色模式'"
          @click="isDark = !isDark"
        >
          <el-icon>
            <component :is="isDark ? Sunny : Moon" />
          </el-icon>
        </button>
      </div>
    </el-aside>
    <el-container>
      <el-header class="admin-header">
        <div class="page-title">{{ pageTitle }}</div>
        <div style="display: flex; align-items: center; gap: 12px">
          <span class="muted">{{ auth.displayName }}</span>
          <el-button link @click="gotoUser">用户端</el-button>
          <el-button link @click="passwordDialog = true">修改密码</el-button>
          <el-button link @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
    <PasswordDialog v-model="passwordDialog" />
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { logout as logoutApi } from '../api/auth'
import { adminFeedbackStats } from '../api/feedback'
import { Moon, Sunny } from '@element-plus/icons-vue'
import PasswordDialog from '../components/PasswordDialog.vue'
import { useTheme } from '../composables/useTheme'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { isDark } = useTheme()
const passwordDialog = ref(false)
const pendingCount = ref(0)

const activePath = computed(() => route.path)
const pageTitle = computed(() => route.meta.title || '')

async function logout() {
  try {
    await logoutApi()
  } catch {
    // 退出失败时仍然清理本地登录态
  }
  auth.clear()
  router.replace('/login')
}

function gotoUser() {
  window.open(import.meta.env.VITE_USER_URL || 'http://127.0.0.1:5175', '_blank')
}

onMounted(async () => {
  try {
    const stats = await adminFeedbackStats()
    pendingCount.value = stats.pending
  } catch {
    pendingCount.value = 0
  }
})
</script>

<style scoped>
.admin-aside {
  background: #1f2d3d;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.admin-menu {
  flex: 1;
  overflow-y: auto;
}

.brand {
  height: 56px;
  line-height: 56px;
  color: #fff;
  font-weight: 600;
  text-align: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.aside-footer {
  display: flex;
  justify-content: flex-start;
  padding: 10px 14px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.theme-toggle {
  width: 32px;
  height: 32px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #c0c4cc;
  font-size: 17px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color 0.2s, background-color 0.2s;
}

.theme-toggle:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

:deep(.el-menu) {
  border-right: none;
}
</style>
