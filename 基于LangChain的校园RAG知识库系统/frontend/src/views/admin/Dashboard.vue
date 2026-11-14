<template>
  <div class="page dashboard">
    <div class="page-head">
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

    <div class="metric-grid">
      <div v-for="item in metrics" :key="item.label" class="metric-card">
        <div class="metric-top">
          <span class="metric-icon" :style="{ color: item.color, backgroundColor: item.color + '14' }">
            <el-icon><component :is="item.icon" /></el-icon>
          </span>
          <span>{{ item.label }}</span>
        </div>
        <div class="metric-value">
          {{ item.value }}<span v-if="item.unit" class="metric-unit">{{ item.unit }}</span>
        </div>
        <div class="metric-sub">{{ item.sub }}</div>
      </div>
    </div>

    <div class="dashboard-main">
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">每日问答趋势</div>
          <div class="legend">
            <span><i style="background: #0f9d8f"></i>提问量</span>
            <span><i style="background: #60a5fa"></i>缓存命中</span>
          </div>
        </div>
        <svg
          class="chart-svg"
          :viewBox="`0 0 ${chart.width} ${chart.height}`"
          role="img"
          aria-label="每日问答趋势"
        >
          <g v-for="tick in chart.ticks" :key="tick.value">
            <line :x1="chart.left" :x2="chart.width - 14" :y1="tick.y" :y2="tick.y" stroke="#eef2f7" />
            <text :x="chart.left - 8" :y="tick.y + 4" text-anchor="end" class="axis-text">
              {{ tick.value }}
            </text>
          </g>
          <g v-for="(bar, index) in chart.bars" :key="index">
            <rect :x="bar.x" :y="bar.y" :width="bar.w" :height="bar.h" :fill="bar.color" rx="2">
              <title>{{ bar.label }} {{ bar.name }}：{{ bar.value }}</title>
            </rect>
          </g>
          <text
            v-for="label in chart.labels"
            :key="label.text"
            :x="label.x"
            :y="chart.height - 8"
            text-anchor="middle"
            class="axis-text"
          >
            {{ label.text }}
          </text>
        </svg>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">{{ donutTitle }}</div>
        </div>
        <div v-if="donutTotal" class="donut-wrap">
          <div class="donut" :style="{ background: donutGradient }">
            <div class="donut-center">
              <strong>{{ donutTotal }}</strong>
              <span>提问总量</span>
            </div>
          </div>
          <div class="donut-legend">
            <div v-for="item in donutItems" :key="item.name" class="legend-row">
              <span class="legend-name">
                <i :style="{ background: item.color }"></i>{{ item.name }}
              </span>
              <span class="legend-value">{{ item.value }} · {{ item.percent }}%</span>
            </div>
          </div>
        </div>
        <div v-else class="empty tall">暂无问答数据</div>
      </section>
    </div>

    <div class="pending-grid">
      <div v-for="item in pendingItems" :key="item.label" class="pending-card">
        <span class="pending-bar" :style="{ background: item.color }"></span>
        <div>
          <div class="pending-label">{{ item.label }}</div>
          <div class="pending-value">{{ item.value }}</div>
        </div>
      </div>
    </div>

    <section class="panel review-panel">
      <div class="panel-head">
        <div class="panel-title">
          文档到期复核
          <span class="count">共 {{ reviewRows.length }} 条</span>
        </div>
        <div class="review-actions">
          <el-button size="small" @click="sendReviewNotice">发送提醒</el-button>
          <el-button size="small" @click="loadReviewDue">刷新</el-button>
        </div>
      </div>
      <el-table :data="reviewRows" size="small" max-height="300">
        <el-table-column prop="title" label="文档" min-width="220" show-overflow-tooltip />
        <el-table-column prop="kb_name" label="知识库" width="180" show-overflow-tooltip />
        <el-table-column prop="review_due_date" label="复核日期" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.overdue" type="danger" size="small">已过期</el-tag>
            <el-tag v-else type="warning" size="small">即将到期</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" @click="review(row, 'confirm')">继续有效</el-button>
            <el-button link size="small" @click="review(row, 'revise')">发起修订</el-button>
            <el-button link type="danger" size="small" @click="review(row, 'repeal')">废止</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="reviewRows.length === 0" class="empty">暂无到期文档</div>
    </section>
  </div>
</template>

<script setup>
import { computed, onActivated, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Lightning,
  Switch,
  Timer,
  User,
  Warning
} from '@element-plus/icons-vue'
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

const DONUT_COLORS = ['#0f9d8f', '#38bdf8', '#6366f1', '#f59e0b', '#ef4444', '#94a3b8']

function percent(value) {
  return `${(((value || 0) * 100).toFixed(1))}%`
}

function pad(value) {
  return String(value).padStart(2, '0')
}

function dateKey(offset) {
  const date = new Date()
  date.setDate(date.getDate() - offset)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

const metrics = computed(() => [
  {
    label: '提问量',
    value: summary.value.questions || 0,
    sub: `近 ${days.value} 天累计`,
    icon: ChatDotRound,
    color: '#0f9d8f'
  },
  {
    label: '活跃用户',
    value: summary.value.active_users || 0,
    sub: '去重提问用户',
    icon: User,
    color: '#3b82f6'
  },
  {
    label: '零引用率',
    value: percent(summary.value.zero_citation_rate),
    sub: `零引用 ${summary.value.zero_citation || 0} 次`,
    icon: Warning,
    color: '#f59e0b'
  },
  {
    label: '转办率',
    value: percent(summary.value.transfer_rate),
    sub: `转办 ${summary.value.transfers || 0} 次`,
    icon: Switch,
    color: '#ef4444'
  },
  {
    label: '缓存命中率',
    value: percent(summary.value.cache_hit_rate),
    sub: `命中 ${summary.value.cache_hits || 0} 次`,
    icon: Lightning,
    color: '#6366f1'
  },
  {
    label: 'P95 耗时',
    value: summary.value.p95_ms || 0,
    unit: '毫秒',
    sub: `P50 ${summary.value.p50_ms || 0} 毫秒`,
    icon: Timer,
    color: '#06b6d4'
  }
])

const trendSeries = computed(() => {
  const source = new Map(trends.value.map((item) => [String(item.date), item]))
  const rows = []
  for (let offset = days.value - 1; offset >= 0; offset -= 1) {
    const key = dateKey(offset)
    const item = source.get(key) || {}
    rows.push({
      date: key.slice(5),
      questions: Number(item.questions || 0),
      cache_hits: Number(item.cache_hits || 0)
    })
  }
  return rows
})

const chart = computed(() => {
  const width = 760
  const height = 340
  const left = 42
  const right = 14
  const top = 14
  const bottom = 30
  const plotW = width - left - right
  const plotH = height - top - bottom
  const items = trendSeries.value
  const maxValue = Math.max(1, ...items.map((item) => Math.max(item.questions, item.cache_hits)))
  const scaleMax = maxValue <= 5 ? 5 : Math.ceil(maxValue / 5) * 5
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    y: top + plotH * (1 - ratio),
    value: Math.round(scaleMax * ratio)
  }))
  const slot = plotW / Math.max(items.length, 1)
  const barW = Math.max(Math.min(slot * 0.24, 16), 3)
  const bars = []
  items.forEach((item, index) => {
    const center = left + slot * index + slot / 2
    const questionHeight = (item.questions / scaleMax) * plotH
    const cacheHeight = (item.cache_hits / scaleMax) * plotH
    bars.push({
      x: center - barW - 2,
      y: top + plotH - questionHeight,
      w: barW,
      h: questionHeight,
      color: '#0f9d8f',
      label: item.date,
      name: '提问量',
      value: item.questions
    })
    bars.push({
      x: center + 2,
      y: top + plotH - cacheHeight,
      w: barW,
      h: cacheHeight,
      color: '#60a5fa',
      label: item.date,
      name: '缓存命中',
      value: item.cache_hits
    })
  })
  const step = Math.max(Math.ceil(items.length / 7), 1)
  const labels = []
  items.forEach((item, index) => {
    if (index % step === 0) {
      labels.push({ x: left + slot * index + slot / 2, text: item.date })
    }
  })
  return { width, height, left, top, plotW, plotH, ticks, bars, labels }
})

const useDepartments = computed(
  () => auth.isAdmin && departmentRows.value.some((item) => Number(item.questions) > 0)
)

const donutTitle = computed(() => (useDepartments.value ? '部门提问占比' : '引用质量构成'))

const donutItems = computed(() => {
  let rows
  if (useDepartments.value) {
    rows = departmentRows.value
      .map((item) => ({ name: item.department_name || '未分配部门', value: Number(item.questions || 0) }))
      .filter((item) => item.value > 0)
  } else {
    const questions = Number(summary.value.questions || 0)
    const zero = Number(summary.value.zero_citation || 0)
    rows = [
      { name: '有引用', value: Math.max(questions - zero, 0) },
      { name: '零引用', value: zero }
    ].filter((item) => item.value > 0)
  }
  rows.sort((a, b) => b.value - a.value)
  const top = rows.slice(0, 5)
  const rest = rows.slice(5).reduce((total, item) => total + item.value, 0)
  if (rest > 0) {
    top.push({ name: '其他', value: rest })
  }
  const total = top.reduce((sum, item) => sum + item.value, 0)
  return top.map((item, index) => ({
    ...item,
    color: DONUT_COLORS[index % DONUT_COLORS.length],
    percent: total ? Math.round((item.value / total) * 1000) / 10 : 0
  }))
})

const donutTotal = computed(() => donutItems.value.reduce((sum, item) => sum + item.value, 0))

const donutGradient = computed(() => {
  if (!donutTotal.value) {
    return '#f1f5f9'
  }
  let cursor = 0
  const parts = donutItems.value.map((item) => {
    const start = (cursor / donutTotal.value) * 360
    cursor += item.value
    const end = (cursor / donutTotal.value) * 360
    return `${item.color} ${start}deg ${end}deg`
  })
  return `conic-gradient(${parts.join(', ')})`
})

const pendingItems = computed(() => [
  { label: '待处理反馈', value: summary.value.pending_feedback || 0, color: '#f59e0b' },
  { label: '过期文档', value: summary.value.stale_documents || 0, color: '#ef4444' },
  { label: '未解决缺口', value: summary.value.open_gaps || 0, color: '#6366f1' },
  { label: '反馈率', value: percent(summary.value.feedback_rate), color: '#0f9d8f' }
])

async function load() {
  try {
    const params = { days: days.value, department_id: departmentId.value || undefined }
    const [summaryData, trendData, departmentData] = await Promise.all([
      getDashboardSummary(params),
      getDashboardTrends(params),
      auth.isAdmin
        ? getDepartmentComparison(params)
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
    reviewRows.value = (data.items || []).slice(0, 100)
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

onActivated(async () => {
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

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.metric-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 12px 14px;
  min-width: 0;
}

.metric-top {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #909399;
  font-size: 13px;
}

.metric-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
}

.metric-value {
  margin-top: 8px;
  font-size: 26px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1.15;
  white-space: nowrap;
}

.metric-unit {
  margin-left: 4px;
  font-size: 13px;
  font-weight: 500;
  color: #909399;
}

.metric-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #9ca3af;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-main {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr);
  gap: 12px;
}

.panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 16px;
  min-width: 0;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.panel-title::before {
  content: '';
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: #0f9d8f;
}

.legend {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: #6b7280;
}

.legend span,
.legend-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.legend i,
.legend-name i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.chart-svg {
  width: 100%;
  height: 340px;
  display: block;
}

.axis-text {
  font-size: 11px;
  fill: #9ca3af;
}

.donut-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 320px;
}

.donut {
  position: relative;
  width: 200px;
  height: 200px;
  border-radius: 50%;
}

.donut::after {
  content: '';
  position: absolute;
  inset: 28px;
  background: #fff;
  border-radius: 50%;
}

.donut-center {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.donut-center strong {
  font-size: 24px;
  color: #1f2937;
  line-height: 1.1;
}

.donut-center span {
  margin-top: 2px;
  font-size: 12px;
  color: #9ca3af;
}

.donut-legend {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 12px;
  font-size: 12px;
  color: #4b5563;
}

.legend-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.legend-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legend-value {
  color: #909399;
  white-space: nowrap;
}

.pending-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.pending-card {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 10px 14px;
}

.pending-bar {
  width: 4px;
  height: 28px;
  border-radius: 2px;
}

.pending-label {
  font-size: 12px;
  color: #909399;
}

.pending-value {
  margin-top: 2px;
  font-size: 20px;
  font-weight: 600;
  color: #1f2937;
  line-height: 1.1;
}

.review-panel {
  padding-bottom: 8px;
}

.count {
  font-size: 12px;
  font-weight: 400;
  color: #9ca3af;
}

.review-actions {
  display: flex;
  gap: 8px;
}

.empty {
  padding: 24px 0;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}

.empty.tall {
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

@media (max-width: 1280px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1024px) {
  .dashboard-main {
    grid-template-columns: minmax(0, 1fr);
  }

  .pending-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
