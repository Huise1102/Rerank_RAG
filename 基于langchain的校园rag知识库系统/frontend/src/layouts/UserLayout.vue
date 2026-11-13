<template>
  <div class="user-shell">
    <div class="user-header">
      <div class="brand">校园制度问答</div>
      <div style="display: flex; align-items: center; gap: 16px">
        <router-link to="/chat" class="nav-item">问答</router-link>
        <router-link to="/feedback" class="nav-item">我的反馈</router-link>
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
      <router-view />
    </div>
    <PasswordDialog v-model="passwordDialog" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { logout as logoutApi } from '../api/auth'
import PasswordDialog from '../components/PasswordDialog.vue'

const auth = useAuthStore()
const router = useRouter()
const passwordDialog = ref(false)

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
</style>
