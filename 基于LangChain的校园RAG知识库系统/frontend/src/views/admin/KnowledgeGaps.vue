<template>
  <div class="page">
    <div class="page-head">
      <h2>知识缺口</h2>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
        <el-select v-model="filters.status" clearable placeholder="全部状态" style="width: 140px" @change="load">
          <el-option label="待处理" value="open" />
          <el-option label="处理中" value="in_progress" />
          <el-option label="已解决" value="resolved" />
          <el-option label="已忽略" value="ignored" />
        </el-select>
        <el-select v-model="filters.category" clearable placeholder="全部分类" style="width: 160px" @change="load">
          <el-option label="缺少文档" value="missing_document" />
          <el-option label="文档过期" value="expired_document" />
          <el-option label="检索问题" value="retrieval" />
          <el-option label="模型问题" value="model" />
          <el-option label="无需处理" value="no_action" />
        </el-select>
        <el-select v-model="filters.kb_id" clearable placeholder="全部知识库" style="width: 200px" @change="load">
          <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button v-if="auth.isAdmin" type="primary" :loading="scanning" @click="doScan">重新扫描</el-button>
        <el-button :loading="exporting" @click="doExport">导出 CSV</el-button>
      </div>
    </div>

    <el-alert
      title="缺口来自零引用、拒答、负反馈与转办问题，按归一化问题聚合，扫描不会覆盖已处理状态"
      type="info"
      :closable="false"
      style="margin-bottom: 12px"
    />

    <el-table :data="items" size="small">
      <el-table-column prop="question" label="问题" min-width="280" />
      <el-table-column prop="kb_name" label="知识库" width="150" />
      <el-table-column prop="occurrences" label="出现次数" width="100" />
      <el-table-column label="来源" width="200">
        <template #default="{ row }">
          <el-tag v-for="source in row.sources" :key="source" size="small" style="margin-right: 4px">
            {{ sourceLabel(source) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="120">
        <template #default="{ row }">{{ categoryLabel(row.category) }}</template>
      </el-table-column>
      <el-table-column prop="last_seen_at" label="最后出现" width="170" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button link size="small" @click="openEdit(row)">处理</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top: 12px"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="filters.page_size"
      :current-page="filters.page"
      @current-change="changePage"
    />

    <el-dialog v-model="dialogVisible" title="处理知识缺口" width="520px">
      <el-form label-width="110px">
        <el-form-item label="问题">
          <div style="line-height: 1.7">{{ editing?.question }}</div>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option label="待处理" value="open" />
            <el-option label="处理中" value="in_progress" />
            <el-option label="已解决" value="resolved" />
            <el-option label="已忽略" value="ignored" />
          </el-select>
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editForm.category" clearable style="width: 100%">
            <el-option label="缺少文档" value="missing_document" />
            <el-option label="文档过期" value="expired_document" />
            <el-option label="检索问题" value="retrieval" />
            <el-option label="模型问题" value="model" />
            <el-option label="无需处理" value="no_action" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联文档 ID">
          <el-input-number v-model="editForm.related_document_id" :min="1" :controls="false" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../../stores/auth'
import { adminKbList } from '../../api/knowledge'
import { exportGaps, listGaps, scanGaps, updateGap } from '../../api/gaps'

const auth = useAuthStore()
const items = ref([])
const total = ref(0)
const knowledgeBases = ref([])
const scanning = ref(false)
const exporting = ref(false)
const dialogVisible = ref(false)
const editing = ref(null)
const filters = reactive({ status: 'open', category: null, kb_id: null, page: 1, page_size: 20 })
const editForm = reactive({ status: 'open', category: null, related_document_id: null })

async function load() {
  try {
    const data = await listGaps({
      status: filters.status || undefined,
      category: filters.category || undefined,
      kb_id: filters.kb_id || undefined,
      page: filters.page,
      page_size: filters.page_size
    })
    items.value = data.items || []
    total.value = data.total || 0
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '知识缺口加载失败')
  }
}

function changePage(page) {
  filters.page = page
  load()
}

async function doScan() {
  scanning.value = true
  try {
    const data = await scanGaps(30)
    ElMessage.success(`扫描完成，新增 ${data.created} 条，更新 ${data.updated} 条`)
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '扫描失败')
  } finally {
    scanning.value = false
  }
}

function openEdit(row) {
  editing.value = row
  editForm.status = row.status
  editForm.category = row.category
  editForm.related_document_id = row.related_document_id
  dialogVisible.value = true
}

async function saveEdit() {
  try {
    await updateGap(editing.value.id, { ...editForm })
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  }
}

async function doExport() {
  exporting.value = true
  try {
    const blob = await exportGaps()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'knowledge_gaps.csv'
    link.click()
    window.URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '导出失败')
  } finally {
    exporting.value = false
  }
}

function sourceLabel(value) {
  return { zero_citation: '零引用', transfer: '转办', negative_feedback: '负反馈' }[value] || value
}

function statusLabel(value) {
  return { open: '待处理', in_progress: '处理中', resolved: '已解决', ignored: '已忽略' }[value] || value
}

function statusType(value) {
  return { open: 'danger', in_progress: 'warning', resolved: 'success', ignored: 'info' }[value] || 'info'
}

function categoryLabel(value) {
  return {
    missing_document: '缺少文档',
    expired_document: '文档过期',
    retrieval: '检索问题',
    model: '模型问题',
    no_action: '无需处理'
  }[value] || '未分类'
}

onMounted(async () => {
  try {
    knowledgeBases.value = await adminKbList()
  } catch {
    knowledgeBases.value = []
  }
  await load()
})
</script>
