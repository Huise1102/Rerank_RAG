import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listKbs } from '@/api/kb'

// 默认全开：向量 + BM25 + 融合 + 精排
export const DEFAULT_STRATEGIES = { vector: true, bm25: true, fusion: true, rerank: true }

// 预置知识库名称（种子数据）
export const PRESET_KB_NAME = '法律领域'

export function normalizeStrategies(raw) {
  return {
    vector: !!(raw && raw.vector),
    bm25: !!(raw && raw.bm25),
    fusion: !!(raw && raw.fusion),
    rerank: !!(raw && raw.rerank),
  }
}

export const useKbStore = defineStore('kb', () => {
  const kbs = ref([])
  const currentKbId = ref(null)
  const loading = ref(false)

  const currentKb = computed(() => kbs.value.find((k) => k.id === currentKbId.value) || null)

  const currentStrategies = computed(() =>
    normalizeStrategies(currentKb.value?.enabled_strategies || DEFAULT_STRATEGIES)
  )

  async function fetchKbs() {
    loading.value = true
    try {
      const data = await listKbs()
      kbs.value = Array.isArray(data) ? data : (data?.items ?? [])
      ensureCurrentKb()
      return kbs.value
    } finally {
      loading.value = false
    }
  }

  // 保证当前选中项有效：优先「法律领域」，否则第一项
  function ensureCurrentKb() {
    if (!kbs.value.length) {
      currentKbId.value = null
      return
    }
    if (currentKbId.value && kbs.value.some((k) => k.id === currentKbId.value)) return
    const preset = kbs.value.find((k) => k.name === PRESET_KB_NAME)
    currentKbId.value = preset ? preset.id : kbs.value[0].id
  }

  function setCurrentKb(id) {
    currentKbId.value = id
  }

  function kbName(id) {
    const kb = kbs.value.find((k) => k.id === id)
    return kb ? kb.name : id == null ? '-' : `#${id}`
  }

  return {
    kbs,
    currentKbId,
    currentKb,
    currentStrategies,
    loading,
    fetchKbs,
    ensureCurrentKb,
    setCurrentKb,
    kbName,
  }
})
