import { create } from 'zustand'

// Agent 问答页局部状态：当前选中用于展示「决策轨迹」的回答、轨迹面板显隐。
export const useAgentStore = create((set) => ({
  selectedMessageId: null,
  traceVisible: true,
  setSelectedMessageId: (id) => set({ selectedMessageId: id }),
  toggleTrace: () => set((s) => ({ traceVisible: !s.traceVisible })),
  reset: () => set({ selectedMessageId: null, traceVisible: true }),
}))