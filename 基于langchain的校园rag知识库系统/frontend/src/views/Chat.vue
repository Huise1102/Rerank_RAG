<template>
  <div class="chat-layout">
    <aside class="conversation-panel">
      <div class="conversation-header">
        <el-button type="primary" style="width: 100%" @click="newConversation">新建会话</el-button>
      </div>
      <div class="conversation-list">
        <div
          v-for="item in conversations"
          :key="item.id"
          class="conversation-item"
          :class="{ active: item.id === activeId }"
          @click="openConversation(item.id)"
        >
          <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ item.title }}</span>
          <el-icon @click.stop="removeConversation(item)"><Delete /></el-icon>
        </div>
        <div v-if="conversations.length === 0" class="muted" style="padding: 12px">暂无历史会话</div>
      </div>
    </aside>

    <section class="chat-main">
      <div class="chat-toolbar">
        <el-select v-model="kbId" placeholder="选择知识库" style="width: 220px" :disabled="sending">
          <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>
        <el-select v-model="strategyId" placeholder="问答策略" style="width: 180px" :disabled="sending">
          <el-option v-for="item in strategies" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-tag v-if="currentStrategy" type="info">{{ currentStrategy.description }}</el-tag>
        <span v-if="kbList.length === 0" class="muted">暂无可用的已发布知识库，请联系管理员</span>
        <el-button link style="margin-left: auto" @click="toggleDetail">
          {{ detailVisible ? '收起检索详情' : '检索详情' }}
        </el-button>
      </div>

      <div ref="messageListRef" class="message-list">
        <div v-if="messages.length === 0" class="empty-hint">
          <p>请输入校园制度相关问题</p>
          <p class="muted">回答会标注引用来源，可点击「检索详情」查看召回过程</p>
        </div>
        <div v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
          <div v-if="message.role === 'assistant'" class="message-role">回答</div>
          <div class="bubble" :class="message.role === 'assistant' ? 'bubble-assistant' : 'bubble-user'">
            <template v-if="message.role === 'assistant'">
              <AnswerContent
                :content="message.content"
                :citations="message.citations || []"
                @cite="(indices) => focusCitation(index, indices)"
              />
              <span v-if="message.streaming" class="streaming-cursor">▋</span>
            </template>
            <div v-else class="user-text">{{ message.content }}</div>
            <div v-if="message.citations && message.citations.length" class="citation-list">
              <div
                v-for="cite in message.citations"
                :id="`cite-${index}-${cite.index}`"
                :key="cite.index"
                class="citation-card"
              >
                <div class="citation-head">
                  <span class="citation-index">[{{ cite.index }}]</span>
                  <span>{{ cite.filename }}</span>
                  <span v-if="cite.page" class="citation-page">第 {{ cite.page }} 页</span>
                </div>
                <div class="citation-text">{{ cite.snippet }}</div>
              </div>
            </div>
            <div v-if="message.role === 'assistant' && message.strategy" class="message-strategy">
              策略：{{ message.strategy }}
            </div>
            <div v-if="message.role === 'assistant' && !message.streaming" class="message-actions">
              <el-button v-if="message.id" link size="small" @click="submitFeedback(message)">反馈</el-button>
              <el-button v-if="message.id" link size="small" @click="showRetrieval(message)">检索详情</el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          maxlength="2000"
          show-word-limit
          placeholder="请输入问题，按 Ctrl 加 Enter 发送"
          :disabled="sending"
          @keydown.ctrl.enter.prevent="send"
        />
        <div class="chat-input-actions">
          <span class="muted">回答仅依据知识库内容，未覆盖时会明确说明</span>
          <div>
            <el-button @click="openGeneralFeedback">疑难提交</el-button>
            <el-button type="primary" :loading="sending" @click="send">发送</el-button>
          </div>
        </div>
      </div>
    </section>

    <aside v-if="detailVisible" class="detail-panel">
      <div class="detail-head">
        <span>检索详情</span>
      </div>
      <div class="detail-body">
        <template v-if="detail">
          <div class="detail-strategy">{{ detail.strategy }}</div>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="向量召回">{{ detail.vector_hits }}</el-descriptions-item>
            <el-descriptions-item label="关键词召回">{{ detail.bm25_hits }}</el-descriptions-item>
            <el-descriptions-item label="融合召回">{{ detail.fused_hits }}</el-descriptions-item>
            <el-descriptions-item label="精排数量">{{ detail.reranked_hits }}</el-descriptions-item>
            <el-descriptions-item label="是否精排">{{ detail.rerank ? '是' : '否' }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ detail.duration_ms }} 毫秒</el-descriptions-item>
          </el-descriptions>
          <div class="detail-sub">召回文档</div>
          <div v-for="doc in detail.documents" :key="doc.index" class="citation-card" style="margin-bottom: 8px">
            [{{ doc.index }}] {{ doc.filename }}<span v-if="doc.page"> 第 {{ doc.page }} 页</span>
            <div style="margin-top: 4px">{{ doc.preview }}</div>
          </div>
          <div v-if="!detail.documents || detail.documents.length === 0" class="muted">本次未召回文档</div>
        </template>
        <div v-else class="muted detail-empty">
          点击回答下方的「检索详情」查看该次回答的召回过程
        </div>
      </div>
    </aside>

    <FeedbackDialog
      v-model="feedbackVisible"
      :message-id="feedbackTarget?.id || null"
      :conversation-id="activeId"
      :question="feedbackQuestion"
      @submitted="onFeedbackSubmitted"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import {
  createConversation,
  deleteConversation,
  getRetrievalDetail,
  listConversations,
  listKnowledgeBases,
  listMessages,
  listStrategies
} from '../api/chat'
import AnswerContent from '../components/AnswerContent.vue'
import FeedbackDialog from '../components/FeedbackDialog.vue'

const adminMode = import.meta.env.VITE_APP_MODE === 'admin'
const appKey = adminMode ? 'admin' : 'user'
const apiBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'

const conversations = ref([])
const messages = ref([])
const kbList = ref([])
const strategies = ref([])
const kbId = ref(null)
const strategyId = ref(null)
const activeId = ref(null)
const input = ref('')
const sending = ref(false)
const messageListRef = ref(null)
const feedbackVisible = ref(false)
const feedbackTarget = ref(null)
const feedbackQuestion = ref('')
const detailVisible = ref(false)
const detail = ref(null)

const currentStrategy = computed(() => strategies.value.find((item) => item.id === strategyId.value))

function focusCitation(messageIndex, indices) {
  const target = document.getElementById(`cite-${messageIndex}-${indices[0]}`)
  if (!target) {
    return
  }
  target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  target.classList.add('citation-active')
  window.setTimeout(() => target.classList.remove('citation-active'), 1600)
}

function scrollToBottom() {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}

async function loadBaseData() {
  const [kbs, strategyList] = await Promise.all([listKnowledgeBases(), listStrategies()])
  kbList.value = kbs
  strategies.value = strategyList
  if (kbs.length > 0 && !kbId.value) {
    kbId.value = kbs[0].id
  }
  const preferred = strategyList.find((item) => item.is_default) || strategyList[0]
  if (preferred && !strategyId.value) {
    strategyId.value = preferred.id
  }
}

async function loadConversations() {
  conversations.value = await listConversations(appKey)
}

async function openConversation(id) {
  activeId.value = id
  detail.value = null
  detailVisible.value = false
  const data = await listMessages(id)
  messages.value = data.messages.map((item) => ({
    id: item.id,
    role: item.role,
    content: item.content,
    citations: item.citations || [],
    strategy: item.role === 'assistant' ? strategies.value.find((preset) => preset.id === item.strategy_id)?.name : ''
  }))
  if (data.conversation.kb_id) {
    kbId.value = data.conversation.kb_id
  }
  scrollToBottom()
}

function newConversation() {
  activeId.value = null
  messages.value = []
  input.value = ''
  detail.value = null
  detailVisible.value = false
}

async function removeConversation(item) {
  try {
    await ElMessageBox.confirm(`确认删除会话「${item.title}」？`, '删除会话', { type: 'warning' })
  } catch {
    return
  }
  await deleteConversation(item.id)
  if (activeId.value === item.id) {
    newConversation()
  }
  await loadConversations()
  ElMessage.success('会话已删除')
}

async function send() {
  const question = input.value.trim()
  if (!question) {
    ElMessage.warning('请输入问题')
    return
  }
  if (!kbId.value) {
    ElMessage.warning('请选择知识库')
    return
  }
  if (sending.value) {
    return
  }
  input.value = ''
  sending.value = true
  messages.value.push({ role: 'user', content: question })
  const assistant = { role: 'assistant', content: '', citations: [], streaming: true, id: null, strategy: '' }
  messages.value.push(assistant)
  scrollToBottom()

  const token = sessionStorage.getItem('token')
  let response
  try {
    response = await fetch(`${apiBase}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        question,
        kb_id: kbId.value,
        conversation_id: activeId.value,
        strategy_id: strategyId.value,
        app_key: appKey
      })
    })
  } catch {
    assistant.streaming = false
    assistant.content = '无法连接后端服务，请稍后重试'
    sending.value = false
    return
  }

  if (!response.ok) {
    let message = '回答失败，请稍后重试'
    try {
      const payload = await response.json()
      message = payload.detail || message
    } catch {
      // 响应体不是 JSON 时使用默认提示
    }
    assistant.streaming = false
    assistant.content = message
    sending.value = false
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let index = buffer.indexOf('\n\n')
      while (index >= 0) {
        const chunk = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 2)
        if (chunk.startsWith('data:')) {
          const payload = JSON.parse(chunk.slice(5).trim())
          handleEvent(payload, assistant)
        }
        index = buffer.indexOf('\n\n')
      }
      scrollToBottom()
    }
  } catch {
    assistant.content += '\n连接中断，请重试'
  } finally {
    assistant.streaming = false
    sending.value = false
    await loadConversations()
    scrollToBottom()
  }
}

function handleEvent(payload, assistant) {
  if (payload.event === 'meta') {
    activeId.value = payload.conversation_id
    assistant.strategy = payload.strategy
  } else if (payload.event === 'delta') {
    assistant.content += payload.content
  } else if (payload.event === 'citations') {
    assistant.citations = payload.citations || []
  } else if (payload.event === 'done') {
    assistant.id = payload.message_id
    assistant.streaming = false
  } else if (payload.event === 'error') {
    assistant.content += `\n${payload.detail}`
    assistant.streaming = false
  }
}

function submitFeedback(message) {
  feedbackTarget.value = message
  const index = messages.value.indexOf(message)
  const previous = index > 0 ? messages.value[index - 1] : null
  feedbackQuestion.value = previous?.role === 'user' ? previous.content : ''
  feedbackVisible.value = true
}

function openGeneralFeedback() {
  feedbackTarget.value = null
  feedbackQuestion.value = ''
  feedbackVisible.value = true
}

async function onFeedbackSubmitted() {
  await loadConversations()
}

async function showRetrieval(message) {
  detail.value = null
  detailVisible.value = true
  const data = await getRetrievalDetail(message.id)
  detail.value = data.detail
}

async function toggleDetail() {
  if (detailVisible.value) {
    detailVisible.value = false
    return
  }
  detailVisible.value = true
  if (detail.value) {
    return
  }
  const last = [...messages.value].reverse().find((item) => item.role === 'assistant' && item.id)
  if (last) {
    const data = await getRetrievalDetail(last.id)
    detail.value = data.detail
  }
}

onMounted(async () => {
  try {
    await loadBaseData()
    await loadConversations()
    if (conversations.value.length > 0) {
      await openConversation(conversations.value[0].id)
    }
  } catch {
    ElMessage.error('加载基础数据失败')
  }
})
</script>

<style scoped>
.streaming-cursor {
  color: #1f6fd0;
  margin-left: 2px;
}

.detail-panel {
  width: 360px;
  flex: none;
  border-left: 1px solid #e4e7ed;
  background: #fff;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.detail-head {
  height: 48px;
  flex: none;
  padding: 0 16px;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.detail-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.detail-strategy {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}

.detail-sub {
  margin: 16px 0 8px;
  font-size: 13px;
  font-weight: 600;
}

.detail-empty {
  padding: 32px 0;
  text-align: center;
}

.message-strategy {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}
</style>
