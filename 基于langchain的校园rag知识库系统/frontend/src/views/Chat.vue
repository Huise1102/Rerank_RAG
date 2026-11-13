<template>
  <div class="chat-layout">
    <aside class="conversation-panel" :class="{ 'mobile-open': mobilePanelOpen }">
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
    <div v-if="mobilePanelOpen" class="mobile-overlay" @click="mobilePanelOpen = false" />

    <section class="chat-main">
      <div class="chat-toolbar">
        <el-button class="mobile-only" link @click="mobilePanelOpen = true">会话</el-button>
        <el-select v-model="kbId" placeholder="选择知识库" style="width: 220px" :disabled="sending" @change="onKbChange">
          <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>
        <el-select v-model="strategyId" placeholder="问答策略" style="width: 180px" :disabled="sending">
          <el-option v-for="item in strategies" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-switch
          v-if="deepAvailable"
          v-model="deepMode"
          active-text="深度问答"
          :disabled="sending"
        />
        <el-tag v-if="currentStrategy" type="info">{{ currentStrategy.description }}</el-tag>
        <span v-if="kbList.length === 0" class="muted">暂无可用的已发布知识库，请联系管理员</span>
        <el-button link style="margin-left: auto" @click="toggleDetail">
          {{ detailVisible ? '收起检索详情' : '检索详情' }}
        </el-button>
      </div>

      <div ref="messageListRef" class="message-list" @scroll="onMessageScroll">
        <div v-if="messages.length === 0" class="empty-hint">
          <p>请输入校园知识库相关问题</p>
          <p class="muted">回答会标注引用来源，可点击「检索详情」查看召回过程</p>
        </div>
        <div v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
          <div v-if="message.role === 'assistant'" class="message-role">
            <span>回答</span>
            <el-tag v-if="message.mode === 'deep'" size="small" type="warning">深度问答</el-tag>
            <el-tag v-if="message.cached" size="small" type="success">热点缓存</el-tag>
            <span v-if="message.agentStatus" class="muted agent-status">{{ message.agentStatus }}</span>
          </div>
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
            <div v-if="message.showCitations && message.citations && message.citations.length" class="citation-list">
              <div
                v-for="cite in message.citations"
                :id="`cite-${index}-${cite.index}`"
                :key="cite.index"
                class="citation-card citation-clickable"
                @click="handleCitationClick(cite)"
              >
                <div class="citation-head">
                  <span class="citation-index">[{{ cite.index }}]</span>
                  <span>{{ cite.doc_title || cite.filename }}</span>
                  <el-tag v-if="cite.version_label" size="small" type="info">{{ cite.version_label }}</el-tag>
                  <span v-if="cite.page" class="citation-page">第 {{ cite.page }} 页</span>
                </div>
                <div class="citation-meta">
                  <span v-if="cite.article_no">{{ cite.article_no }}</span>
                  <span v-if="cite.effective_date">生效 {{ cite.effective_date }}</span>
                  <span v-if="cite.department">{{ cite.department }}</span>
                </div>
                <div class="citation-text">{{ cite.text || cite.snippet }}</div>
              </div>
            </div>
            <div v-if="message.role === 'assistant' && !message.streaming" class="answer-disclaimer">
              {{ message.disclaimer || disclaimerText }}
            </div>
            <div v-if="message.role === 'assistant' && message.strategy" class="message-strategy">
              策略：{{ message.strategy }}
            </div>
            <div v-if="message.role === 'assistant' && !message.streaming" class="message-actions">
              <el-button
                v-if="message.citations && message.citations.length"
                link
                size="small"
                @click="toggleCitations(message)"
              >
                {{ message.showCitations ? '收起引用溯源' : `引用溯源 (${message.citations.length})` }}
              </el-button>
              <el-button v-if="message.id" link size="small" @click="submitFeedback(message)">反馈</el-button>
              <el-button
                v-if="message.id && message.transfer_required"
                link
                type="warning"
                size="small"
                @click="transferToHuman(message)"
              >
                转人工
              </el-button>
              <el-button v-if="message.id" link size="small" @click="showRetrieval(message)">检索详情</el-button>
              <el-button
                v-if="message.trace && message.trace.length"
                link
                size="small"
                @click="openTrace(message)"
              >
                决策轨迹
              </el-button>
            </div>
          </div>
        </div>
        <el-button
          v-if="showBackToBottom"
          class="back-to-bottom"
          circle
          @click="scrollToBottom(true)"
        >
          回到底部
        </el-button>
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
            <el-button v-if="sending" type="danger" plain @click="stopGeneration">停止生成</el-button>
            <el-button type="primary" :loading="sending" @click="send">发送</el-button>
          </div>
        </div>
      </div>
    </section>

    <aside v-if="detailVisible" class="detail-panel">
      <div class="detail-head">
        <span>检索详情</span>
        <el-button link @click="detailVisible = false">关闭</el-button>
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
            <el-descriptions-item label="问答模式">
              {{ detail.mode === 'deep' ? '深度问答' : '快速问答' }}
            </el-descriptions-item>
            <el-descriptions-item label="缓存命中">{{ detail.cache?.hit ? '是' : '否' }}</el-descriptions-item>
            <el-descriptions-item v-if="detail.cache?.hit" label="缓存匹配">
              {{
                detail.cache?.match_type === 'semantic'
                  ? `语义相似 ${(detail.cache.similarity * 100).toFixed(1)}%`
                  : '精确命中'
              }}
            </el-descriptions-item>
            <el-descriptions-item label="耗时">{{ detail.duration_ms }} 毫秒</el-descriptions-item>
            <el-descriptions-item label="角色">{{ detail.role || '-' }}</el-descriptions-item>
            <el-descriptions-item label="可见范围">{{ detail.visibility_scope || '-' }}</el-descriptions-item>
            <el-descriptions-item label="命中版本">
              {{ (detail.versions || []).join('、') || '-' }}
            </el-descriptions-item>
          </el-descriptions>
          <div class="detail-sub">召回文档</div>
          <div v-for="doc in detail.documents" :key="doc.index" class="citation-card" style="margin-bottom: 8px">
            [{{ doc.index }}] {{ doc.filename }}<span v-if="doc.article_no"> {{ doc.article_no }}</span
            ><span v-if="doc.version_label">（{{ doc.version_label }}）</span
            ><span v-if="doc.page"> 第 {{ doc.page }} 页</span>
            <div style="margin-top: 4px">{{ doc.preview }}</div>
          </div>
          <div v-if="!detail.documents || detail.documents.length === 0" class="muted">本次未召回文档</div>
        </template>
        <div v-else class="muted detail-empty">
          点击回答下方的「检索详情」查看该次回答的召回过程
        </div>
      </div>
    </aside>

    <el-drawer v-model="citationDrawer" title="引用详情" direction="btt" size="70%">
      <div v-if="citationDetail" class="citation-detail">
        <div class="citation-head">
          <span class="citation-index">[{{ citationDetail.index }}]</span>
          <span>{{ citationDetail.doc_title || citationDetail.filename }}</span>
          <el-tag v-if="citationDetail.version_label" size="small" type="info">
            {{ citationDetail.version_label }}
          </el-tag>
        </div>
        <div class="citation-meta">
          <span v-if="citationDetail.article_no">{{ citationDetail.article_no }}</span>
          <span v-if="citationDetail.page">第 {{ citationDetail.page }} 页</span>
          <span v-if="citationDetail.effective_date">生效 {{ citationDetail.effective_date }}</span>
          <span v-if="citationDetail.department">{{ citationDetail.department }}</span>
        </div>
        <div class="citation-text">{{ citationDetail.text || citationDetail.snippet }}</div>
        <el-button type="primary" style="margin-top: 16px" @click="openPreview(citationDetail)">
          查看原文
        </el-button>
      </div>
    </el-drawer>

    <el-drawer v-model="traceDrawer" title="决策轨迹" direction="rtl" size="420px">
      <DecisionTrace v-if="traceMessage" :trace="traceMessage.trace" :rounds="traceMessage.rounds" />
    </el-drawer>

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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import {
  createConversation,
  deleteConversation,
  getChatCapabilities,
  getRetrievalDetail,
  listConversations,
  listKnowledgeBases,
  listMessages,
  listStrategies
} from '../api/chat'
import AnswerContent from '../components/AnswerContent.vue'
import DecisionTrace from '../components/DecisionTrace.vue'
import FeedbackDialog from '../components/FeedbackDialog.vue'
import { previewDocumentBlob, submitTransfer } from '../api/feedback'

const adminMode = import.meta.env.VITE_APP_MODE === 'admin'
const appKey = adminMode ? 'admin' : 'user'
const disclaimerText = '本回答由系统根据知识库自动生成，仅供参考，具体以正式文件为准。'
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
const stickToBottom = ref(true)
const showBackToBottom = ref(false)
const mobilePanelOpen = ref(false)
const isMobile = ref(false)
const citationDrawer = ref(false)
const citationDetail = ref(null)
const abortController = ref(null)
const deepMode = ref(false)
const deepAvailable = ref(false)
const traceDrawer = ref(false)
const traceMessage = ref(null)

const currentStrategy = computed(() => strategies.value.find((item) => item.id === strategyId.value))

function toggleCitations(message) {
  message.showCitations = !message.showCitations
}

function focusCitation(messageIndex, indices) {
  const message = messages.value[messageIndex]
  if (message) {
    message.showCitations = true
  }
  nextTick(() => {
    const target = document.getElementById(`cite-${messageIndex}-${indices[0]}`)
    if (!target) {
      return
    }
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    target.classList.add('citation-active')
    window.setTimeout(() => target.classList.remove('citation-active'), 1600)
  })
}

function onMessageScroll() {
  const element = messageListRef.value
  if (!element) {
    return
  }
  stickToBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
  showBackToBottom.value = !stickToBottom.value
}

function scrollToBottom(force = false) {
  nextTick(() => {
    const element = messageListRef.value
    if (!element || (!force && !stickToBottom.value)) {
      return
    }
    element.scrollTop = element.scrollHeight
    stickToBottom.value = true
    showBackToBottom.value = false
  })
}

function handleCitationClick(cite) {
  if (isMobile.value) {
    citationDetail.value = cite
    citationDrawer.value = true
    return
  }
  openPreview(cite)
}

function stopGeneration() {
  if (abortController.value) {
    abortController.value.abort()
  }
}

function updateViewport() {
  isMobile.value = window.innerWidth < 768
  if (!isMobile.value) {
    mobilePanelOpen.value = false
  }
}

async function refreshCapabilities() {
  if (!kbId.value) {
    deepAvailable.value = false
    deepMode.value = false
    return
  }
  try {
    const data = await getChatCapabilities(kbId.value)
    deepAvailable.value = Boolean(data.deep_enabled)
    if (!deepAvailable.value) {
      deepMode.value = false
    }
  } catch {
    deepAvailable.value = false
    deepMode.value = false
  }
}

watch(kbId, () => {
  deepMode.value = false
  refreshCapabilities()
})

function openTrace(message) {
  traceMessage.value = message
  traceDrawer.value = true
}

function agentStatusText(step) {
  if (step.action === 'retrieve') {
    return `第 ${step.round} 轮检索：${step.query || ''}`
  }
  if (step.action === 'answer') {
    return '正在整理答案'
  }
  if (step.action === 'clarify') {
    return '需要补充信息'
  }
  return '正在回退快速问答'
}

async function loadBaseData() {
  const [kbs, strategyList] = await Promise.all([
    listKnowledgeBases(adminMode ? 'admin' : 'user'),
    listStrategies()
  ])
  kbList.value = kbs
  strategies.value = strategyList
  if (kbs.length > 0 && !kbId.value) {
    kbId.value = kbs[0].id
  }
  const preferred = strategyList.find((item) => item.is_default) || strategyList[0]
  if (preferred && !strategyId.value) {
    strategyId.value = preferred.id
  }
  await refreshCapabilities()
}

async function loadConversations() {
  conversations.value = await listConversations(appKey)
}

async function openConversation(id) {
  activeId.value = id
  mobilePanelOpen.value = false
  detail.value = null
  detailVisible.value = false
  const data = await listMessages(id)
  messages.value = data.messages.map((item) => ({
    id: item.id,
    role: item.role,
    content: item.content,
    citations: item.citations || [],
    showCitations: false,
    strategy: item.role === 'assistant' ? strategies.value.find((preset) => preset.id === item.strategy_id)?.name : ''
  }))
  if (data.conversation.kb_id) {
    kbId.value = data.conversation.kb_id
  }
  await refreshCapabilities()
  stickToBottom.value = true
  scrollToBottom(true)
}

function newConversation() {
  activeId.value = null
  messages.value = []
  input.value = ''
  detail.value = null
  detailVisible.value = false
  mobilePanelOpen.value = false
}

function onKbChange() {
  activeId.value = null
  messages.value = []
  detail.value = null
  detailVisible.value = false
  deepMode.value = false
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
  const assistant = {
    role: 'assistant',
    content: '',
    citations: [],
    showCitations: false,
    streaming: true,
    id: null,
    strategy: '',
    disclaimer: '',
    transfer_required: false,
    mode: deepMode.value ? 'deep' : 'fast',
    cached: false,
    trace: [],
    rounds: 0,
    agentStatus: deepMode.value ? '正在分析问题' : ''
  }
  messages.value.push(assistant)
  stickToBottom.value = true
  scrollToBottom(true)

  const token = sessionStorage.getItem('token')
  abortController.value = new AbortController()
  let response
  try {
    response = await fetch(`${apiBase}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      signal: abortController.value.signal,
      body: JSON.stringify({
        question,
        kb_id: kbId.value,
        conversation_id: activeId.value,
        strategy_id: strategyId.value,
        mode: deepMode.value ? 'deep' : 'fast',
        app_key: appKey
      })
    })
  } catch (error) {
    assistant.streaming = false
    assistant.content = error?.name === 'AbortError' ? '已停止生成' : '无法连接后端服务，请稍后重试'
    sending.value = false
    abortController.value = null
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
  } catch (error) {
    if (error?.name === 'AbortError') {
      assistant.content += assistant.content ? '\n已停止生成' : '已停止生成'
    } else {
      assistant.content += '\n连接中断，请重试'
    }
  } finally {
    assistant.streaming = false
    sending.value = false
    abortController.value = null
    await loadConversations()
    scrollToBottom()
  }
}

function handleEvent(payload, assistant) {
  if (payload.event === 'meta') {
    activeId.value = payload.conversation_id
    assistant.strategy = payload.strategy
    assistant.mode = payload.mode || assistant.mode
    assistant.transfer_required = Boolean(payload.transfer_required)
  } else if (payload.event === 'delta') {
    assistant.content += payload.content
  } else if (payload.event === 'citations') {
    assistant.citations = payload.citations || []
  } else if (payload.event === 'agent_step') {
    const step = payload.step || {}
    assistant.trace = [...(assistant.trace || []), step]
    assistant.rounds = step.round || assistant.rounds
    assistant.agentStatus = agentStatusText(step)
  } else if (payload.event === 'done') {
    assistant.id = payload.message_id
    assistant.streaming = false
    assistant.disclaimer = payload.disclaimer || ''
    assistant.transfer_required = Boolean(payload.transfer_required)
    assistant.cached = Boolean(payload.cached)
    assistant.mode = payload.mode || assistant.mode
    assistant.agentStatus = ''
    if (payload.agent_trace) {
      assistant.trace = payload.agent_trace
    }
    if (payload.agent_rounds) {
      assistant.rounds = payload.agent_rounds
    }
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

async function openPreview(cite) {
  if (!cite.document_id) {
    return
  }
  try {
    const blob = await previewDocumentBlob(cite.document_id)
    const url = URL.createObjectURL(blob)
    window.open(url, '_blank')
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch {
    ElMessage.error('原文预览失败，可能无权限或文件不存在')
  }
}

async function transferToHuman(message) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请补充需要人工处理的问题说明', '转人工', {
      inputPlaceholder: '例如：该问题涉及个人具体情况，需要教务处确认',
      inputType: 'textarea',
      confirmButtonText: '提交转办',
      cancelButtonText: '取消'
    })
    reason = result.value || ''
  } catch {
    return
  }
  try {
    const created = await submitTransfer({
      assistant_message_id: message.id,
      reason: reason || '用户申请转人工',
      conversation_id: activeId.value
    })
    ElMessage.success(`已转办，工单号 ${created.ticket_no}`)
    message.transfer_required = false
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '转办失败，请稍后重试')
  }
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
  updateViewport()
  window.addEventListener('resize', updateViewport)
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

onUnmounted(() => {
  window.removeEventListener('resize', updateViewport)
  if (abortController.value) {
    abortController.value.abort()
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

.answer-disclaimer {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e4e7ed;
  font-size: 12px;
  color: #909399;
}

.citation-meta {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  font-size: 12px;
  color: #606266;
}

.citation-clickable {
  cursor: pointer;
}

.citation-clickable:hover {
  border-color: #1f6fd0;
}
</style>
