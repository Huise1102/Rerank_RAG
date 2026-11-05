import { create } from 'zustand'
import {
  listConversations,
  listMessages,
  deleteConversation,
  clearConversations,
} from '../api/chat'

let seq = 0
export function nextMessageId() {
  seq += 1
  return `local-${Date.now()}-${seq}`
}

export function normalizeCitations(raw) {
  if (!Array.isArray(raw)) return []
  return raw.map((c) => ({
    index: c.index ?? c.id ?? null,
    source: c.source ?? '未知来源',
    page: c.page ?? null,
    score: c.score ?? null,
    count: c.count ?? 0,
    text: c.text ?? '',
  }))
}

export function normalizeDocs(raw) {
  return Array.isArray(raw) ? raw : []
}

export const useChatStore = create((set, get) => ({
  conversations: [],
  currentConversationId: null,
  messages: [],
  loadingConversations: false,
  loadingMessages: false,

  async fetchConversations(kbId) {
    set({ loadingConversations: true })
    try {
      const data = await listConversations(kbId)
      const conversations = Array.isArray(data) ? data : data?.items ?? []
      set({ conversations })
      return conversations
    } finally {
      set({ loadingConversations: false })
    }
  },

  async selectConversation(id) {
    set({ loadingMessages: true })
    try {
      const data = await listMessages(id)
      const rows = Array.isArray(data) ? data : data?.items ?? []
      set({
        currentConversationId: id,
        messages: rows.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content || '',
          citations: normalizeCitations(m.citations),
          retrieved_docs: normalizeDocs(m.retrieved_docs),
          latency: m.latency || {},
          strategy: m.strategy || '',
          stats: m.stats || null,
          // Agent 版：决策轨迹 / 轮数 / 澄清标记随 latency 落库，历史回看从这里拆出
          agent_trace: m.latency?.agent_trace || [],
          rounds: m.latency?.rounds || 0,
          is_clarification: m.latency?.is_clarification || false,
          streaming: false,
        })),
      })
    } finally {
      set({ loadingMessages: false })
    }
  },

  newConversation() {
    set({ currentConversationId: null, messages: [] })
  },

  setCurrentConversationId(id) {
    set({ currentConversationId: id })
  },

  async removeConversation(id) {
    await deleteConversation(id)
    const conversations = get().conversations.filter((c) => c.id !== id)
    const currentConversationId =
      get().currentConversationId === id ? null : get().currentConversationId
    const messages = get().currentConversationId === id ? [] : get().messages
    set({ conversations, currentConversationId, messages })
  },

  async clearAll(kbId) {
    await clearConversations(kbId)
    set({ conversations: [], currentConversationId: null, messages: [] })
  },

  reset() {
    set({ conversations: [], currentConversationId: null, messages: [] })
  },

  appendMessage(msg) {
    set((s) => ({ messages: [...s.messages, msg] }))
  },

  updateMessage(id, patch) {
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    }))
  },
}))