<template>
  <el-container style="height: 100%">
    <el-aside width="210px" style="background: #1f2d3d">
      <div class="brand">校园 RAG 管理端</div>
      <el-menu
        :default-active="activePath"
        background-color="#1f2d3d"
        text-color="#c0c4cc"
        active-text-color="#ffffff"
        router
      >
        <el-menu-item index="/chat">问答</el-menu-item>
        <el-menu-item index="/knowledge">知识库</el-menu-item>
        <el-menu-item index="/strategy">检索策略</el-menu-item>
        <el-menu-item index="/model">模型配置</el-menu-item>
        <el-menu-item index="/logs">日志</el-menu-item>
        <el-menu-item index="/feedback">
          反馈处理
          <el-badge v-if="pendingCount > 0" :value="pendingCount" style="margin-left: 10px" />
        </el-menu-item>
        <el-menu-item index="/users">用户与权限</el-menu-item>
      </el-menu>
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
import PasswordDialog from '../components/PasswordDialog.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
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
.brand {
  height: 56px;
  line-height: 56px;
  color: #fff;
  font-weight: 600;
  text-align: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
:deep(.el-menu) {
  border-right: none;
}
</style>
