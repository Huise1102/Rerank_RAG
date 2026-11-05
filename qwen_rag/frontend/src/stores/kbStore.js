import { create } from 'zustand'
import { listKbs } from '../api/kb'

export const DEFAULT_STRATEGIES = { vector: true, bm25: true, fusion: true, rerank: true }

export function normalizeStrategies(raw) {
  if (raw && typeof raw === 'object') {
    return {
      vector: raw.vector !== false,
      bm25: raw.bm25 !== false,
      fusion: raw.fusion !== false,
      rerank: raw.rerank !== false,
    }
  }
  return { ...DEFAULT_STRATEGIES }
}

export const useKbStore = create((set, get) => ({
  kbs: [],
  loading: false,
  currentKbId: null,

  async fetchKbs() {
    set({ loading: true })
    try {
      const data = await listKbs()
      const kbs = Array.isArray(data) ? data : data?.items ?? []
      set({ kbs })
      get().ensureCurrentKb()
      return kbs
    } finally {
      set({ loading: false })
    }
  },

  // 仅在「已启用」知识库中选中；清空列表则置空
  ensureCurrentKb() {
    const { kbs, currentKbId } = get()
    const active = kbs.filter((k) => k.is_active)
    if (!active.length) {
      set({ currentKbId: null })
      return
    }
    if (currentKbId && active.some((k) => k.id === currentKbId)) return
    set({ currentKbId: active[0].id })
  },

  setCurrentKb(id) {
    set({ currentKbId: id })
  },

  // 删除了当前知识库后重新定位
  afterRemove(id) {
    if (get().currentKbId === id) {
      set({ currentKbId: null })
      get().ensureCurrentKb()
    }
  },
}))