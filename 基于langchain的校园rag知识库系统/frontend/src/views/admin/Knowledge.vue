<template>
  <div style="display: flex; height: 100%">
    <div style="width: 320px; border-right: 1px solid #e4e7ed; padding: 16px; overflow-y: auto">
      <div class="page-header" style="justify-content: flex-end">
        <el-button type="primary" size="small" @click="openKbDialog()">新建</el-button>
      </div>
      <div
        v-for="kb in kbList"
        :key="kb.id"
        class="kb-item"
        :class="{ active: kb.id === activeKbId }"
        @click="selectKb(kb)"
      >
        <div style="font-weight: 600">{{ kb.name }}</div>
        <div class="muted">{{ kb.description || '暂无描述' }}</div>
        <el-tag size="small" :type="kb.status === 'published' ? 'success' : 'info'" style="margin-top: 6px">
          {{ kb.status === 'published' ? '已发布' : '未发布' }}
        </el-tag>
      </div>
      <div v-if="kbList.length === 0" class="muted">暂无知识库</div>
    </div>

    <div style="flex: 1; padding: 16px; overflow-y: auto">
      <template v-if="activeKb">
        <div class="page-header">
          <div>
            <div class="page-title">{{ activeKb.name }}</div>
            <div class="muted">文档上传后需执行入库，重建会重新生成该知识库的向量与关键词索引</div>
          </div>
          <div>
            <el-button size="small" @click="openKbDialog(activeKb)">编辑</el-button>
            <el-button size="small" type="danger" @click="removeKb(activeKb)">删除</el-button>
            <el-button size="small" type="primary" :loading="rebuilding" @click="rebuild">重建索引</el-button>
          </div>
        </div>

        <el-upload
          :show-file-list="false"
          :http-request="uploadFile"
          accept=".pdf,.docx,.txt,.md"
          style="margin-bottom: 12px"
        >
          <el-button :loading="uploading">上传文档</el-button>
        </el-upload>

        <el-table :data="documents" size="small" border>
          <el-table-column prop="filename" label="文件名" show-overflow-tooltip />
          <el-table-column prop="file_type" label="类型" width="80" />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ (row.size_bytes / 1024).toFixed(1) }} KB</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="docStatusType(row.status)">{{ docStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="分块数" width="90" />
          <el-table-column prop="error_message" label="错误信息" show-overflow-tooltip />
          <el-table-column label="操作" width="170">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="ingest(row)">入库</el-button>
              <el-button link type="danger" size="small" @click="removeDocument(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="documents.length === 0" class="empty-hint">暂无文档，请先上传制度文件</div>
      </template>
      <el-empty v-else description="请选择或新建知识库" />
    </div>

    <el-dialog v-model="kbDialog" :title="editingKb ? '编辑知识库' : '新建知识库'" width="480px">
      <el-form :model="kbForm" label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="kbForm.name" maxlength="100" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="kbForm.description" type="textarea" :rows="3" maxlength="500" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="kbForm.status" style="width: 100%">
            <el-option label="已发布" value="published" />
            <el-option label="未发布" value="unpublished" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="kbDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingKb" @click="saveKb">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  adminKbList,
  createKb,
  deleteDocument,
  deleteKb,
  ingestDocument,
  listDocuments,
  rebuildKb,
  updateKb,
  uploadDocument
} from '../../api/knowledge'

const kbList = ref([])
const documents = ref([])
const activeKbId = ref(null)
const kbDialog = ref(false)
const editingKb = ref(null)
const savingKb = ref(false)
const uploading = ref(false)
const rebuilding = ref(false)
const kbForm = reactive({ name: '', description: '', status: 'published' })

const activeKb = computed(() => kbList.value.find((item) => item.id === activeKbId.value) || null)

function docStatusLabel(status) {
  return { pending: '待入库', ready: '已入库', failed: '失败' }[status] || status
}

function docStatusType(status) {
  return { pending: 'warning', ready: 'success', failed: 'danger' }[status] || 'info'
}

async function loadKbs() {
  kbList.value = await adminKbList()
  if (!activeKbId.value && kbList.value.length > 0) {
    activeKbId.value = kbList.value[0].id
    await loadDocuments()
  }
}

async function selectKb(kb) {
  activeKbId.value = kb.id
  await loadDocuments()
}

async function loadDocuments() {
  if (!activeKbId.value) {
    documents.value = []
    return
  }
  documents.value = await listDocuments(activeKbId.value)
}

function openKbDialog(kb = null) {
  editingKb.value = kb
  kbForm.name = kb?.name || ''
  kbForm.description = kb?.description || ''
  kbForm.status = kb?.status || 'published'
  kbDialog.value = true
}

async function saveKb() {
  if (!kbForm.name.trim()) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  savingKb.value = true
  try {
    if (editingKb.value) {
      await updateKb(editingKb.value.id, { ...kbForm })
      ElMessage.success('知识库已更新')
    } else {
      const created = await createKb({ ...kbForm })
      activeKbId.value = created.id
      ElMessage.success('知识库已创建')
    }
    kbDialog.value = false
    await loadKbs()
    await loadDocuments()
  } finally {
    savingKb.value = false
  }
}

async function removeKb(kb) {
  try {
    await ElMessageBox.confirm(`删除知识库「${kb.name}」会同时删除其文档与索引，确认继续？`, '删除知识库', {
      type: 'warning'
    })
  } catch {
    return
  }
  await deleteKb(kb.id)
  ElMessage.success('知识库已删除')
  activeKbId.value = null
  documents.value = []
  await loadKbs()
}

async function uploadFile({ file }) {
  if (!activeKbId.value) {
    ElMessage.warning('请先选择知识库')
    return
  }
  uploading.value = true
  try {
    await uploadDocument(activeKbId.value, file)
    ElMessage.success('上传成功，请点击「入库」生成索引')
    await loadDocuments()
  } finally {
    uploading.value = false
  }
}

async function ingest(row) {
  await ingestDocument(activeKbId.value, row.id)
  ElMessage.success('入库完成')
  await loadDocuments()
}

async function rebuild() {
  rebuilding.value = true
  try {
    await rebuildKb(activeKbId.value)
    ElMessage.success('索引重建完成')
    await loadDocuments()
  } finally {
    rebuilding.value = false
  }
}

async function removeDocument(row) {
  try {
    await ElMessageBox.confirm(`确认删除文档「${row.filename}」？`, '删除文档', { type: 'warning' })
  } catch {
    return
  }
  await deleteDocument(activeKbId.value, row.id)
  ElMessage.success('文档已删除')
  await loadDocuments()
}

onMounted(loadKbs)
</script>

<style scoped>
.kb-item {
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 8px;
  border: 1px solid transparent;
}
.kb-item:hover {
  background: #f5f7fa;
}
.kb-item.active {
  border-color: #1f6fd0;
  background: #ecf5ff;
}
</style>
