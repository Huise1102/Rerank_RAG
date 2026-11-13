<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="muted">策略决定向量召回与关键词召回的权重、融合参数与精排开关，用户端只能选择已开放的策略</div>
      </div>
      <el-button type="primary" @click="openDialog()">新建策略</el-button>
    </div>

    <el-table :data="items" size="small" border>
      <el-table-column prop="name" label="名称" width="140" />
      <el-table-column prop="description" label="说明" show-overflow-tooltip />
      <el-table-column label="召回方式" width="160">
        <template #default="{ row }">
          <el-tag v-if="row.use_vector" size="small" style="margin-right: 4px">向量</el-tag>
          <el-tag v-if="row.use_bm25" size="small" type="success">关键词</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="权重" width="130">
        <template #default="{ row }">{{ row.vector_weight }} / {{ row.bm25_weight }}</template>
      </el-table-column>
      <el-table-column prop="rerank_top_n" label="精排条数" width="90" />
      <el-table-column label="精排" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.rerank ? 'success' : 'info'">{{ row.rerank ? '开' : '关' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="对用户开放" width="110">
        <template #default="{ row }">
          <el-switch :model-value="row.is_open" @change="toggleOpen(row, $event)" />
        </template>
      </el-table-column>
      <el-table-column label="默认" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.is_default" size="small" type="warning">默认</el-tag>
          <span v-else class="muted">否</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
          <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="editing ? '编辑策略' : '新建策略'" width="560px">
      <el-form :model="form" label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="50" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="300" />
        </el-form-item>
        <div style="display: flex; gap: 16px">
          <el-form-item label="启用向量召回">
            <el-switch v-model="form.use_vector" />
          </el-form-item>
          <el-form-item label="启用关键词召回">
            <el-switch v-model="form.use_bm25" />
          </el-form-item>
          <el-form-item label="启用精排">
            <el-switch v-model="form.rerank" />
          </el-form-item>
        </div>
        <div style="display: flex; gap: 16px">
          <el-form-item label="向量权重">
            <el-input-number v-model="form.vector_weight" :min="0" :max="1" :step="0.1" />
          </el-form-item>
          <el-form-item label="关键词权重">
            <el-input-number v-model="form.bm25_weight" :min="0" :max="1" :step="0.1" />
          </el-form-item>
        </div>
        <div style="display: flex; gap: 16px">
          <el-form-item label="向量召回数量">
            <el-input-number v-model="form.vector_top_k" :min="1" :max="100" />
          </el-form-item>
          <el-form-item label="关键词召回数量">
            <el-input-number v-model="form.bm25_top_k" :min="1" :max="100" />
          </el-form-item>
          <el-form-item label="精排条数">
            <el-input-number v-model="form.rerank_top_n" :min="1" :max="20" />
          </el-form-item>
        </div>
        <div style="display: flex; gap: 16px">
          <el-form-item label="融合常数">
            <el-input-number v-model="form.rrf_k" :min="1" :max="500" />
          </el-form-item>
          <el-form-item label="对用户开放">
            <el-switch v-model="form.is_open" />
          </el-form-item>
          <el-form-item label="设为默认">
            <el-switch v-model="form.is_default" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminStrategies, createStrategy, deleteStrategy, updateStrategy } from '../../api/knowledge'

const items = ref([])
const dialog = ref(false)
const editing = ref(null)
const saving = ref(false)
const form = reactive({
  name: '',
  description: '',
  use_vector: true,
  use_bm25: true,
  vector_weight: 0.5,
  bm25_weight: 0.5,
  rrf_k: 60,
  rerank: true,
  vector_top_k: 50,
  bm25_top_k: 50,
  rerank_top_n: 5,
  is_open: true,
  is_default: false
})

async function load() {
  items.value = await adminStrategies()
}

function openDialog(row = null) {
  editing.value = row
  Object.assign(form, {
    name: row?.name || '',
    description: row?.description || '',
    use_vector: row?.use_vector ?? true,
    use_bm25: row?.use_bm25 ?? true,
    vector_weight: row?.vector_weight ?? 0.5,
    bm25_weight: row?.bm25_weight ?? 0.5,
    rrf_k: row?.rrf_k ?? 60,
    rerank: row?.rerank ?? true,
    vector_top_k: row?.vector_top_k ?? 50,
    bm25_top_k: row?.bm25_top_k ?? 50,
    rerank_top_n: row?.rerank_top_n ?? 5,
    is_open: row?.is_open ?? true,
    is_default: row?.is_default ?? false
  })
  dialog.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入策略名称')
    return
  }
  if (!form.use_vector && !form.use_bm25) {
    ElMessage.warning('至少启用一种召回方式')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateStrategy(editing.value.id, { ...form })
    } else {
      await createStrategy({ ...form })
    }
    ElMessage.success('策略已保存')
    dialog.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleOpen(row, value) {
  await updateStrategy(row.id, { is_open: value })
  row.is_open = value
  ElMessage.success(value ? '已对用户开放' : '已关闭用户访问')
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除策略「${row.name}」？`, '删除策略', { type: 'warning' })
  } catch {
    return
  }
  await deleteStrategy(row.id)
  ElMessage.success('策略已删除')
  await load()
}

onMounted(load)
</script>
