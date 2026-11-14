<template>
  <div class="knowledge-layout">
    <div class="kb-sidebar">
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
        <div style="margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap">
          <el-tag size="small" :type="kbStatusType(kb.status)">{{ kbStatusLabel(kb.status) }}</el-tag>
          <el-tag size="small" :type="kb.visibility === 'public' ? 'success' : 'warning'">
            {{ kb.visibility === 'public' ? '全校公开' : '部门内部' }}
          </el-tag>
          <el-tag size="small" type="info">{{ strategyLabel(kb.chunk_strategy) }}</el-tag>
        </div>
      </div>
      <div v-if="kbList.length === 0" class="muted">暂无知识库</div>
    </div>

    <div class="kb-main">
      <template v-if="activeKb">
        <div class="page-header">
          <div>
            <div class="kb-name">{{ activeKb.name }}</div>
            <div class="muted">文档需先解析生成索引，再发布；发布后旧版本自动废止，问答只召回当前有效版本</div>
          </div>
          <div>
            <el-button size="small" @click="openKbDialog(activeKb)">编辑</el-button>
            <el-button size="small" type="danger" @click="removeKb(activeKb)">删除</el-button>
            <el-button size="small" type="primary" :loading="rebuilding" @click="rebuild">重建索引</el-button>
            <el-button size="small" type="success" @click="openUploadDialog">上传文档</el-button>
          </div>
        </div>

        <div style="display: flex; gap: 8px; margin-bottom: 12px">
          <el-input v-model="filters.keyword" placeholder="标题或文号" clearable style="width: 220px" @keyup.enter="loadDocuments" />
          <el-select v-model="filters.visibility" placeholder="可见性" clearable style="width: 140px" @change="loadDocuments">
            <el-option label="公开" value="public" />
            <el-option label="部门内部" value="department" />
            <el-option label="仅管理员" value="admin_only" />
          </el-select>
          <el-select v-model="filters.lifecycle" placeholder="生命周期" clearable style="width: 140px" @change="loadDocuments">
            <el-option label="草稿" value="draft" />
            <el-option label="已发布" value="published" />
            <el-option label="已废止" value="repealed" />
            <el-option label="已归档" value="archived" />
          </el-select>
          <el-button type="primary" @click="loadDocuments">查询</el-button>
        </div>

        <el-table :data="documents" size="small" border max-height="520">
          <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="doc_no" label="文号" width="140" show-overflow-tooltip />
          <el-table-column label="可见性" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="visibilityType(row.visibility)">{{ visibilityLabel(row.visibility) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="归属部门" width="110">
            <template #default="{ row }">{{ row.department_name || '全校' }}</template>
          </el-table-column>
          <el-table-column label="当前版本" width="100">
            <template #default="{ row }">
              <div v-if="row.version">{{ row.version.version_label }}</div>
              <span v-else class="muted">无</span>
            </template>
          </el-table-column>
          <el-table-column label="生命周期" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.version" size="small" :type="lifecycleType(row.version.lifecycle_status)">
                {{ lifecycleLabel(row.version.lifecycle_status) }}
              </el-tag>
              <span v-else class="muted">未发布</span>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="分块" width="80" />
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDetail(row)">版本</el-button>
              <el-button link type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="removeDocument(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="documents.length === 0" class="empty-hint">暂无文档，请先上传知识文档</div>
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          style="margin-top: 12px"
          @current-change="loadDocuments"
        />
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
        <el-form-item label="可见性" required>
          <el-select v-model="kbForm.visibility" style="width: 100%" @change="onVisibilityChange">
            <el-option label="部门内部" value="department" />
            <el-option v-if="isAdmin" label="全校公开" value="public" />
          </el-select>
        </el-form-item>
        <el-form-item label="归属部门" :required="kbForm.visibility === 'department'">
          <el-select
            v-model="kbForm.department_id"
            :disabled="kbForm.visibility === 'public' || !isAdmin"
            clearable
            style="width: 100%"
          >
            <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="责任人账号 ID">
          <el-input-number v-model="kbForm.owner_user_id" :min="1" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="复核周期（天）">
          <el-input-number v-model="kbForm.review_cycle_days" :min="1" :max="3650" style="width: 100%" />
        </el-form-item>
        <el-form-item label="分块策略">
          <el-select v-model="kbForm.chunk_strategy" style="width: 100%">
            <el-option label="条款优先" value="clause" />
            <el-option label="递归长度" value="recursive" />
            <el-option label="固定长度" value="fixed" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="kbForm.status" style="width: 100%">
            <el-option label="已发布" value="published" />
            <el-option label="未发布" value="unpublished" />
            <el-option label="已停用" value="disabled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="kbDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingKb" @click="saveKb">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="uploadDialog" title="上传文档" width="520px">
      <el-form :model="uploadForm" label-position="top">
        <el-form-item label="文件" required>
          <el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
            :file-list="fileList"
            accept=".pdf,.docx,.txt,.md"
          >
            <el-button>选择文件</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="uploadForm.title" placeholder="留空则使用文件名" maxlength="255" />
        </el-form-item>
        <el-form-item label="文号">
          <el-input v-model="uploadForm.doc_no" maxlength="100" />
        </el-form-item>
        <el-form-item label="归属部门">
          <el-select v-model="uploadForm.department_id" clearable style="width: 100%">
            <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="可见性" required>
          <el-select v-model="uploadForm.visibility" style="width: 100%">
            <el-option label="公开" value="public" />
            <el-option label="部门内部" value="department" />
            <el-option v-if="isAdmin" label="仅管理员" value="admin_only" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本号">
          <el-input v-model="uploadForm.version_label" maxlength="50" />
        </el-form-item>
        <el-form-item label="生效日期">
          <el-date-picker v-model="uploadForm.effective_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="复核日期">
          <el-date-picker v-model="uploadForm.review_due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传并解析</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="文档版本" size="760px">
      <div v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="标题">{{ detail.title }}</el-descriptions-item>
          <el-descriptions-item label="文号">{{ detail.doc_no || '未填写' }}</el-descriptions-item>
          <el-descriptions-item label="可见性">{{ visibilityLabel(detail.visibility) }}</el-descriptions-item>
          <el-descriptions-item label="归属部门">{{ detail.department_name || '全校' }}</el-descriptions-item>
          <el-descriptions-item label="责任人">{{ detail.owner_name || '未设置' }}</el-descriptions-item>
          <el-descriptions-item label="复核日期">{{ detail.review_due_date || '未设置' }}</el-descriptions-item>
        </el-descriptions>

        <div style="margin: 16px 0; display: flex; gap: 8px">
          <el-button size="small" @click="openVersionUpload(detail)">上传新版本</el-button>
          <el-button size="small" type="warning" @click="archiveDocument(detail)">归档文档</el-button>
        </div>

        <el-table :data="detail.versions" size="small" border>
          <el-table-column prop="version_label" label="版本" width="100" />
          <el-table-column label="生命周期" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="lifecycleType(row.lifecycle_status)">{{ lifecycleLabel(row.lifecycle_status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="处理状态" width="100">
            <template #default="{ row }">{{ processLabel(row.process_status) }}</template>
          </el-table-column>
          <el-table-column prop="effective_date" label="生效日期" width="110" />
          <el-table-column prop="chunk_count" label="分块" width="70" />
          <el-table-column label="操作" min-width="260">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="parseVersion(row)">解析</el-button>
              <el-button
                v-if="row.process_status === 'completed'"
                link
                type="success"
                size="small"
                @click="publishVersion(row)"
              >
                发布
              </el-button>
              <el-button
                v-if="row.lifecycle_status === 'published'"
                link
                type="danger"
                size="small"
                @click="repealVersion(row)"
              >
                废止
              </el-button>
              <el-button
                v-if="row.lifecycle_status === 'repealed' && isAdmin"
                link
                type="warning"
                size="small"
                @click="rollbackVersion(row)"
              >
                回滚
              </el-button>
              <el-button link size="small" @click="previewVersion(row)">预览</el-button>
            </template>
          </el-table-column>
        </el-table>

        <h4>最近一次入库报告</h4>
        <p class="report-block">{{ latestReport }}</p>
      </div>
    </el-drawer>

    <el-dialog v-model="editDialog" title="编辑文档" width="520px">
      <el-form :model="editForm" label-position="top">
        <el-form-item label="标题" required>
          <el-input v-model="editForm.title" maxlength="255" />
        </el-form-item>
        <el-form-item label="文号">
          <el-input v-model="editForm.doc_no" maxlength="100" />
        </el-form-item>
        <el-form-item label="归属部门">
          <el-select v-model="editForm.department_id" clearable style="width: 100%">
            <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="可见性">
          <el-select v-model="editForm.visibility" style="width: 100%">
            <el-option label="公开" value="public" />
            <el-option label="部门内部" value="department" />
            <el-option v-if="isAdmin" label="仅管理员" value="admin_only" />
          </el-select>
        </el-form-item>
        <el-form-item label="复核日期">
          <el-date-picker v-model="editForm.review_due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="versionDialog" title="上传新版本" width="480px">
      <el-form label-position="top">
        <el-form-item label="文件" required>
          <el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="onVersionFileChange"
            :on-remove="onVersionFileRemove"
            :file-list="versionFileList"
            accept=".pdf,.docx,.txt,.md"
          >
            <el-button>选择文件</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="版本号" required>
          <el-input v-model="versionForm.version_label" maxlength="50" />
        </el-form-item>
        <el-form-item label="生效日期">
          <el-date-picker v-model="versionForm.effective_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="versionDialog = false">取消</el-button>
        <el-button type="primary" :loading="versionUploading" @click="submitVersion">上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listDepartments } from '../../api/admin'
import { adminKbList, createKb, deleteKb, rebuildKb, updateKb } from '../../api/knowledge'
import {
  archiveAdminDocument,
  createAdminDocument,
  createDocumentVersion,
  deleteAdminDocument,
  getAdminDocument,
  listAdminDocuments,
  parseDocumentVersion,
  previewDocumentBlob,
  publishDocumentVersion,
  repealDocumentVersion,
  rollbackDocumentVersion,
  updateAdminDocument
} from '../../api/document'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

const kbList = ref([])
const departments = ref([])
const documents = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const activeKbId = ref(null)
const filters = reactive({ keyword: '', visibility: '', lifecycle: '' })
const kbDialog = ref(false)
const editingKb = ref(null)
const savingKb = ref(false)
const rebuilding = ref(false)
const kbForm = reactive({
  name: '',
  description: '',
  status: 'published',
  department_id: null,
  visibility: 'department',
  owner_user_id: null,
  review_cycle_days: 180,
  chunk_strategy: 'clause'
})

const uploadDialog = ref(false)
const uploading = ref(false)
const uploadForm = reactive({
  title: '',
  doc_no: '',
  department_id: null,
  visibility: 'public',
  version_label: 'v1',
  effective_date: null,
  review_due_date: null
})
const uploadFileRaw = ref(null)
const fileList = ref([])

const detailVisible = ref(false)
const detail = ref(null)
const latestReport = computed(() => {
  const version = detail.value?.versions?.[0]
  if (!version?.ingest_report) {
    return '暂无入库报告'
  }
  const report = version.ingest_report
  return `分块 ${report.chunks || 0}，表格 ${report.tables || 0}，条款 ${report.clauses || 0}，策略 ${report.strategy || '-'}`
})

const editDialog = ref(false)
const savingEdit = ref(false)
const editingDocument = ref(null)
const editForm = reactive({ title: '', doc_no: '', department_id: null, visibility: 'public', review_due_date: null })

const versionDialog = ref(false)
const versionUploading = ref(false)
const versionTarget = ref(null)
const versionFileRaw = ref(null)
const versionFileList = ref([])
const versionForm = reactive({ version_label: '', effective_date: null })

const activeKb = computed(() => kbList.value.find((item) => item.id === activeKbId.value) || null)

function strategyLabel(value) {
  return { clause: '条款优先', recursive: '递归长度', fixed: '固定长度' }[value] || value
}

function kbStatusLabel(value) {
  return { published: '已发布', unpublished: '未发布', disabled: '已停用' }[value] || value
}

function kbStatusType(value) {
  return { published: 'success', unpublished: 'info', disabled: 'danger' }[value] || 'info'
}

function onVisibilityChange(value) {
  if (value === 'public') {
    kbForm.department_id = null
  }
}

function visibilityLabel(value) {
  return { public: '公开', department: '部门内部', admin_only: '仅管理员' }[value] || value
}

function visibilityType(value) {
  return { public: 'success', department: 'warning', admin_only: 'danger' }[value] || 'info'
}

function lifecycleLabel(value) {
  return { draft: '草稿', published: '已发布', repealed: '已废止', archived: '已归档' }[value] || value
}

function lifecycleType(value) {
  return { draft: 'info', published: 'success', repealed: 'warning', archived: 'danger' }[value] || 'info'
}

function processLabel(value) {
  return { pending: '待解析', parsing: '解析中', completed: '已完成', failed: '失败' }[value] || value
}

async function loadBase() {
  const [kbs, departmentList] = await Promise.all([adminKbList(), listDepartments()])
  kbList.value = kbs
  departments.value = departmentList
  if (!activeKbId.value && kbs.length > 0) {
    activeKbId.value = kbs[0].id
  }
  await loadDocuments()
}

async function selectKb(kb) {
  activeKbId.value = kb.id
  page.value = 1
  await loadDocuments()
}

async function loadDocuments() {
  if (!activeKbId.value) {
    documents.value = []
    total.value = 0
    return
  }
  const data = await listAdminDocuments({
    kb_id: activeKbId.value,
    page: page.value,
    page_size: pageSize,
    keyword: filters.keyword || undefined,
    visibility: filters.visibility || undefined,
    lifecycle: filters.lifecycle || undefined
  })
  documents.value = data.items
  total.value = data.total
}

function openKbDialog(kb = null) {
  editingKb.value = kb
  kbForm.name = kb?.name || ''
  kbForm.description = kb?.description || ''
  kbForm.status = kb?.status || 'published'
  kbForm.department_id = kb?.department_id || (isAdmin.value ? null : auth.user?.department_id || null)
  kbForm.visibility = kb?.visibility || (isAdmin.value ? 'public' : 'department')
  kbForm.owner_user_id = kb?.owner_user_id || null
  kbForm.review_cycle_days = kb?.review_cycle_days || 180
  kbForm.chunk_strategy = kb?.chunk_strategy || 'clause'
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
    await loadBase()
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
  await loadBase()
}

function openUploadDialog() {
  const kb = activeKb.value
  Object.assign(uploadForm, {
    title: '',
    doc_no: '',
    department_id: kb?.department_id || null,
    visibility: kb?.visibility === 'public' ? 'public' : 'department',
    version_label: 'v1',
    effective_date: null,
    review_due_date: null
  })
  uploadFileRaw.value = null
  fileList.value = []
  uploadDialog.value = true
}

function onFileChange(file) {
  uploadFileRaw.value = file.raw
  fileList.value = [file]
}

function onFileRemove() {
  uploadFileRaw.value = null
  fileList.value = []
}

async function submitUpload() {
  if (!uploadFileRaw.value) {
    ElMessage.warning('请选择文件')
    return
  }
  if (uploadForm.visibility === 'department' && !uploadForm.department_id) {
    ElMessage.warning('部门内部文档必须选择归属部门')
    return
  }
  uploading.value = true
  try {
    const form = new FormData()
    form.append('kb_id', activeKbId.value)
    form.append('file', uploadFileRaw.value)
    Object.entries(uploadForm).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        form.append(key, value)
      }
    })
    const created = await createAdminDocument(form)
    uploadDialog.value = false
    ElMessage.success('上传成功，正在解析索引')
    await loadDocuments()
    await openDetail(created)
    await parseVersion(detail.value.versions[0])
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function openDetail(row) {
  detail.value = await getAdminDocument(row.id)
  detailVisible.value = true
}

async function refreshDetail() {
  if (detail.value) {
    detail.value = await getAdminDocument(detail.value.id)
  }
  await loadDocuments()
}

async function parseVersion(row) {
  try {
    await parseDocumentVersion(row.id)
    ElMessage.success('解析与索引完成')
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '解析失败')
  }
}

async function publishVersion(row) {
  try {
    await publishDocumentVersion(row.id)
    ElMessage.success('版本已发布，旧版本自动废止')
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '发布失败')
  }
}

async function repealVersion(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请填写废止原因', '废止版本', { inputType: 'textarea' })
    reason = result.value || ''
  } catch {
    return
  }
  await repealDocumentVersion(row.id, reason)
  ElMessage.success('版本已废止')
  await refreshDetail()
}

async function rollbackVersion(row) {
  try {
    await ElMessageBox.confirm(`确认回滚到版本「${row.version_label}」？`, '回滚版本', { type: 'warning' })
  } catch {
    return
  }
  await rollbackDocumentVersion(row.id)
  ElMessage.success('已回滚并发布')
  await refreshDetail()
}

async function previewVersion(row) {
  try {
    const blob = await previewDocumentBlob(detail.value.id, row.id)
    const url = URL.createObjectURL(blob)
    window.open(url, '_blank')
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch {
    ElMessage.error('预览失败')
  }
}

function openEditDialog(row) {
  editingDocument.value = row
  Object.assign(editForm, {
    title: row.title,
    doc_no: row.doc_no || '',
    department_id: row.department_id || null,
    visibility: row.visibility,
    review_due_date: row.review_due_date || null
  })
  editDialog.value = true
}

async function saveEdit() {
  if (!editForm.title.trim()) {
    ElMessage.warning('请输入标题')
    return
  }
  savingEdit.value = true
  try {
    await updateAdminDocument(editingDocument.value.id, { ...editForm })
    ElMessage.success('文档已更新，索引元数据已同步')
    editDialog.value = false
    await loadDocuments()
    if (detailVisible.value && detail.value?.id === editingDocument.value.id) {
      await refreshDetail()
    }
  } finally {
    savingEdit.value = false
  }
}

async function archiveDocument(row) {
  try {
    await ElMessageBox.confirm('归档后文档不再参与问答，确认继续？', '归档文档', { type: 'warning' })
  } catch {
    return
  }
  await archiveAdminDocument(row.id, '管理端归档')
  ElMessage.success('文档已归档')
  detailVisible.value = false
  await loadDocuments()
}

async function removeDocument(row) {
  try {
    await ElMessageBox.confirm(`确认删除文档「${row.title}」及其全部版本？`, '删除文档', { type: 'warning' })
  } catch {
    return
  }
  await deleteAdminDocument(row.id)
  ElMessage.success('文档已删除')
  await loadDocuments()
}

function openVersionUpload(row) {
  versionTarget.value = row
  versionForm.version_label = ''
  versionForm.effective_date = null
  versionFileRaw.value = null
  versionFileList.value = []
  versionDialog.value = true
}

function onVersionFileChange(file) {
  versionFileRaw.value = file.raw
  versionFileList.value = [file]
}

function onVersionFileRemove() {
  versionFileRaw.value = null
  versionFileList.value = []
}

async function submitVersion() {
  if (!versionFileRaw.value || !versionForm.version_label.trim()) {
    ElMessage.warning('请选择文件并填写版本号')
    return
  }
  versionUploading.value = true
  try {
    const form = new FormData()
    form.append('file', versionFileRaw.value)
    form.append('version_label', versionForm.version_label)
    if (versionForm.effective_date) {
      form.append('effective_date', versionForm.effective_date)
    }
    await createDocumentVersion(versionTarget.value.id, form)
    versionDialog.value = false
    ElMessage.success('新版本已上传，请解析后发布')
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '上传失败')
  } finally {
    versionUploading.value = false
  }
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

onMounted(loadBase)
</script>

<style scoped>
.knowledge-layout {
  display: flex;
  height: 100%;
}

.kb-sidebar {
  width: 300px;
  flex: none;
  padding: 16px;
  overflow-y: auto;
  border-right: 1px solid #e4e7ed;
}

.kb-main {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
}

.kb-name {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}
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
.report-block {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 10px 12px;
  white-space: pre-wrap;
  font-size: 13px;
}
</style>
