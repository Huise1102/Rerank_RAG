import { onMounted, onUnmounted, ref } from 'vue'
import {
  listNotifications,
  readAllNotifications,
  readNotification
} from '../api/notifications'

export function formatNotificationTime(value) {
  if (!value) {
    return ''
  }
  return String(value).replace('T', ' ').slice(0, 16)
}

export function useNotifications() {
  const notifications = ref([])
  const unread = ref(0)
  let timer = null

  async function loadNotifications() {
    try {
      const data = await listNotifications({ limit: 20 })
      notifications.value = data.items || []
      unread.value = data.unread || 0
    } catch {
      notifications.value = []
      unread.value = 0
    }
  }

  async function markAllRead() {
    try {
      await readAllNotifications()
      await loadNotifications()
    } catch {
      // 标记失败时保留当前列表
    }
  }

  async function readOne(item) {
    if (item.read) {
      return
    }
    try {
      await readNotification(item.id)
      item.read = true
      unread.value = Math.max(unread.value - 1, 0)
    } catch {
      // 已读失败不阻断跳转
    }
  }

  onMounted(() => {
    loadNotifications()
    timer = window.setInterval(loadNotifications, 60000)
  })

  onUnmounted(() => {
    if (timer) {
      window.clearInterval(timer)
    }
  })

  return { notifications, unread, loadNotifications, markAllRead, readOne }
}
