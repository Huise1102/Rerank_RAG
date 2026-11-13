<template>
  <div class="page" style="height: 100%; overflow-y: auto">
    <div class="page-header">
      <div>
        <div class="page-title">评估回归</div>
        <div class="muted">使用黄金测试集检查检索与生成质量，评估请求会按所选角色执行 ACL 过滤</div>
      </div>
      <el-button @click="loadReports">刷新</el-button>
    </div>

    <el-card shadow="never" style="margin-bottom: 16px">
      <el-form :inline="true" :model="form">
        <el-form-item label="知识库">
          <el-select v-model="form.kb_id" style="width: 180px">
            <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="评估类型">
          <el-select v-model="form.kind" style="width: 140px">
            <el-option label="检索评估" value="retrieval" />
            <el-option label="生成评估" value="generation" />
          </el-select>
        </el-form-item>
        <el-form-item label="测试集">
          <el-input v-model="form.testset_path" style="width: 320px" />
        </el-form-item>
        <el-form-item label="样本数">
          <el-input-number v-model="form.limit" :min="1" :max="400" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 130px">
            <el-option label="管理员" value="admin" />
            <el-option label="部门管理员" value="dept_admin" />
            <el-option label="教职工" value="staff" />
            <el-option label="学生" value="student" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button :loading="validating" @click="validate">校验测试集</el-button>
          <el-button type="primary" :loading="running" @click="run">开始评估</el-button>
        </el-form-item>
      </el-form>
      <el-alert
        v-if="validation"
        :title="validation.valid ? '测试集校验通过' : '测试集校验未通过'"
        :type="validation.valid ? 'success' : 'error'"
        :closable="false"
        show-icon
      >
        <div>样本 {{ validation.total }} 条，类别 {{ Object.keys(validation.categories || {}).length }} 个</div>
        <div v-for="item in (validation.errors || []).slice(0, 5)" :key="item" class="muted">{{ item }}</div>
        <div v-for="item in (validation.warnings || []).slice(0, 5)" :key="item" class="muted">{{ item }}</div>
      </el-alert>
    </el-card>

    <el-row v-if="current" :gutter="12" style="margin-bottom: 16px">
      <el-col v-for="item in summaryCards" :key="item.label" :span="4">
        <el-card shadow="never">
          <div class="muted">{{ item.label }}</div>
          <div class="stat-value">{{ item.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-table :data="reports" size="small" border>
      <el-table-column prop="id" label="编号" width="80" />
      <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
      <el-table-column prop="kind" label="类型" width="100">
        <template #default="{ row }">{{ row.kind === 'retrieval' ? '检索' : '生成' }}</template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column label="摘要" min-width="260">
        <template #default="{ row }">{{ summaryText(row.summary) }}</template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button link type="primary" @click="openReport(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="reportVisible" title="评估报告" width="860px">
      <div v-if="current">
        <div v-if="current.markdown" class="markdown-block">{{ current.markdown }}</div>
        <div v-else class="muted">暂无报告内容</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminKbList } from '../../api/knowledge'
import { getEvaluationReport, listEvaluationReports, runEvaluation, validateTestset } from '../../api/evaluation'

const kbList = ref([])
const reports = ref([])
const current = ref(null)
const reportVisible = ref(false)
const validation = ref(null)
const running = ref(false)
const validating = ref(false)
const form = reactive({
  kb_id: null,
  kind: 'retrieval',
  testset_path: 'tests/fixtures/evaluation_sample.jsonl',
  limit: 20,
  top_k: 10,
  role: 'admin',
  department_id: null
})

const summaryCards = computed(() => {
  const summary = current.value?.summary || {}
  if (current.value?.kind === 'generation') {
    return [
      { label: '样本数', value: summary.total ?? '-' },
      { label: '忠实度', value: summary.faithfulness ?? '-' },
      { label: '相关性', value: summary.relevance ?? '-' },
      { label: '引用覆盖', value: summary.citation_coverage ?? '-' },
      { label: '有依据', value: summary.with_evidence ?? '-' }
    ]
  }
  return [
    { label: '样本数', value: summary.total ?? '-' },
    { label: '命中率', value: summary.hit_rate ?? '-' },
    { label: 'Recall', value: summary.recall ?? '-' },
    { label: 'MRR', value: summary.mrr ?? '-' },
    { label: 'nDCG', value: summary.ndcg ?? '-' },
    { label: '关键词覆盖', value: summary.keyword_coverage ?? '-' }
  ]
})

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : ''
}

function summaryText(summary) {
  if (!summary) {
    return '-'
  }
  if ('faithfulness' in summary) {
    return `忠实度 ${summary.faithfulness}，相关性 ${summary.relevance}，样本 ${summary.total}`
  }
  return `命中率 ${summary.hit_rate}，Recall ${summary.recall}，样本 ${summary.total}`
}

async function loadReports() {
  reports.value = await listEvaluationReports()
  if (reports.value.length && !current.value) {
    await openReport(reports.value[0])
  }
}

async function openReport(row) {
  current.value = await getEvaluationReport(row.id)
  reportVisible.value = true
}

async function validate() {
  validating.value = true
  try {
    validation.value = await validateTestset({ testset_path: form.testset_path })
    ElMessage.success('校验完成')
  } finally {
    validating.value = false
  }
}

async function run() {
  if (!form.kb_id) {
    ElMessage.warning('请选择知识库')
    return
  }
  running.value = true
  try {
    const result = await runEvaluation({
      ...form,
      department_id: form.role === 'admin' ? null : form.department_id
    })
    current.value = { ...result, kind: form.kind }
    reportVisible.value = true
    ElMessage.success('评估完成')
    await loadReports()
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  kbList.value = await adminKbList()
  if (kbList.value.length) {
    form.kb_id = kbList.value[0].id
    form.department_id = kbList.value[0].department_id || null
  }
  await loadReports()
})
</script>

<style scoped>
.markdown-block {
  white-space: pre-wrap;
  line-height: 1.8;
  font-size: 13px;
  background: #f7f8fa;
  border-radius: 8px;
  padding: 14px;
  max-height: 520px;
  overflow-y: auto;
}
</style>
