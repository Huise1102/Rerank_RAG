<template>
  <el-dialog
    :model-value="modelValue"
    title="提交反馈"
    width="520px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="question" class="context-box">关联问题：{{ question }}</div>
    <el-form :model="form" label-position="top">
      <el-form-item label="反馈类型" required>
        <el-select v-model="form.feedbackType" style="width: 100%">
          <el-option v-for="item in types" :key="item" :label="item" :value="item" />
        </el-select>
      </el-form-item>
      <el-form-item label="问题描述" required>
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="4"
          maxlength="1000"
          show-word-limit
          placeholder="请说明具体问题，例如答案错误、引用缺失或内容过期"
        />
      </el-form-item>
      <el-form-item label="期望答案或补充说明">
        <el-input v-model="form.expectedAnswer" type="textarea" :rows="2" maxlength="1000" show-word-limit />
      </el-form-item>
      <el-form-item label="联系方式">
        <el-input v-model="form.contact" maxlength="100" placeholder="可选，便于回访" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">提交</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { submitFeedback } from '../api/chat'
import { useAuthStore } from '../stores/auth'

const props = defineProps({
  modelValue: Boolean,
  messageId: { type: Number, default: null },
  conversationId: { type: Number, default: null },
  question: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue', 'submitted'])
const auth = useAuthStore()

const types = ['答案错误', '引用错误或缺失', '回答不完整', '知识库无相关内容', '制度过期', '其他']
const form = reactive({
  feedbackType: '答案错误',
  description: '',
  expectedAnswer: '',
  contact: ''
})
const loading = ref(false)

async function submit() {
  if (form.description.trim().length < 10) {
    ElMessage.warning('问题描述至少 10 个字')
    return
  }
  loading.value = true
  try {
    const result = await submitFeedback({
      assistant_message_id: props.messageId,
      conversation_id: props.conversationId,
      feedback_type: form.feedbackType,
      description: form.description,
      expected_answer: form.expectedAnswer || null,
      contact: form.contact || auth.user?.display_name || null
    })
    ElMessage.success(`提交成功，工单号 ${result.ticket_no}`)
    await ElMessageBox.alert(`工单号：${result.ticket_no}\n可在「我的反馈」查看处理进度。`, '提交成功')
    form.description = ''
    form.expectedAnswer = ''
    emit('update:modelValue', false)
    emit('submitted', result)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.context-box {
  background: #f4f6f9;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
  color: #606266;
  margin-bottom: 14px;
  max-height: 90px;
  overflow: hidden;
}
</style>
