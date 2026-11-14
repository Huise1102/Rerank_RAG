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
      <template v-if="wecomEnabled">
        <el-divider>或</el-divider>
        <el-button
          class="wecom-button"
          style="width: 100%"
          :loading="wecomLoading"
          @click="loginWithWecom"
        >
          企业微信登录
        </el-button>
      </template>
      <template v-if="showQrEntry">
        <el-divider>手机访问</el-divider>
        <div class="mobile-entry">
          <img v-if="qrDataUrl" :src="qrDataUrl" alt="移动端访问二维码" class="mobile-qr" />
          <div class="muted">{{ qrHint }}</div>
          <div class="mobile-url">{{ mobileUrl }}</div>
        </div>
      </template>
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
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import QRCode from 'qrcode'
import {
  login,
  changePassword as changePasswordApi,
  getWecomStatus,
  getWecomAuthorizeUrl
} from '../api/auth'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const adminMode = import.meta.env.VITE_APP_MODE === 'admin'
const mobileMode = import.meta.env.VITE_APP_MODE === 'mobile'
const modeLabel = adminMode ? '管理端' : mobileMode ? '移动端' : '用户端'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const errorMessage = ref('')
const passwordDialog = ref(false)
const changing = ref(false)
const passwordForm = reactive({ newPassword: '', confirm: '' })
const wecomEnabled = ref(false)
const wecomLoading = ref(false)
const showQrEntry = !adminMode && !mobileMode
const isLoopbackHost = ['localhost', '127.0.0.1'].includes(window.location.hostname)
const mobileUrl = showQrEntry ? resolveMobileUrl() : ''
const qrHint = isLoopbackHost
  ? '本机地址生成的二维码仅本机可用，手机请改用局域网地址或配置 VITE_MOBILE_URL'
  : '扫码进入移动端，企业微信内可直接打开'
const qrDataUrl = ref('')

function resolveMobileUrl() {
  const configured = (import.meta.env.VITE_MOBILE_URL || '').trim()
  if (configured) return configured
  return `${window.location.protocol}//${window.location.hostname}:5176`
}

onMounted(async () => {
  if (showQrEntry && mobileUrl) {
    QRCode.toDataURL(mobileUrl, {
      width: 168,
      margin: 1,
      color: { dark: '#1f3b73', light: '#ffffff' }
    })
      .then((url) => {
        qrDataUrl.value = url
      })
      .catch(() => {
        qrDataUrl.value = ''
      })
  }
  if (adminMode) {
    return
  }
  try {
    const data = await getWecomStatus()
    wecomEnabled.value = Boolean(data.enabled)
  } catch {
    wecomEnabled.value = false
  }
})

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

async function loginWithWecom() {
  errorMessage.value = ''
  wecomLoading.value = true
  try {
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/chat'
    const data = await getWecomAuthorizeUrl(redirect)
    window.location.href = data.authorize_url
  } catch {
    errorMessage.value = '企业微信登录暂不可用，请稍后重试'
    wecomLoading.value = false
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

.mobile-entry {
  text-align: center;
}

.mobile-qr {
  width: 140px;
  height: 140px;
  margin: 4px auto 8px;
  display: block;
  border-radius: 8px;
}

.mobile-url {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  word-break: break-all;
}

@media (max-width: 767px) {
  .login-page {
    padding: 24px 16px;
  }

  .login-card {
    width: 100%;
    max-width: 420px;
    padding: 24px 20px;
  }
}
</style>
