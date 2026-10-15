<template>
  <div class="page">
    <!-- 列表视图 -->
    <template v-if="!detailKb">
      <div class="page-head">
        <div class="page-head__left">
          <div class="page-title">知识库</div>
          <div class="page-subtitle">管理知识库及其文档，支持 pdf / docx / txt / md 四种格式</div>
        </div>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          <span>新建知识库</span>
        </el-button>
      </div>

      <div class="panel">
        <el-table :data="kbStore.kbs" v-loading="kbStore.loading" stripe style="width: 100%">
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column prop="description" label="描述" min-width="220">
            <template #default="{ row }">
              <span class="muted">{{ row.description || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_strategy" label="分块策略" width="130">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.chunk_strategy || 'recursive' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="100" align="center">
            <template #default="{ row }">
              <el-switch
                v-model="row.is_active"
                :loading="togglingId === row.id"
                inline-prompt
                active-text="启用"
                inactive-text="停用"
                @change="(val) => toggleActive(row, val)"
              />
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="300" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row)">进入详情</el-button>
              <el-button link type="primary" @click="openEdit(row)">修改</el-button>
              <el-button link type="danger" @click="handleDeleteKb(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="table-empty">暂无知识库，点击右上角「新建知识库」创建</div>
          </template>
        </el-table>
      </div>
    </template>

    <!-- 详情视图：文档管理 -->
    <template v-else>
      <div class="page-head">
        <div class="page-head__left">
          <div class="detail-crumb">
            <el-button link @click="closeDetail">
              <el-icon><ArrowLeft /></el-icon>
              <span>返回列表</span>
            </el-button>
          </div>
          <div class="page-title">{{ detailKb.name }}</div>
          <div class="page-subtitle">
            分块策略 {{ detailKb.chunk_strategy || 'recursive' }} ·
            {{ detailKb.description || '暂无描述' }}
          </div>
        </div>
        <el-upload
          :show-file-list="false"
          :http-request="handleUpload"
          :before-upload="beforeUpload"
          accept=".pdf,.docx,.txt,.md"
          multiple
        >
          <el-button type="primary">
            <el-icon><UploadFilled /></el-icon>
            <span>上传文档</span>
          </el-button>
        </el-upload>
      </div>

      <div class="panel upload-panel">
        <el-upload
          drag
          multiple
          :show-file-list="false"
          :http-request="handleUpload"
          :before-upload="beforeUpload"
          accept=".pdf,.docx,.txt,.md"
        >
          <el-icon class="upload-panel__icon"><UploadFilled /></el-icon>
          <div class="upload-panel__text">将文档拖到此处，或<em>点击选择文件</em></div>
          <div class="upload-panel__sub">支持 .pdf / .docx / .txt / .md，可一次拖入多个文件</div>
        </el-upload>
        <div class="upload-tip">上传后系统自动解析并建立索引，处理状态每 3 秒自动刷新。</div>
      </div>

      <div class="panel">
        <el-table :data="documents" v-loading="docsLoading" stripe style="width: 100%">
          <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="file-link" @click="openPreview(row)">{{ row.filename }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="file_type" label="类型" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.file_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="110">
            <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="statusMeta(row.status).type" effect="light">
                {{ statusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="分块数" width="90" />
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openPreview(row)">查看</el-button>
              <el-button link type="danger" @click="handleDeleteDoc(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="table-empty">暂无文档，点击右上角「上传文档」添加</div>
          </template>
        </el-table>

        <div v-if="hasFailed" class="error-note">
          部分文档处理失败，可在「操作」列删除后重新上传。
        </div>
      </div>
    </template>

    <!-- 新建知识库对话框 -->
    <el-dialog v-model="createVisible" title="新建知识库" width="460px">
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" placeholder="如：法律领域" maxlength="100" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="3"
            placeholder="简要描述该知识库的领域与用途"
          />
        </el-form-item>
        <el-form-item label="分块策略">
          <el-select v-model="createForm.chunk_strategy" style="width: 100%">
            <el-option label="recursive（递归分块，推荐）" value="recursive" />
            <el-option label="fixed（固定长度分块）" value="fixed" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 修改知识库对话框 -->
    <el-dialog v-model="editVisible" title="修改知识库" width="460px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="editForm.name" placeholder="如：法律领域" maxlength="100" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="editForm.description"
            type="textarea"
            :rows="3"
            placeholder="简要描述该知识库的领域与用途"
          />
        </el-form-item>
        <el-form-item label="分块策略">
          <el-select v-model="editForm.chunk_strategy" style="width: 100%">
            <el-option label="recursive（递归分块，推荐）" value="recursive" />
            <el-option label="fixed（固定长度分块）" value="fixed" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="updating" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 文档预览对话框 -->
    <el-dialog
      v-model="previewVisible"
      :title="previewDoc ? previewDoc.filename : '文档预览'"
      width="960px"
      top="6vh"
      destroy-on-close
    >
      <div v-if="previewDoc" class="preview-meta">
        <el-tag size="small" effect="plain">{{ previewDoc.file_type }}</el-tag>
        <span>{{ formatSize(previewDoc.file_size) }}</span>
        <span>{{ formatTime(previewDoc.created_at) }}</span>
      </div>

      <div v-loading="previewLoading" class="preview-body">
        <iframe v-if="previewKind === 'pdf'" :src="previewUrl" class="preview-frame" title="PDF 预览" />
        <div
          v-else-if="previewKind === 'markdown'"
          class="preview-text markdown-body"
          v-html="previewHtml"
        ></div>
        <pre v-else-if="previewKind === 'text'" class="preview-text preview-pre">{{ previewText }}</pre>
        <div v-else class="preview-unsupported">
          <el-icon class="preview-unsupported__icon"><Document /></el-icon>
          <div>该格式（{{ previewDoc.file_type }}）暂不支持在线预览，请下载后用本地软件打开。</div>
        </div>
      </div>

      <template #footer>
        <el-button @click="openInNewTab">
          <el-icon><TopRight /></el-icon>
          <span>新窗口打开</span>
        </el-button>
        <el-button type="primary" @click="downloadDoc">
          <el-icon><Download /></el-icon>
          <span>下载</span>
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { marked } from 'marked'
import { useKbStore } from '@/stores/kb'
import { createKb, deleteKb, updateKb } from '@/api/kb'
import { uploadDocument, listDocuments, deleteDocument, documentFileUrl } from '@/api/document'

const kbStore = useKbStore()

// 新建对话框
const createVisible = ref(false)
const creating = ref(false)
const createForm = ref({ name: '', description: '', chunk_strategy: 'recursive' })

// 修改对话框
const editVisible = ref(false)
const updating = ref(false)
const editingKbId = ref(null)
const editForm = ref({ name: '', description: '', chunk_strategy: 'recursive' })

// 启用开关：记录正在提交的知识库 id
const togglingId = ref(null)

// 详情视图
const detailKb = ref(null)
const documents = ref([])
const docsLoading = ref(false)
let pollTimer = null

// 文档预览
const previewVisible = ref(false)
const previewDoc = ref(null)
const previewText = ref('')
const previewLoading = ref(false)

const hasFailed = computed(() => documents.value.some((d) => d.status === 'failed'))

//: 原始文档直链（走 Vite 代理 /api → 后端）
const previewUrl = computed(() =>
  previewDoc.value ? documentFileUrl(previewDoc.value.id) : ''
)
//: pdf 交给浏览器内置阅读器；md / txt 取回文本自行渲染；docx 无法在线预览
const previewKind = computed(() => {
  const type = previewDoc.value?.file_type
  if (type === 'pdf') return 'pdf'
  if (type === 'md') return 'markdown'
  if (type === 'txt') return 'text'
  return 'unsupported'
})
const previewHtml = computed(() => {
  try {
    return marked.parse(previewText.value || '', { gfm: true, breaks: true })
  } catch (e) {
    return ''
  }
})

function openCreate() {
  createForm.value = { name: '', description: '', chunk_strategy: 'recursive' }
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.value.name.trim()) {
    ElMessage.warning('请填写知识库名称')
    return
  }
  creating.value = true
  try {
    await createKb({
      name: createForm.value.name.trim(),
      description: createForm.value.description.trim(),
      chunk_strategy: createForm.value.chunk_strategy,
    })
    ElMessage.success('知识库创建成功')
    createVisible.value = false
    await kbStore.fetchKbs()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    creating.value = false
  }
}

function openEdit(row) {
  editingKbId.value = row.id
  editForm.value = {
    name: row.name || '',
    description: row.description || '',
    chunk_strategy: row.chunk_strategy || 'recursive',
  }
  editVisible.value = true
}

async function submitEdit() {
  if (!editForm.value.name.trim()) {
    ElMessage.warning('请填写知识库名称')
    return
  }
  updating.value = true
  try {
    await updateKb(editingKbId.value, {
      name: editForm.value.name.trim(),
      description: editForm.value.description.trim(),
      chunk_strategy: editForm.value.chunk_strategy,
    })
    ElMessage.success('知识库已更新')
    editVisible.value = false
    await kbStore.fetchKbs()
    if (detailKb.value && detailKb.value.id === editingKbId.value) {
      detailKb.value = { ...detailKb.value, ...editForm.value }
    }
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    updating.value = false
  }
}

async function toggleActive(row, val) {
  togglingId.value = row.id
  try {
    await updateKb(row.id, { is_active: val })
    ElMessage.success(val ? `已启用「${row.name}」` : `已停用「${row.name}」`)
    await kbStore.fetchKbs()
  } catch (e) {
    row.is_active = !val // 失败回滚开关状态
  } finally {
    togglingId.value = null
  }
}

async function handleDeleteKb(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除知识库「${row.name}」吗？其下的文档、向量索引与 BM25 索引都会被删除。`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch (e) {
    return
  }
  try {
    await deleteKb(row.id)
    ElMessage.success('已删除知识库')
    await kbStore.fetchKbs()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function openDetail(kb) {
  detailKb.value = kb
  await fetchDocuments()
  startPollingIfNeeded()
}

function closeDetail() {
  stopPolling()
  detailKb.value = null
  documents.value = []
}

async function fetchDocuments() {
  if (!detailKb.value) return
  docsLoading.value = true
  try {
    const data = await listDocuments(detailKb.value.id)
    documents.value = Array.isArray(data) ? data : (data?.items ?? [])
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    docsLoading.value = false
  }
}

function isProcessing(status) {
  return ['pending', 'parsing', 'indexing'].includes(status)
}

function startPollingIfNeeded() {
  if (documents.value.some((d) => isProcessing(d.status))) {
    startPolling()
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    await fetchDocuments()
    if (!documents.value.some((d) => isProcessing(d.status))) {
      stopPolling()
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function beforeUpload(file) {
  const allowed = ['.pdf', '.docx', '.txt', '.md']
  const name = file.name.toLowerCase()
  const ok = allowed.some((ext) => name.endsWith(ext))
  if (!ok) {
    ElMessage.error('仅支持 .pdf / .docx / .txt / .md 四种格式')
    return false
  }
  return true
}

async function handleUpload(option) {
  const file = option.file
  try {
    const doc = await uploadDocument(detailKb.value.id, file)
    ElMessage.success(`「${file.name}」上传成功，正在解析入库`)
    // 后端落库后即返回，这里先把新行乐观插入表格（状态 pending），无需等下一次轮询
    if (doc && doc.id) {
      documents.value = [doc, ...documents.value.filter((d) => d.id !== doc.id)]
    }
    await fetchDocuments()
    startPolling()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function handleDeleteDoc(row) {
  try {
    await ElMessageBox.confirm(`确定删除文档「${row.filename}」吗？`, '删除文档', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch (e) {
    return
  }
  try {
    await deleteDocument(row.id)
    ElMessage.success('已删除文档')
    // 后端只删库记录后立即返回（索引清理在后台），这里立刻把行移出表格
    documents.value = documents.value.filter((d) => d.id !== row.id)
    await fetchDocuments()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

// ------------------------------- 文档预览 ---------------------------------- //

async function openPreview(row) {
  previewDoc.value = row
  previewText.value = ''
  previewVisible.value = true
  const kind = previewKind.value
  // pdf 交给浏览器内置阅读器；txt / md 需要取回文本自行渲染
  if (kind !== 'text' && kind !== 'markdown') return
  previewLoading.value = true
  try {
    const resp = await fetch(previewUrl.value)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    previewText.value = await resp.text()
  } catch (e) {
    previewText.value = ''
    ElMessage.error('文档内容读取失败，可尝试下载后查看')
  } finally {
    previewLoading.value = false
  }
}

function openInNewTab() {
  if (previewUrl.value) window.open(previewUrl.value, '_blank')
}

function downloadDoc() {
  if (!previewDoc.value) return
  const a = document.createElement('a')
  a.href = documentFileUrl(previewDoc.value.id, true)
  a.download = previewDoc.value.filename || ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function statusMeta(status) {
  const map = {
    pending: { label: '待处理', type: 'info' },
    parsing: { label: '解析中', type: 'warning' },
    indexing: { label: '索引中', type: 'warning' },
    completed: { label: '已完成', type: 'success' },
    failed: { label: '失败', type: 'danger' },
  }
  return map[status] || { label: status || '未知', type: 'info' }
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined || bytes === '') return '—'
  const n = Number(bytes)
  if (Number.isNaN(n)) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').replace(/\.\d+$/, '').slice(0, 19)
}

// 直接访问/刷新该页时也要拉取列表，避免依赖从其他页面带入的 store 数据
onMounted(() => {
  kbStore.fetchKbs()
})

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.muted {
  color: var(--app-text-muted);
}

.table-empty {
  padding: 28px 0;
  color: var(--app-text-muted);
  font-size: 13px;
}

.detail-crumb {
  margin-bottom: 4px;
}

/* 上传：可拖动区域 */
.upload-panel :deep(.el-upload) {
  width: 100%;
}

.upload-panel :deep(.el-upload-dragger) {
  width: 100%;
  padding: 24px 20px;
  border-radius: 6px;
}

.upload-panel__icon {
  font-size: 34px;
  color: var(--app-accent);
  margin-bottom: 6px;
}

.upload-panel__text {
  font-size: 14px;
  color: var(--app-text-regular);
}

.upload-panel__text em {
  font-style: normal;
  font-weight: 600;
  color: var(--app-accent-dark);
}

.upload-panel__sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-text-muted);
}

.upload-tip {
  margin-top: 10px;
  font-size: 12px;
  color: var(--app-text-muted);
}

/* 文件名：点击查看 */
.file-link {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--app-accent-dark);
  cursor: pointer;
}

.file-link:hover {
  text-decoration: underline;
}

/* 文档预览 */
.preview-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--app-text-muted);
}

.preview-body {
  height: 62vh;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  overflow: auto;
  background-color: var(--app-surface-sunken);
}

.preview-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.preview-text {
  min-height: 100%;
  margin: 0;
  padding: 18px 22px;
  background-color: var(--app-surface);
}

.preview-pre {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-text);
  white-space: pre-wrap;
  word-break: break-word;
}

.preview-unsupported {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 100%;
  padding: 0 48px;
  text-align: center;
  font-size: 13px;
  color: var(--app-text-muted);
}

.preview-unsupported__icon {
  font-size: 40px;
  color: var(--app-accent);
}

.error-note {
  margin-top: 12px;
  padding: 8px 12px;
  border: 1px solid var(--app-danger-soft-border);
  background-color: var(--app-danger-soft-bg);
  border-radius: 4px;
  font-size: 12px;
  color: var(--app-danger);
}
</style>
