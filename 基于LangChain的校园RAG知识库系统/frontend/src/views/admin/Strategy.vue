<template>
  <div class="page">
    <div class="page-header">
      <div class="muted">
        策略决定向量与关键词两路召回的权重、融合参数与精排开关，用户端只能选择已开放的策略
      </div>
      <el-button type="primary" @click="openDialog()">新建策略</el-button>
    </div>

    <div class="strategy-stats">
      <div class="stat-card">
        <div class="stat-value">{{ items.length }}</div>
        <div class="stat-label">策略总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ openCount }}</div>
        <div class="stat-label">对用户开放</div>
      </div>
      <div class="stat-card">
        <div class="stat-value stat-value--text">{{ defaultName }}</div>
        <div class="stat-label">当前默认策略</div>
      </div>
    </div>

    <el-empty v-if="!items.length" description="暂无策略，点击右上角新建" />

    <div v-else class="strategy-grid">
      <div
        v-for="row in items"
        :key="row.id"
        class="strategy-card"
        :class="{ 'is-default': row.is_default }"
      >
        <div class="strategy-card__head">
          <div class="strategy-title">
            <span class="strategy-name">{{ row.name }}</span>
            <el-tag v-if="row.is_default" size="small" type="warning">默认</el-tag>
            <el-tag v-if="!row.is_open" size="small" type="info">未开放</el-tag>
          </div>
          <div class="strategy-desc">{{ row.description || '未填写说明' }}</div>
        </div>

        <div class="block-tags">
          <span class="block-tag" :class="{ 'is-off': !row.use_vector }">向量召回</span>
          <span class="block-tag" :class="{ 'is-off': !row.use_bm25 }">关键词召回</span>
          <span class="block-tag" :class="{ 'is-off': !(row.use_vector && row.use_bm25) }">RRF 融合</span>
          <span class="block-tag" :class="{ 'is-off': !row.rerank }">精排</span>
        </div>

        <div class="strategy-combo">链路：{{ comboLabel(row) }}</div>

        <div class="strategy-params">
          <span>权重 {{ row.vector_weight }} / {{ row.bm25_weight }}</span>
          <span>召回 {{ row.vector_top_k }} / {{ row.bm25_top_k }}</span>
          <span>精排 Top-{{ row.rerank_top_n }}</span>
          <span>RRF k 取 {{ row.rrf_k }}</span>
        </div>

        <div class="strategy-footer">
          <div class="open-switch">
            <span>对用户开放</span>
            <el-switch :model-value="row.is_open" @change="toggleOpen(row, $event)" />
          </div>
          <div class="strategy-actions">
            <el-button link type="primary" size="small" @click="openDialog(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="dialog" :title="editing ? '编辑策略' : '新建策略'" width="640px">
      <el-form :model="form" label-position="top" class="strategy-form">
        <div class="form-grid">
          <el-form-item label="策略名称" required>
            <el-input v-model="form.name" maxlength="50" placeholder="例如 混合检索加精排" />
          </el-form-item>
          <el-form-item label="说明">
            <el-input v-model="form.description" maxlength="300" placeholder="说明适用场景" />
          </el-form-item>
        </div>

        <div class="section-label">构建块开关</div>
        <div class="block-list">
          <div v-for="block in blocks" :key="block.key" class="block-row">
            <div>
              <div class="block-name">{{ block.name }}</div>
              <div class="block-desc">{{ block.desc }}</div>
            </div>
            <el-switch v-model="form[block.key]" />
          </div>
        </div>
        <div class="preview-line">
          当前组合：<b>{{ comboLabel(form) }}</b>
        </div>

        <div class="section-label">参数设置</div>
        <div class="form-grid">
          <el-form-item label="向量权重">
            <el-input-number v-model="form.vector_weight" :min="0" :max="1" :step="0.1" />
          </el-form-item>
          <el-form-item label="关键词权重">
            <el-input-number v-model="form.bm25_weight" :min="0" :max="1" :step="0.1" />
          </el-form-item>
          <el-form-item label="向量召回数量">
            <el-input-number v-model="form.vector_top_k" :min="1" :max="100" />
          </el-form-item>
          <el-form-item label="关键词召回数量">
            <el-input-number v-model="form.bm25_top_k" :min="1" :max="100" />
          </el-form-item>
          <el-form-item label="精排保留条数">
            <el-input-number v-model="form.rerank_top_n" :min="1" :max="20" />
          </el-form-item>
          <el-form-item label="RRF 融合常数">
            <el-input-number v-model="form.rrf_k" :min="1" :max="500" />
          </el-form-item>
        </div>

        <div class="section-label">开放设置</div>
        <div class="form-grid">
          <el-form-item label="对用户端开放">
            <el-switch v-model="form.is_open" />
          </el-form-item>
          <el-form-item label="设为默认策略">
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
import { computed, onActivated, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminStrategies, createStrategy, deleteStrategy, updateStrategy } from '../../api/knowledge'

const items = ref([])
const dialog = ref(false)
const editing = ref(null)
const saving = ref(false)

const blocks = [
  { key: 'use_vector', name: '向量召回', desc: 'BGE 稠密向量，适合语义相近的问法' },
  { key: 'use_bm25', name: '关键词召回', desc: 'BM25 词频检索，条款号与专有名词命中更准' },
  { key: 'rerank', name: '精排 Rerank', desc: '交叉编码模型对融合结果重新排序' }
]

const openCount = computed(() => items.value.filter((item) => item.is_open).length)
const defaultName = computed(() => {
  const target = items.value.find((item) => item.is_default)
  return target ? target.name : '未设置'
})

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

function comboLabel(row) {
  const vector = Boolean(row.use_vector)
  const bm25 = Boolean(row.use_bm25)
  const rerank = Boolean(row.rerank)
  if (!vector && !bm25) {
    return '未启用召回'
  }
  if (vector && bm25) {
    return rerank ? '混合检索 + 精排' : '混合检索'
  }
  const base = vector ? '纯向量检索' : '纯关键词检索'
  return rerank ? `${base} + 精排` : base
}

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

onActivated(load)
</script>

<style scoped>
.strategy-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat-card {
  padding: 14px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: #1f2d3d;
}

.stat-value--text {
  font-size: 16px;
  line-height: 33px;
}

.stat-label {
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.strategy-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px;
}

.strategy-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
}

.strategy-card.is-default {
  border-color: #e6c37a;
}

.strategy-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.strategy-name {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}

.strategy-desc {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  min-height: 18px;
}

.block-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.block-tag {
  padding: 1px 8px;
  border: 1px solid #a9c6ea;
  border-radius: 3px;
  background: #eef5fd;
  color: #1f6fd0;
  font-size: 12px;
  font-weight: 600;
}

.block-tag.is-off {
  border-color: #dcdfe6;
  background: #f5f7fa;
  color: #b1b3b8;
  font-weight: 400;
  text-decoration: line-through;
}

.strategy-combo {
  font-size: 13px;
  color: #303133;
}

.strategy-params {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  font-size: 12px;
  color: #606266;
}

.strategy-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 10px;
  border-top: 1px solid #ebeef5;
}

.open-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}

.strategy-actions {
  display: flex;
  align-items: center;
}

.strategy-form :deep(.el-form-item) {
  margin-bottom: 12px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.section-label {
  margin: 6px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.block-list {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 0 12px;
}

.block-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f2f5;
}

.block-row:last-child {
  border-bottom: none;
}

.block-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.block-desc {
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.preview-line {
  margin: 10px 0 4px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #f5f8fd;
  font-size: 13px;
  color: #606266;
}

.preview-line b {
  color: #1f6fd0;
}

@media (max-width: 900px) {
  .strategy-stats {
    grid-template-columns: 1fr;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
