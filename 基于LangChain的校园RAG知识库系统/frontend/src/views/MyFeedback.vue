<template>
  <div class="page" style="height: 100%; overflow-y: auto">
    <div class="page-header">
      <div class="page-title">我的反馈</div>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="items" size="small" border>
      <el-table-column prop="ticket_no" label="工单号" width="190" />
      <el-table-column label="来源" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.kind === 'transfer' ? 'warning' : 'info'">
            {{ row.kind === 'transfer' ? '转人工' : '反馈' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="feedback_type" label="类型" width="140" />
      <el-table-column prop="description" label="问题描述" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="提交时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="items.length === 0" class="empty-hint">暂无反馈记录</div>

    <el-dialog v-model="detailVisible" title="反馈详情" width="680px">
      <div v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="工单号">{{ detail.ticket_no }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(detail.status) }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ detail.feedback_type }}</el-descriptions-item>
          <el-descriptions-item label="提交时间">{{ formatTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="知识库">{{ detail.kb_name || '未关联' }}</el-descriptions-item>
          <el-descriptions-item label="策略">{{ detail.strategy || '未记录' }}</el-descriptions-item>
          <el-descriptions-item label="首次响应">{{ formatTime(detail.first_response_at) || '待响应' }}</el-descriptions-item>
          <el-descriptions-item label="转办说明">{{ detail.transfer_reason || '无' }}</el-descriptions-item>
        </el-descriptions>
        <p class="muted">人工转办承诺：1 个工作日内首次响应，3 个工作日内办结。</p>

        <h4>问题描述</h4>
        <p class="detail-text">{{ detail.description }}</p>
        <h4 v-if="detail.expected_answer">期望答案</h4>
        <p v-if="detail.expected_answer" class="detail-text">{{ detail.expected_answer }}</p>
        <h4>原问题</h4>
        <p class="detail-text">{{ detail.question_snapshot || '无' }}</p>
        <h4>系统答案</h4>
        <p class="detail-text">{{ detail.answer_snapshot || '无' }}</p>
        <h4>管理员回复</h4>
        <p class="detail-text">{{ detail.reply || '暂无回复' }}</p>
        <p v-if="detail.reject_reason" class="muted">驳回原因：{{ detail.reject_reason }}</p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onActivated, ref } from 'vue'
import { myFeedback, myFeedbackDetail } from '../api/feedback'

const items = ref([])
const detailVisible = ref(false)
const detail = ref(null)

const statusMap = {
  pending: ['待处理', 'warning'],
  processing: ['处理中', 'primary'],
  replied: ['已回复', 'success'],
  resolved: ['已解决', 'success'],
  rejected: ['已驳回', 'danger']
}

function statusLabel(status) {
  return statusMap[status]?.[0] || status
}

function statusType(status) {
  return statusMap[status]?.[1] || 'info'
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : ''
}

async function load() {
  items.value = await myFeedback()
}

async function showDetail(row) {
  detail.value = await myFeedbackDetail(row.id)
  detailVisible.value = true
}

onActivated(load)
</script>

<style scoped>
.detail-text {
  background: #f7f8fa;
  padding: 10px 12px;
  border-radius: 6px;
  white-space: pre-wrap;
  line-height: 1.7;
}
</style>
