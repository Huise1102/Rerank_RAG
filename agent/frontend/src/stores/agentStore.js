import { create } from 'zustand'

// 问答页状态：当前查看轨迹的回答，以及轨迹面板的显隐。
export const useAgentStore = create((set) => ({
  selectedMessageId: null,
  traceVisible: true,
  setSelectedMessageId: (id) => set({ selectedMessageId: id }),
  toggleTrace: () => set((s) => ({ traceVisible: !s.traceVisible })),
  reset: () => set({ selectedMessageId: null, traceVisible: true }),
}))