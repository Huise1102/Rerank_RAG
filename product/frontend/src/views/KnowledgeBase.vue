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
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="230" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row)">进入详情</el-button>
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

      <div class="upload-tip">
        支持 .pdf / .docx / .txt / .md；上传后系统自动解析并建立索引，状态每 3 秒自动刷新。
      </div>

      <div class="panel">
        <el-table :data="documents" v-loading="docsLoading" stripe style="width: 100%">
          <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
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
          <el-table-column label="操作" width="100" align="right">
            <template #default="{ row }">
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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbStore } from '@/stores/kb'
import { createKb, deleteKb } from '@/api/kb'
import { uploadDocument, listDocuments, deleteDocument } from '@/api/document'

const kbStore = useKbStore()

// 新建对话框
const createVisible = ref(false)
const creating = ref(false)
const createForm = ref({ name: '', description: '', chunk_strategy: 'recursive' })

// 详情视图
const detailKb = ref(null)
const documents = ref([])
const docsLoading = ref(false)
let pollTimer = null

const hasFailed = computed(() => documents.value.some((d) => d.status === 'failed'))

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
    await uploadDocument(detailKb.value.id, file)
    ElMessage.success(`「${file.name}」上传成功，正在解析入库`)
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
    await fetchDocuments()
  } catch (e) {
    /* 拦截器已提示 */
  }
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

.upload-tip {
  font-size: 12px;
  color: var(--app-text-muted);
  margin-bottom: 14px;
}

.error-note {
  margin-top: 12px;
  padding: 8px 12px;
  border: 1px solid #e6c9c9;
  background-color: #fbf3f3;
  border-radius: 4px;
  font-size: 12px;
  color: var(--app-danger);
}
</style>
