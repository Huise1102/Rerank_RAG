<template>
  <div class="page">
    <div class="page-head">
      <h2>运营看板</h2>
      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
        <el-select v-if="auth.isAdmin" v-model="departmentId" clearable placeholder="全部部门" style="width: 180px" @change="load">
          <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="days" style="width: 120px" @change="load">
          <el-option label="近 7 天" :value="7" />
          <el-option label="近 14 天" :value="14" />
          <el-option label="近 30 天" :value="30" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <div class="card-grid">
      <el-card><div class="muted">提问量</div><div class="stat-value">{{ summary.questions || 0 }}</div></el-card>
      <el-card><div class="muted">活跃用户</div><div class="stat-value">{{ summary.active_users || 0 }}</div></el-card>
      <el-card><div class="muted">零引用率</div><div class="stat-value">{{ percent(summary.zero_citation_rate) }}</div></el-card>
      <el-card><div class="muted">转办率</div><div class="stat-value">{{ percent(summary.transfer_rate) }}</div></el-card>
      <el-card><div class="muted">缓存命中率</div><div class="stat-value">{{ percent(summary.cache_hit_rate) }}</div></el-card>
      <el-card><div class="muted">反馈率</div><div class="stat-value">{{ percent(summary.feedback_rate) }}</div></el-card>
      <el-card><div class="muted">P95 耗时</div><div class="stat-value">{{ summary.p95_ms || 0 }} 毫秒</div></el-card>
      <el-card><div class="muted">待处理反馈</div><div class="stat-value">{{ summary.pending_feedback || 0 }}</div></el-card>
      <el-card><div class="muted">过期文档</div><div class="stat-value">{{ summary.stale_documents || 0 }}</div></el-card>
      <el-card><div class="muted">未解决缺口</div><div class="stat-value">{{ summary.open_gaps || 0 }}</div></el-card>
    </div>

    <el-card style="margin-top: 12px">
      <template #header>每日趋势</template>
      <el-table :data="trends" size="small">
        <el-table-column prop="date" label="日期" width="120" />
        <el-table-column prop="questions" label="提问量" width="100" />
        <el-table-column label="趋势" min-width="220">
          <template #default="{ row }">
            <div class="trend-bar">
              <div class="trend-bar-inner" :style="{ width: barWidth(row.questions) }" />
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="zero_citation" label="零引用" width="100" />
        <el-table-column prop="cache_hits" label="缓存命中" width="110" />
      </el-table>
    </el-card>

    <el-card v-if="auth.isAdmin" style="margin-top: 12px">
      <template #header>部门对比</template>
      <el-table :data="departmentRows" size="small">
        <el-table-column prop="department_name" label="部门" min-width="160" />
        <el-table-column prop="questions" label="提问量" width="100" />
        <el-table-column prop="active_users" label="活跃用户" width="100" />
        <el-table-column prop="zero_citation" label="零引用" width="100" />
        <el-table-column prop="cache_hits" label="缓存命中" width="110" />
      </el-table>
    </el-card>

    <el-card style="margin-top: 12px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>文档到期复核</span>
          <div>
            <el-button size="small" @click="sendReviewNotice">发送提醒</el-button>
            <el-button size="small" @click="loadReviewDue">刷新</el-button>
          </div>
        </div>
      </template>
      <el-table :data="reviewRows" size="small">
        <el-table-column prop="title" label="文档" min-width="220" />
        <el-table-column prop="kb_name" label="知识库" width="160" />
        <el-table-column prop="review_due_date" label="复核日期" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.overdue" type="danger" size="small">已过期</el-tag>
            <el-tag v-else type="warning" size="small">即将到期</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240">
          <template #default="{ row }">
            <el-button link size="small" @click="review(row, 'confirm')">继续有效</el-button>
            <el-button link size="small" @click="review(row, 'revise')">发起修订</el-button>
            <el-button link size="small" type="danger" @click="review(row, 'repeal')">废止</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="reviewRows.length === 0" class="muted" style="padding: 12px">暂无到期文档</div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../../stores/auth'
import { listDepartments } from '../../api/admin'
import {
  getDashboardSummary,
  getDashboardTrends,
  getDepartmentComparison,
  listReviewDue,
  notifyReviewDue,
  reviewDocument
} from '../../api/dashboard'

const auth = useAuthStore()
const days = ref(7)
const departmentId = ref(null)
const summary = ref({})
const trends = ref([])
const departmentRows = ref([])
const reviewRows = ref([])
const departments = ref([])

const maxQuestions = computed(() => Math.max(...trends.value.map((item) => item.questions), 1))

function percent(value) {
  return `${(((value || 0) * 100).toFixed(1))}%`
}

function barWidth(value) {
  return `${Math.max((value / maxQuestions.value) * 100, 2)}%`
}

async function load() {
  try {
    const params = { days: days.value, department_id: departmentId.value || undefined }
    const [summaryData, trendData, departmentData] = await Promise.all([
      getDashboardSummary(params),
      getDashboardTrends({ days: Math.max(days.value, 14), department_id: departmentId.value || undefined }),
      auth.isAdmin
        ? getDepartmentComparison({ days: 30, department_id: departmentId.value || undefined })
        : Promise.resolve({ items: [] })
    ])
    summary.value = summaryData
    trends.value = trendData.items || []
    departmentRows.value = departmentData.items || []
    await loadReviewDue()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '看板数据加载失败')
  }
}

async function loadReviewDue() {
  try {
    const data = await listReviewDue(30)
    reviewRows.value = (data.items || []).slice(0, 20)
  } catch {
    reviewRows.value = []
  }
}

async function review(row, action) {
  let note = ''
  if (action !== 'confirm') {
    try {
      const result = await ElMessageBox.prompt(
        action === 'repeal' ? '请填写废止原因' : '请填写修订说明',
        action === 'repeal' ? '废止文档' : '发起修订',
        { inputPlaceholder: '必填', inputValidator: (value) => Boolean(value && value.trim()) }
      )
      note = result.value
    } catch {
      return
    }
  }
  try {
    await reviewDocument(row.id, { action, note })
    ElMessage.success('已更新复核状态')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '操作失败')
  }
}

async function sendReviewNotice() {
  try {
    const data = await notifyReviewDue(30)
    ElMessage.success(`已为 ${data.notifications} 条提醒发送站内通知`)
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '提醒发送失败')
  }
}

onMounted(async () => {
  if (auth.isAdmin) {
    try {
      departments.value = await listDepartments()
    } catch {
      departments.value = []
    }
  }
  await load()
})
</script>
