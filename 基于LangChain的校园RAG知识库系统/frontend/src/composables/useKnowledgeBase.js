import { computed, ref } from 'vue'
import {
  getVisibleKb,
  listVisibleKbDocuments,
  listVisibleKbs
} from '../api/knowledge'

const VISIBILITY_LABELS = {
  public: '全校公开',
  department: '部门内部',
  admin_only: '仅管理员'
}

const STRATEGY_LABELS = {
  clause: '条款优先',
  recursive: '递归长度',
  fixed: '固定长度'
}

export function visibilityLabel(value) {
  return VISIBILITY_LABELS[value] || value || '-'
}

export function strategyLabel(value) {
  return STRATEGY_LABELS[value] || value || '-'
}

export function formatDate(value) {
  if (!value) {
    return '-'
  }
  return String(value).replace('T', ' ').slice(0, 10)
}

export function formatDateTime(value) {
  if (!value) {
    return '-'
  }
  return String(value).replace('T', ' ').slice(0, 16)
}

export function formatSize(bytes) {
  const size = Number(bytes || 0)
  if (size <= 0) {
    return '-'
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 用户端知识库页面共用的数据逻辑。
 * 只调用用户可读接口，不包含任何新增、修改、删除操作。
 */
export function useKnowledgeBase() {
  const kbs = ref([])
  const activeKbId = ref(null)
  const detail = ref(null)
  const documents = ref([])
  const loading = ref(false)
  const detailLoading = ref(false)
  const error = ref('')
  const previewVisible = ref(false)
  const previewDocument = ref(null)

  const activeKb = computed(
    () => kbs.value.find((item) => item.id === activeKbId.value) || null
  )

  async function selectKb(id) {
    if (!id) {
      activeKbId.value = null
      detail.value = null
      documents.value = []
      return
    }
    activeKbId.value = id
    detailLoading.value = true
    error.value = ''
    try {
      const [kbDetail, kbDocuments] = await Promise.all([
        getVisibleKb(id),
        listVisibleKbDocuments(id)
      ])
      // 快速切换知识库时丢弃过期响应
      if (activeKbId.value !== id) {
        return
      }
      detail.value = kbDetail
      documents.value = Array.isArray(kbDocuments) ? kbDocuments : []
    } catch (e) {
      if (activeKbId.value === id) {
        detail.value = null
        documents.value = []
        error.value = '知识库详情加载失败，请稍后重试'
      }
    } finally {
      if (activeKbId.value === id) {
        detailLoading.value = false
      }
    }
  }

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const list = await listVisibleKbs()
      kbs.value = Array.isArray(list) ? list : []
      if (kbs.value.length === 0) {
        activeKbId.value = null
        detail.value = null
        documents.value = []
        return
      }
      const stillVisible = kbs.value.some((item) => item.id === activeKbId.value)
      const targetId = stillVisible ? activeKbId.value : kbs.value[0].id
      await selectKb(targetId)
    } catch (e) {
      kbs.value = []
      activeKbId.value = null
      detail.value = null
      documents.value = []
      error.value = '知识库目录加载失败，请稍后重试'
    } finally {
      loading.value = false
    }
  }

  function openPreview(document) {
    if (!document) {
      return
    }
    previewDocument.value = document
    previewVisible.value = true
  }

  return {
    kbs,
    activeKb,
    activeKbId,
    detail,
    documents,
    loading,
    detailLoading,
    error,
    previewVisible,
    previewDocument,
    load,
    selectKb,
    openPreview
  }
}
