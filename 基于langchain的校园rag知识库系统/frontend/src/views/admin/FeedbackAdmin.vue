<template>
  <div class="page">
    <div class="stat-grid">
      <div class="stat-card">
        <div class="muted">待处理</div>
        <div class="stat-value">{{ stats.pending }}</div>
      </div>
      <div class="stat-card">
        <div class="muted">处理中</div>
        <div class="stat-value">{{ stats.processing }}</div>
      </div>
      <div class="stat-card">
        <div class="muted">已回复</div>
        <div class="stat-value">{{ stats.replied }}</div>
      </div>
      <div class="stat-card">
        <div class="muted">转办待受理</div>
        <div class="stat-value">{{ stats.transfer_pending }}</div>
      </div>
      <div class="stat-card">
        <div class="muted">超时工单</div>
        <div class="stat-value" :style="{ color: stats.overdue ? '#f56c6c' : '#303133' }">{{ stats.overdue }}</div>
      </div>
      <div class="stat-card">
        <div class="muted">今日新增</div>
        <div class="stat-value">{{ stats.today_new }}</div>
      </div>
    </div>

    <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap">
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

    <el-table :data="items" size="small" border @row-click="openDetail">
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
.block {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 10px 12px;
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
}
</style>
