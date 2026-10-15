import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listConversations, listMessages, deleteConversation, clearConversations } from '@/api/chat'

// 引用来源：兼容数组或 { citations: [...] } 两种返回形态
export function normalizeCitations(raw) {
  if (Array.isArray(raw)) return raw
  if (raw && Array.isArray(raw.citations)) return raw.citations
  return []
}

// 检索文档块：兼容数组或 { results / retrieved_docs: [...] }
export function normalizeDocs(raw) {
  if (Array.isArray(raw)) return raw
  if (raw && Array.isArray(raw.retrieved_docs)) return raw.retrieved_docs
  if (raw && Array.isArray(raw.results)) return raw.results
  return []
}

let messageSeq = 0
export function nextMessageId() {
  messageSeq += 1
  return `m-${Date.now()}-${messageSeq}`
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref([])
  const currentConversationId = ref(null)
  const messages = ref([])
  const loadingConversations = ref(false)

  async function fetchConversations(kbId) {
    if (kbId == null) {
      conversations.value = []
      return
    }
    loadingConversations.value = true
    try {
      const data = await listConversations(kbId)
      conversations.value = Array.isArray(data) ? data : (data?.items ?? [])
    } finally {
      loadingConversations.value = false
    }
  }

  async function selectConversation(conversationId) {
    currentConversationId.value = conversationId
    const data = await listMessages(conversationId)
    const list = Array.isArray(data) ? data : (data?.items ?? [])
    messages.value = list.map((m) => ({
      id: nextMessageId(),
      role: m.role,
      content: m.content || '',
      citations: normalizeCitations(m.citations),
      retrieved_docs: normalizeDocs(m.retrieved_docs),
      latency: m.latency || {},
      strategy: m.strategy || '',
      stats: m.stats || null,
      streaming: false,
    }))
  }

  function newConversation() {
    currentConversationId.value = null
    messages.value = []
  }

  async function removeConversation(conversationId) {
    await deleteConversation(conversationId)
    conversations.value = conversations.value.filter((c) => c.id !== conversationId)
    if (currentConversationId.value === conversationId) {
      newConversation()
    }
  }

  // 清空会话列表：清空后回到"新建会话"状态，避免仍停留在已删会话上
  async function clearAllConversations(kbId) {
    const data = await clearConversations(kbId)
    conversations.value = []
    newConversation()
    return data?.deleted ?? 0
  }

  function reset() {
    conversations.value = []
    currentConversationId.value = null
    messages.value = []
  }

  return {
    conversations,
    currentConversationId,
    messages,
    loadingConversations,
    fetchConversations,
    selectConversation,
    newConversation,
    removeConversation,
    clearAllConversations,
    reset,
  }
})
