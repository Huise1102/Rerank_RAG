<template>
  <el-container class="app-shell">
    <el-aside width="230px" class="app-aside">
      <div class="app-brand">
        <div class="app-brand__title">基于混合检索与<br />Rerank 的领域知识库系统</div>
        <div class="app-brand__sub">混合检索 · 精排重排 · 引用溯源</div>
      </div>
      <el-menu class="app-menu" :default-active="activeMenu" router>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>问答</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Collection /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/strategy">
          <el-icon><Operation /></el-icon>
          <span>检索策略</span>
        </el-menu-item>
        <el-menu-item index="/logs">
          <el-icon><Tickets /></el-icon>
          <span>日志</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="app-header">
        <div class="app-header__title">基于混合检索与Rerank的领域知识库系统</div>
        <div class="app-status">
          <span class="app-status__dot" :class="status.cls"></span>
          <span>{{ status.text }}</span>
        </div>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const activeMenu = computed(() => route.path)

const health = ref(null)
const healthError = ref(false)

const status = computed(() => {
  if (healthError.value || !health.value) {
    return { text: '后端未连接', cls: 'is-bad' }
  }
  const raw = health.value.status
  if (raw === 'degraded') return { text: '服务降级', cls: 'is-warn' }
  if (raw === 'error') return { text: '服务异常', cls: 'is-bad' }
  if (health.value.warmed_up === false) return { text: '模型预热中', cls: 'is-warn' }
  return { text: '服务正常', cls: '' }
})

onMounted(async () => {
  try {
    const res = await fetch('/api/v1/health')
    if (!res.ok) throw new Error('bad status')
    health.value = await res.json()
  } catch (e) {
    healthError.value = true
  }
})
</script>
