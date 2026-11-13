<template>
  <div class="trace-panel">
    <div class="muted" style="margin-bottom: 12px">
      共 {{ rounds || trace.length }} 轮决策，{{ trace.length }} 个步骤
    </div>
    <el-timeline v-if="trace.length">
      <el-timeline-item
        v-for="(step, index) in trace"
        :key="index"
        :timestamp="step.round ? `第 ${step.round} 轮` : '兜底'"
        :type="stepType(step.action)"
      >
        <div class="trace-title">{{ actionLabel(step.action) }}</div>
        <div v-if="step.query" class="trace-row">检索词：{{ step.query }}</div>
        <div v-if="step.strategy" class="trace-row">检索策略：{{ step.strategy }}</div>
        <div v-if="step.hits !== undefined" class="trace-row">
          召回 {{ step.hits }} 条，新增 {{ step.new_evidence ?? 0 }} 条，耗时 {{ step.duration_ms ?? 0 }} 毫秒
        </div>
        <div v-if="step.evidence_count !== undefined" class="trace-row">
          累计证据 {{ step.evidence_count }} 条
        </div>
        <div v-if="step.note" class="trace-row muted">{{ step.note }}</div>
      </el-timeline-item>
    </el-timeline>
    <div v-else class="muted">暂无决策轨迹</div>
  </div>
</template>

<script setup>
defineProps({
  trace: { type: Array, default: () => [] },
  rounds: { type: Number, default: 0 }
})

function actionLabel(action) {
  return {
    retrieve: '检索制度原文',
    answer: '生成回答',
    clarify: '请求补充信息',
    fallback: '回退快速问答'
  }[action] || action
}

function stepType(action) {
  if (action === 'retrieve') return 'primary'
  if (action === 'clarify') return 'warning'
  if (action === 'fallback') return 'danger'
  return 'success'
}
</script>

<style scoped>
.trace-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.trace-row {
  font-size: 13px;
  line-height: 1.7;
  color: #606266;
}
</style>
