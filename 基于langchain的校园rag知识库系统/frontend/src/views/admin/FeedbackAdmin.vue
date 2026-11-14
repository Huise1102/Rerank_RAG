<template>
  <div class="page feedback-page">
    <div class="metric-grid">
      <div v-for="item in metrics" :key="item.label" class="metric-card">
        <div class="metric-top">
          <span class="metric-icon" :style="{ color: item.color, backgroundColor: item.color + '14' }">
            <el-icon><component :is="item.icon" /></el-icon>
          </span>
          <span>{{ item.label }}</span>
        </div>
        <div class="metric-value" :style="item.warn ? { color: item.color } : null">
          {{ item.value }}
        </div>
        <div class="metric-sub">{{ item.sub }}</div>
      </div>
    </div>

    <section class="panel status-panel">
      <div class="panel-head">
        <div class="panel-title">
          工单状态构成
          <span class="count">共 {{ stats.total }} 件</span>
        </div>
      </div>
      <div class="status-bar">
        <span
          v-for="item in statusItems"
          v-show="item.value"
          :key="item.name"
          :style="{ width: item.width + '%', background: item.color }"
          :title="`${item.name} ${item.value} 件`"
        />
      </div>
      <div class="status-legend">
        <div v-for="item in statusItems" :key="item.name" class="status-legend-item">
          <i :style="{ background: item.color }" />
          <span>{{ item.name }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>
    </section>

    <section class="panel list-panel">
      <div class="panel-head">
        <div class="panel-title">
          工单列表
          <span class="count">共 {{ total }} 条</span>
        </div>
        <el-button size="small" @click="load">刷新</el-button>
      </div>

      <div class="filter-bar">
        <el-select v-model="filters.kind" placeholder="工单来源" clearable style="width: 130px" @change="search">
          <el-option label="用户反馈" value="feedback" />
          <el-option label="转人工" value="transfer" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px" @change="search">
          <el-option v-for="(label, value) in statusLabels" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-model="filters.feedback_type" placeholder="类型" clearable style="width: 170px" @change="search">
          <el-option v-for="item in types" :key="item" :label="item" :value="item" />
        </el-select>
        <el-input v-model="filters.submitter" placeholder="提交人" clearable style="width: 140px" @keyup.enter="search" />
        <el-input v-model="filters.keyword" placeholder="工单号或关键词" clearable style="width: 220px" @keyup.enter="search" />
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <el-table :data="items" size="small" border max-height="430" @row-click="openDetail">
      <el-table-column prop="ticket_no" label="工单号" width="180" />
      <el-table-column label="来源" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.kind === 'transfer' ? 'warning' : 'info'">
            {{ row.kind === 'transfer' ? '转人工' : '反馈' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="feedback_type" label="类型" width="150" />
      <el-table-column prop="question_snapshot" label="问题摘要" show-overflow-tooltip />
      <el-table-column label="提交人" width="120">
        <template #default="{ row }">{{ row.submitter.display_name || row.submitter.username }}</template>
      </el-table-column>
      <el-table-column prop="kb_name" label="知识库" width="140" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ statusLabels[row.status] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="处理人" width="110">
        <template #default="{ row }">{{ row.handler_id ? '管理员' : '未受理' }}</template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top: 12px"
      @current-change="load"
    />
    </section>

    <el-drawer v-model="drawer" title="反馈详情" size="620px">
      <div v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="工单号">{{ detail.ticket_no }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabels[detail.status] }}</el-descriptions-item>
          <el-descriptions-item label="提交人">
            {{ detail.submitter.display_name }}（{{ detail.submitter.username }}）
          </el-descriptions-item>
          <el-descriptions-item label="联系方式">{{ detail.contact || '未填写' }}</el-descriptions-item>
          <el-descriptions-item label="知识库">{{ detail.kb_name || '未关联' }}</el-descriptions-item>
          <el-descriptions-item label="策略">{{ detail.strategy || '未记录' }}</el-descriptions-item>
          <el-descriptions-item label="转办部门">{{ departmentName(detail.assigned_department_id) }}</el-descriptions-item>
          <el-descriptions-item label="首次响应">{{ formatTime(detail.first_response_at) || '未响应' }}</el-descriptions-item>
        </el-descriptions>
        <p v-if="detail.transfer_reason" class="muted">转办说明：{{ detail.transfer_reason }}</p>

        <h4>问题描述</h4>
        <p class="block">{{ detail.description }}</p>
        <h4 v-if="detail.expected_answer">期望答案</h4>
        <p v-if="detail.expected_answer" class="block">{{ detail.expected_answer }}</p>
        <h4>原始问题</h4>
        <p class="block">{{ detail.question_snapshot || '无' }}</p>
        <h4>系统答案</h4>
        <p class="block">{{ detail.answer_snapshot || '无' }}</p>
        <h4>引用列表</h4>
        <div v-for="cite in detail.citations_snapshot || []" :key="cite.index" class="block">
          [{{ cite.index }}] {{ cite.filename }}<span v-if="cite.page"> 第 {{ cite.page }} 页</span>
          <div>{{ cite.snippet }}</div>
        </div>
        <h4>处理时间线</h4>
        <el-timeline>
          <el-timeline-item
            v-for="event in detail.events || []"
            :key="event.id"
            :timestamp="formatTime(event.created_at)"
          >
            {{ event.operator_name }}：{{ statusLabels[event.to_status] }}
            <div v-if="event.note" class="muted">{{ event.note }}</div>
          </el-timeline-item>
        </el-timeline>
        <h4>给用户的回复</h4>
        <p class="block">{{ detail.reply || '暂无' }}</p>
        <p v-if="detail.reject_reason" class="muted">驳回原因：{{ detail.reject_reason }}</p>
        <p v-if="detail.handle_note" class="muted">内部备注：{{ detail.handle_note }}</p>

        <div style="margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap">
          <el-button v-if="detail.status === 'pending'" type="primary" @click="accept">受理</el-button>
          <el-button
            v-if="['pending', 'processing', 'replied'].includes(detail.status)"
            @click="openAction('reply')"
          >
            回复
          </el-button>
          <el-button
            v-if="['pending', 'processing', 'replied'].includes(detail.status)"
            @click="openAction('reply_resolve')"
          >
            回复并解决
          </el-button>
          <el-button v-if="['pending', 'processing', 'replied'].includes(detail.status)" @click="openAction('resolve')">
            解决
          </el-button>
          <el-button
            v-if="['pending', 'processing', 'replied'].includes(detail.status)"
            type="danger"
            @click="openAction('reject')"
          >
            驳回
          </el-button>
          <el-button @click="openAction('forward')">转办</el-button>
          <el-button v-if="['resolved', 'rejected'].includes(detail.status)" @click="openAction('reopen')">
            重新打开
          </el-button>
          <el-button @click="openAction('note')">内部备注</el-button>
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="actionDialog" :title="actionTitle" width="480px">
      <el-form label-position="top">
        <el-form-item v-if="['reply', 'reply_resolve', 'resolve'].includes(actionForm.action)" label="用户可见回复">
          <el-input v-model="actionForm.reply" type="textarea" :rows="4" maxlength="2000" />
        </el-form-item>
        <el-form-item v-if="actionForm.action === 'reject'" label="驳回原因" required>
          <el-input v-model="actionForm.reject_reason" type="textarea" :rows="3" maxlength="1000" />
        </el-form-item>
        <el-form-item v-if="actionForm.action === 'forward'" label="转办部门" required>
          <el-select v-model="actionForm.assigned_department_id" style="width: 100%">
            <el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="['note', 'resolve', 'forward'].includes(actionForm.action)" label="内部备注">
          <el-input v-model="actionForm.note" type="textarea" :rows="3" maxlength="1000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="actionDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitAction">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Bell,
  Calendar,
  ChatDotRound,
  Loading,
  Switch,
  Warning
} from '@element-plus/icons-vue'
import {
  adminFeedbackDetail,
  adminFeedbackList,
  adminFeedbackStats,
  handleFeedback
} from '../../api/feedback'
import { listDepartments } from '../../api/admin'

const statusLabels = {
  pending: '待处理',
  processing: '处理中',
  replied: '已回复',
  resolved: '已解决',
  rejected: '已驳回'
}
const types = ['答案错误', '引用错误或缺失', '回答不完整', '知识库无相关内容', '内容过期', '转人工', '其他']

const departments = ref([])
const stats = reactive({
  pending: 0,
  processing: 0,
  replied: 0,
  resolved: 0,
  rejected: 0,
  total: 0,
  today_new: 0,
  today_resolved: 0,
  transfer_pending: 0,
  overdue: 0
})
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filters = reactive({ kind: '', status: '', feedback_type: '', submitter: '', keyword: '' })
const drawer = ref(false)
const detail = ref(null)
const actionDialog = ref(false)
const submitting = ref(false)
const actionForm = reactive({
  action: 'note',
  reply: '',
  reject_reason: '',
  note: '',
  assigned_department_id: null
})

const actionTitle = computed(
  () =>
    ({
      reply: '回复用户',
      reply_resolve: '回复并解决',
      resolve: '解决反馈',
      reject: '驳回反馈',
      forward: '转办工单',
      reopen: '重新打开',
      note: '内部备注'
    })[actionForm.action] || '处理反馈'
)

const metrics = computed(() => [
  {
    label: '待处理',
    value: stats.pending,
    sub: '等待受理',
    icon: Bell,
    color: '#f59e0b'
  },
  {
    label: '处理中',
    value: stats.processing,
    sub: '已受理，处理中',
    icon: Loading,
    color: '#3b82f6'
  },
  {
    label: '已回复',
    value: stats.replied,
    sub: '等待用户确认',
    icon: ChatDotRound,
    color: '#0f9d8f'
  },
  {
    label: '转办待受理',
    value: stats.transfer_pending,
    sub: '需部门接单',
    icon: Switch,
    color: '#6366f1'
  },
  {
    label: '超时工单',
    value: stats.overdue,
    sub: '超过响应时限',
    icon: Warning,
    color: '#ef4444',
    warn: true
  },
  {
    label: '今日新增',
    value: stats.today_new,
    sub: `今日已解决 ${stats.today_resolved}`,
    icon: Calendar,
    color: '#06b6d4'
  }
])

const statusItems = computed(() => {
  const rows = [
    { name: '待处理', value: stats.pending, color: '#f59e0b' },
    { name: '处理中', value: stats.processing, color: '#3b82f6' },
    { name: '已回复', value: stats.replied, color: '#0f9d8f' },
    { name: '已解决', value: stats.resolved, color: '#22c55e' },
    { name: '已驳回', value: stats.rejected, color: '#ef4444' }
  ]
  const total = rows.reduce((sum, item) => sum + Number(item.value || 0), 0)
  return rows.map((item) => ({
    ...item,
    width: total ? (Number(item.value || 0) / total) * 100 : 0
  }))
})

function statusType(status) {
  return {
    pending: 'warning',
    processing: 'primary',
    replied: 'success',
    resolved: 'success',
    rejected: 'danger'
  }[status] || 'info'
}

function departmentName(id) {
  if (!id) {
    return '未分配'
  }
  return departments.value.find((item) => item.id === id)?.name || `部门 ${id}`
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : ''
}

async function load() {
  const [list, stat] = await Promise.all([
    adminFeedbackList({
      page: page.value,
      page_size: pageSize,
      kind: filters.kind || undefined,
      status: filters.status || undefined,
      feedback_type: filters.feedback_type || undefined,
      submitter: filters.submitter || undefined,
      keyword: filters.keyword || undefined
    }),
    adminFeedbackStats()
  ])
  items.value = list.items
  total.value = list.total
  Object.assign(stats, stat)
}

function search() {
  page.value = 1
  load()
}

function reset() {
  filters.kind = ''
  filters.status = ''
  filters.feedback_type = ''
  filters.submitter = ''
  filters.keyword = ''
  search()
}

async function openDetail(row) {
  detail.value = await adminFeedbackDetail(row.id)
  drawer.value = true
}

async function refreshDetail() {
  if (detail.value) {
    detail.value = await adminFeedbackDetail(detail.value.id)
  }
  await load()
}

async function accept() {
  await handleFeedback(detail.value.id, { action: 'accept' })
  ElMessage.success('已受理')
  await refreshDetail()
}

function openAction(action) {
  actionForm.action = action
  actionForm.reply = ''
  actionForm.reject_reason = ''
  actionForm.note = ''
  actionForm.assigned_department_id = detail.value?.assigned_department_id || null
  actionDialog.value = true
}

async function submitAction() {
  if (actionForm.action === 'reject' && !actionForm.reject_reason.trim()) {
    ElMessage.warning('驳回必须填写原因')
    return
  }
  if (['reply', 'reply_resolve'].includes(actionForm.action) && !actionForm.reply.trim()) {
    ElMessage.warning('请填写用户可见回复')
    return
  }
  if (actionForm.action === 'forward' && !actionForm.assigned_department_id) {
    ElMessage.warning('请选择转办部门')
    return
  }
  submitting.value = true
  try {
    await handleFeedback(detail.value.id, {
      action: actionForm.action,
      reply: actionForm.reply || null,
      reject_reason: actionForm.reject_reason || null,
      note: actionForm.note || null,
      assigned_department_id: actionForm.assigned_department_id || null
    })
    ElMessage.success('处理完成')
    actionDialog.value = false
    await refreshDetail()
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  departments.value = await listDepartments()
  await load()
})
</script>

<style scoped>
.feedback-page {
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
  line-height: 1.15;
}

.metric-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #9ca3af;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  margin-bottom: 10px;
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

.count {
  font-size: 12px;
  font-weight: 400;
  color: #9ca3af;
}

.status-bar {
  display: flex;
  height: 10px;
  border-radius: 5px;
  overflow: hidden;
  background: #f1f5f9;
}

.status-bar span {
  display: block;
  height: 100%;
}

.status-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin-top: 10px;
  font-size: 12px;
  color: #4b5563;
}

.status-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-legend-item i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.status-legend-item strong {
  color: #1f2937;
  font-weight: 600;
}

.filter-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.block {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 10px 12px;
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
}

@media (max-width: 1280px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
