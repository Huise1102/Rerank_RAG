<template>
  <el-container class="app-shell">
    <el-aside :width="menuVisible ? '230px' : '0px'" class="app-aside" :class="{ 'is-collapsed': !menuVisible }">
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
        <div class="app-header__left">
          <el-button class="app-header__toggle" text @click="menuVisible = !menuVisible">
            <el-icon><Fold v-if="menuVisible" /><Expand v-else /></el-icon>
          </el-button>
          <div class="app-header__title">基于混合检索与Rerank的领域知识库系统</div>
        </div>
        <div class="app-status">
          <span class="app-status__dot" :class="status.cls"></span>
          <span>{{ status.text }}</span>
        </div>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 左下角：深色模式开关 -->
    <button class="theme-toggle" @click="toggleTheme">
      <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
      <span class="theme-toggle__text">{{ isDark ? '浅色模式' : '深色模式' }}</span>
    </button>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const activeMenu = computed(() => route.path)

//: 左侧菜单栏是否显示，默认显示
const menuVisible = ref(true)

//: 深色模式状态，初值取自 <html> 是否已有 .dark（main.js 已按 localStorage 预置）
const isDark = ref(document.documentElement.classList.contains('dark'))

function applyTheme(dark) {
  isDark.value = dark
  document.documentElement.classList.toggle('dark', dark)
}

function toggleTheme() {
  applyTheme(!isDark.value)
  try {
    localStorage.setItem('app-theme', isDark.value ? 'dark' : 'light')
  } catch (e) {
    /* 隐私模式下 localStorage 可能不可用，忽略即可 */
  }
}

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

<style scoped>
/* 菜单栏显隐：宽度由 el-aside 的 width 属性驱动，这里补过渡与收起态 */
.app-aside {
  transition: width 0.2s ease;
}

.app-aside.is-collapsed {
  border-right-color: transparent;
}

.app-header__left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.app-header__toggle {
  flex-shrink: 0;
  padding: 6px;
  font-size: 18px;
  color: var(--app-text-secondary);
}

.app-header__toggle:hover {
  color: var(--app-accent-dark);
  background-color: var(--app-accent-soft);
}

/* 左下角深色模式开关 */
.theme-toggle {
  position: fixed;
  left: 16px;
  bottom: 16px;
  z-index: 2000;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 13px;
  font-family: inherit;
  font-size: 13px;
  color: var(--app-text-regular);
  background-color: var(--app-surface);
  border: 1px solid var(--app-border);
  border-radius: 999px;
  box-shadow: 0 2px 10px rgba(90, 74, 50, 0.14);
  cursor: pointer;
  transition: color 0.18s, background-color 0.18s, border-color 0.18s, box-shadow 0.18s;
}

.theme-toggle:hover {
  color: var(--app-accent-dark);
  border-color: var(--app-accent);
  background-color: var(--app-accent-soft);
}

html.dark .theme-toggle {
  box-shadow: 0 0 0 1px rgba(0, 212, 170, 0.14), 0 4px 16px rgba(0, 0, 0, 0.5);
}

.theme-toggle__text {
  font-weight: 600;
}
</style>
