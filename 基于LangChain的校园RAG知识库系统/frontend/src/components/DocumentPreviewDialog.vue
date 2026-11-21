<template>
  <el-dialog
    :model-value="modelValue"
    :title="document ? document.title || document.filename : '文档查看'"
    :width="mobile ? '100%' : '78%'"
    :fullscreen="mobile"
    top="5vh"
    destroy-on-close
    class="doc-preview-dialog"
    @update:model-value="(value) => emit('update:modelValue', value)"
    @closed="cleanup"
  >
    <div v-if="document" class="doc-preview-meta">
      <el-tag size="small" effect="plain">{{ (document.file_type || '').toUpperCase() }}</el-tag>
      <el-tag v-if="document.version_label" size="small" type="info">
        {{ document.version_label }}
      </el-tag>
      <span v-if="document.doc_no">{{ document.doc_no }}</span>
      <span v-if="document.effective_date">生效 {{ formatDate(document.effective_date) }}</span>
      <span>{{ formatSize(document.size_bytes) }}</span>
    </div>

    <div v-loading="loading" class="doc-preview-body">
      <div v-if="error" class="doc-preview-state">
        <p>{{ error }}</p>
        <el-button size="small" @click="load">重新加载</el-button>
      </div>
      <iframe
        v-else-if="kind === 'pdf'"
        :src="blobUrl"
        class="doc-preview-frame"
        title="PDF 预览"
      />
      <div
        v-else-if="kind === 'markdown'"
        class="doc-preview-text answer-body"
        v-html="markdownHtml"
      ></div>
      <pre v-else-if="kind === 'text'" class="doc-preview-text doc-preview-pre">{{ textContent }}</pre>
      <div v-else-if="!loading" class="doc-preview-state">
        <p>该格式暂不支持在线预览，可下载后用本地软件打开。</p>
        <el-button type="primary" size="small" @click="download">下载原文</el-button>
      </div>
    </div>

    <template #footer>
      <el-button @click="close">关闭</el-button>
      <el-button type="primary" :disabled="!blobData" @click="download">下载原文</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { previewVisibleDocument } from '../api/knowledge'
import { formatDate, formatSize } from '../composables/useKnowledgeBase'

marked.setOptions({ gfm: true, breaks: true })

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  document: { type: Object, default: null },
  mobile: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const loading = ref(false)
const error = ref('')
const kind = ref('')
const blobUrl = ref('')
const blobData = ref(null)
const textContent = ref('')

const markdownHtml = computed(() => {
  try {
    return DOMPurify.sanitize(marked.parse(textContent.value || ''))
  } catch (e) {
    return ''
  }
})

function revokeUrl() {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
    blobUrl.value = ''
  }
}

function cleanup() {
  revokeUrl()
  blobData.value = null
  textContent.value = ''
  kind.value = ''
  error.value = ''
  loading.value = false
}

async function load() {
  if (!props.document?.id) {
    return
  }
  const currentId = props.document.id
  cleanup()
  loading.value = true
  try {
    const blob = await previewVisibleDocument(currentId)
    if (!props.modelValue || props.document?.id !== currentId) {
      return
    }
    blobData.value = blob
    const fileType = String(props.document.file_type || '').toLowerCase()
    if (fileType === 'pdf') {
      blobUrl.value = URL.createObjectURL(
        new Blob([blob], { type: 'application/pdf' })
      )
      kind.value = 'pdf'
    } else if (fileType === 'md') {
      textContent.value = await blob.text()
      kind.value = 'markdown'
    } else if (fileType === 'txt') {
      textContent.value = await blob.text()
      kind.value = 'text'
    } else {
      kind.value = 'unsupported'
    }
  } catch (e) {
    if (props.modelValue && props.document?.id === currentId) {
      error.value = '文档加载失败，可能无查看权限或文件不存在'
    }
  } finally {
    if (props.document?.id === currentId) {
      loading.value = false
    }
  }
}

function download() {
  if (!blobData.value) {
    return
  }
  const url = URL.createObjectURL(blobData.value)
  const link = document.createElement('a')
  link.href = url
  link.download = props.document?.filename || props.document?.title || 'document'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function close() {
  emit('update:modelValue', false)
}

watch(
  [() => props.modelValue, () => props.document?.id],
  ([visible]) => {
    if (visible) {
      load()
    }
  }
)
</script>

<style scoped>
.doc-preview-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
  color: #606266;
  font-size: 13px;
}

.doc-preview-body {
  min-height: 160px;
}

.doc-preview-frame {
  width: 100%;
  height: 68vh;
  border: none;
  border-radius: 6px;
  background: #f5f7fa;
}

.doc-preview-text {
  max-height: 68vh;
  overflow-y: auto;
  padding: 16px 18px;
  border-radius: 6px;
  background: #f7f8fa;
  line-height: 1.8;
  font-size: 14px;
}

.doc-preview-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

.doc-preview-state {
  padding: 48px 16px;
  text-align: center;
  color: #909399;
}

.doc-preview-state p {
  margin: 0 0 14px;
}
</style>
