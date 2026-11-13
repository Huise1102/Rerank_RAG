<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-title">校园 RAG 知识库系统</div>
      <div class="login-subtitle">{{ modeLabel }}</div>
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
        class="login-error"
      />
      <el-form :model="form" label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="请输入用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入密码"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="loading" @click="submit">登录</el-button>
      </el-form>
      <div v-if="adminMode" class="muted" style="margin-top: 16px; text-align: center">
        普通用户请访问用户端入口
      </div>
    </div>

    <el-dialog
      v-model="passwordDialog"
      title="首次登录需修改密码"
      width="420px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <el-form :model="passwordForm" label-position="top">
        <el-form-item label="新密码">
          <el-input v-model="passwordForm.newPassword" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="passwordForm.confirm" type="password" show-password />
        </el-form-item>
        <div class="muted">密码至少 8 位，需同时包含字母和数字</div>
      </el-form>
      <template #footer>
        <el-button type="primary" :loading="changing" @click="changePassword">保存并进入系统</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, changePassword as changePasswordApi } from '../api/auth'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const adminMode = import.meta.env.VITE_APP_MODE === 'admin'
const modeLabel = adminMode ? '管理端' : '用户端'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const errorMessage = ref('')
const passwordDialog = ref(false)
const changing = ref(false)
const passwordForm = reactive({ newPassword: '', confirm: '' })

watch(
  () => [form.username, form.password],
  () => {
    errorMessage.value = ''
  }
)

async function submit() {
  if (!form.username || !form.password) {
    errorMessage.value = '请输入用户名和密码'
    return
  }
  errorMessage.value = ''
  loading.value = true
  try {
    const data = await login({ username: form.username, password: form.password })
    if (adminMode && data.user.role !== 'admin') {
      errorMessage.value = '该账号无权登录管理端，请使用用户端登录'
      return
    }
    auth.setAuth(data.access_token, data.user)
    if (data.user.must_change_password) {
      passwordDialog.value = true
      return
    }
    await enter()
  } catch (error) {
    if (!error.response) {
      errorMessage.value = '无法连接后端服务，请确认后端已启动'
      return
    }
    const status = error.response.status
    const detail = error.response.data?.detail
    if (status === 429) {
      errorMessage.value = typeof detail === 'string' ? detail : '登录失败次数过多，请稍后重试'
    } else if (status === 401 || status === 403) {
      errorMessage.value = typeof detail === 'string' ? detail : '登录失败，请检查用户名和密码'
    } else if (status >= 500) {
      errorMessage.value = '后端服务未连接或异常，请确认后端已启动'
    } else {
      errorMessage.value = '登录失败，请稍后重试'
    }
  } finally {
    loading.value = false
  }
}

async function changePassword() {
  if (passwordForm.newPassword.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  if (passwordForm.newPassword !== passwordForm.confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  changing.value = true
  try {
    await changePasswordApi({
      old_password: form.password,
      new_password: passwordForm.newPassword
    })
    ElMessage.success('密码已修改')
    passwordDialog.value = false
    await auth.refresh()
    await enter()
  } finally {
    changing.value = false
  }
}

async function enter() {
  const redirect = route.query.redirect
  await router.replace(typeof redirect === 'string' ? redirect : '/chat')
}
</script>

<style scoped>
.login-error {
  margin-bottom: 16px;
}
</style>
