<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-title">校园 RAG 知识库系统</div>
      <div class="login-subtitle">企业微信登录</div>
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
      />
      <div v-else class="wecom-tip">{{ tip }}</div>
      <el-button
        v-if="errorMessage"
        type="primary"
        style="width: 100%; margin-top: 16px"
        @click="backToLogin"
      >
        返回登录页
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { wecomLogin } from '../api/auth'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const errorMessage = ref('')
const tip = ref('正在验证企业微信身份，请稍候')

onMounted(async () => {
  const code = typeof route.query.code === 'string' ? route.query.code : ''
  const state = typeof route.query.state === 'string' ? route.query.state : ''
  if (!code || !state) {
    errorMessage.value = '登录参数缺失，请返回登录页重新发起企业微信登录'
    return
  }
  try {
    const data = await wecomLogin({ code, state })
    auth.setAuth(data.access_token, data.user)
    const redirect = data.redirect || '/chat'
    await router.replace(redirect)
  } catch (error) {
    const detail = error.response?.data?.detail
    errorMessage.value = detail ? String(detail) : '企业微信登录失败，请返回登录页重试'
  }
})

async function backToLogin() {
  await router.replace('/login')
}
</script>

<style scoped>
.wecom-tip {
  color: #606266;
  text-align: center;
  padding: 8px 0;
}
</style>
