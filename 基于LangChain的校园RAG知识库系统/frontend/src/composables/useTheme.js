import { ref, watch } from 'vue'

const STORAGE_KEY = 'campus-rag-theme'
const isDark = ref(false)
let initialized = false

function preferredDark() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'dark') return true
  if (saved === 'light') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

function apply(dark) {
  document.documentElement.classList.toggle('dark', dark)
}

// 首次调用时读取本地偏好并同步根节点主题类，后续调用共享同一状态
export function useTheme() {
  if (!initialized) {
    initialized = true
    isDark.value = preferredDark()
    apply(isDark.value)
    watch(isDark, (value) => {
      apply(value)
      localStorage.setItem(STORAGE_KEY, value ? 'dark' : 'light')
    })
  }
  return { isDark }
}
