<template>
  <div class="chat-page">
    <div class="chat-toolbar">
      <div class="toolbar-left">
        <span class="toolbar-label">知识库</span>
        <el-select
          v-model="kbStore.currentKbId"
          placeholder="请选择知识库"
          style="width: 240px"
          @change="onKbChange"
        >
          <el-option v-for="kb in kbStore.kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>
      </div>
      <div class="toolbar-right">
        <span class="toolbar-label">当前策略</span>
        <StrategyBuilder :model-value="kbStore.currentStrategies" readonly />
      </div>
    </div>

    <div class="chat-body">
      <aside class="conv-panel">
        <el-button class="conv-new" @click="newConversation">
          <el-icon><Plus /></el-icon>
          <span>新建会话</span>
        </el-button>
        <ul class="conv-list scroll-area">
          <li
            v-for="c in chatStore.conversations"
            :key="c.id"
            class="conv-item"
            :class="{ 'is-active': c.id === chatStore.currentConversationId }"
            @click="selectConversation(c.id)"
          >
            <span class="conv-title">{{ c.title || '新会话' }}</span>
            <el-icon class="conv-del" @click.stop="removeConversation(c.id)"><Delete /></el-icon>
          </li>
          <li v-if="!chatStore.conversations.length" class="conv-empty">暂无历史会话</li>
        </ul>
      </aside>

      <section class="conv-main">
        <div ref="scrollEl" class="msg-scroll scroll-area">
          <div v-if="!chatStore.messages.length" class="empty-hint">
            <div class="empty-hint__title serif">开始你的提问</div>
            <div class="empty-hint__sub">
              选择知识库后输入问题，系统将返回带引用溯源的答案与检索详情。
            </div>
          </div>

          <div v-for="msg in chatStore.messages" :key="msg.id" class="msg-block">
            <ChatMessage :message="msg" @cite="(idx) => handleCitationClick(msg.id, idx)" />

            <div v-if="msg.role === 'assistant'" class="assistant-extras">
              <div v-if="latencyText(msg)" class="latency-line">{{ latencyText(msg) }}</div>

              <div v-if="citationsOf(msg).length" class="citations">
                <div class="section-label">引用来源（{{ citationsOf(msg).length }} 条）</div>
                <CitationCard
                  v-for="(c, i) in citationsOf(msg)"
                  :key="i"
                  :citation="c"
                  :index="citeIndex(c, i)"
                  :id="`cite-${msg.id}-${citeIndex(c, i)}`"
                />
              </div>

              <RetrievalDetail
                v-if="docsOf(msg).length"
                :docs="docsOf(msg)"
                :latency="msg.latency"
                :stats="msg.stats"
                :strategy="msg.strategy"
              />
            </div>
          </div>
        </div>

        <div class="composer">
          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            :disabled="!kbStore.currentKbId"
            placeholder="请输入你的问题…（Enter 发送，Shift+Enter 换行）"
            @keydown="onKeydown"
          />
          <div class="composer-actions">
            <span class="composer-hint">
              {{
                kbStore.currentKb
                  ? `当前知识库：${kbStore.currentKb.name} · 检索策略修改请前往「检索策略」页`
                  : '请先选择知识库'
              }}
            </span>
            <el-button
              type="primary"
              :loading="sending"
              :disabled="!kbStore.currentKbId || !input.trim()"
              @click="handleSend"
            >
              <el-icon v-if="!sending"><Promotion /></el-icon>
              <span>发送</span>
            </el-button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbStore } from '@/stores/kb'
import {
  useChatStore,
  nextMessageId,
  normalizeCitations,
  normalizeDocs,
} from '@/stores/chat'
import { chatStream } from '@/api/chat'
import ChatMessage from '@/components/ChatMessage.vue'
import CitationCard from '@/components/CitationCard.vue'
import RetrievalDetail from '@/components/RetrievalDetail.vue'
import StrategyBuilder from '@/components/StrategyBuilder.vue'

const kbStore = useKbStore()
const chatStore = useChatStore()

const input = ref('')
const sending = ref(false)
const scrollEl = ref(null)
let abortController = null

onMounted(async () => {
  await kbStore.fetchKbs()
  if (kbStore.currentKbId) {
    await chatStore.fetchConversations(kbStore.currentKbId)
  }
})

async function onKbChange() {
  chatStore.reset()
  if (kbStore.currentKbId) {
    await chatStore.fetchConversations(kbStore.currentKbId)
  }
}

function newConversation() {
  chatStore.newConversation()
}

async function selectConversation(id) {
  if (id === chatStore.currentConversationId) return
  try {
    await chatStore.selectConversation(id)
    await scrollToBottom()
  } catch (e) {
    /* 错误提示由拦截器统一处理 */
  }
}

async function removeConversation(id) {
  try {
    await ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch (e) {
    return
  }
  try {
    await chatStore.removeConversation(id)
    ElMessage.success('已删除会话')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function citationsOf(msg) {
  return msg.citations || []
}

function docsOf(msg) {
  return msg.retrieved_docs || []
}

function citeIndex(c, i) {
  return c.index ?? i + 1
}

function latencyText(msg) {
  const lat = msg.latency || {}
  const parts = []
  if (lat.retrieve_ms) parts.push(`检索 ${(lat.retrieve_ms / 1000).toFixed(2)}s`)
  if (lat.rerank_ms) parts.push(`精排 ${(lat.rerank_ms / 1000).toFixed(2)}s`)
  if (lat.generate_ms) parts.push(`生成 ${(lat.generate_ms / 1000).toFixed(2)}s`)
  if (lat.total_ms) parts.push(`总计 ${(lat.total_ms / 1000).toFixed(2)}s`)
  if (msg.strategy) parts.push(`策略 ${msg.strategy}`)
  return parts.join(' ｜ ')
}

function handleCitationClick(msgId, index) {
  const el = document.getElementById(`cite-${msgId}-${index}`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('cite-flash')
  setTimeout(() => el.classList.remove('cite-flash'), 1400)
}

async function scrollToBottom() {
  await nextTick()
  if (scrollEl.value) {
    scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  }
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    handleSend()
  }
}

async function handleSend() {
  const text = input.value.trim()
  if (!text || sending.value || !kbStore.currentKbId) return

  input.value = ''
  chatStore.messages.push({
    id: nextMessageId(),
    role: 'user',
    content: text,
  })

  const assistant = reactive({
    id: nextMessageId(),
    role: 'assistant',
    content: '',
    citations: [],
    retrieved_docs: [],
    latency: {},
    strategy: '',
    stats: null,
    streaming: true,
  })
  chatStore.messages.push(assistant)
  sending.value = true
  scrollToBottom()

  abortController = new AbortController()
  try {
    const result = await chatStream({
      kbId: kbStore.currentKbId,
      conversationId: chatStore.currentConversationId,
      question: text,
      signal: abortController.signal,
      onDelta: (delta) => {
        assistant.content += delta
        scrollToBottom()
      },
      onMeta: (meta) => {
        assistant.retrieved_docs = normalizeDocs(meta.retrieved_docs)
        if (meta.stats) assistant.stats = meta.stats
        if (meta.strategy) assistant.strategy = meta.strategy
      },
    })

    assistant.content = result.answer || assistant.content
    assistant.citations = normalizeCitations(result.citations)
    if (result.retrieved_docs?.length) {
      assistant.retrieved_docs = normalizeDocs(result.retrieved_docs)
    }
    if (result.latency && Object.keys(result.latency).length) {
      assistant.latency = result.latency
    }
    if (result.strategy) assistant.strategy = result.strategy

    if (result.conversation_id) {
      chatStore.currentConversationId = result.conversation_id
      await chatStore.fetchConversations(kbStore.currentKbId)
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      if (!assistant.content) assistant.content = `调用失败：${err.message}`
      ElMessage.error(`生成失败：${err.message}`)
    }
  } finally {
    assistant.streaming = false
    sending.value = false
    abortController = null
    scrollToBottom()
  }
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  max-width: 1180px;
  margin: 0 auto;
}

.chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  background-color: #ffffff;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 12px 18px;
  margin-bottom: 14px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.toolbar-label {
  font-size: 13px;
  color: var(--app-text-secondary);
  font-weight: 600;
}

.chat-body {
  flex: 1;
  display: flex;
  gap: 14px;
  min-height: 0;
}

.conv-panel {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 12px;
}

.conv-new {
  width: 100%;
  margin-bottom: 10px;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 0;
}

.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-text-regular);
  margin-bottom: 3px;
}

.conv-item:hover {
  background-color: var(--app-surface-sunken);
}

.conv-item.is-active {
  background-color: var(--app-accent-soft);
  color: var(--app-accent-dark);
  font-weight: 600;
}

.conv-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-del {
  color: var(--app-text-muted);
  opacity: 0;
  transition: opacity 0.15s;
}

.conv-item:hover .conv-del {
  opacity: 1;
}

.conv-del:hover {
  color: var(--app-danger);
}

.conv-empty {
  padding: 24px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--app-text-muted);
}

.conv-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background-color: #ffffff;
  border: 1px solid var(--app-border);
  border-radius: 6px;
}

.msg-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 18px 22px;
}

.empty-hint {
  text-align: center;
  padding: 80px 20px;
}

.empty-hint__title {
  font-size: 20px;
  font-weight: 700;
  color: #1f1f1f;
}

.empty-hint__sub {
  margin-top: 8px;
  font-size: 13px;
  color: var(--app-text-muted);
}

.msg-block {
  margin-bottom: 6px;
}

.assistant-extras {
  margin: 0 0 18px 16px;
  padding-left: 2px;
}

.latency-line {
  font-size: 12px;
  color: var(--app-text-muted);
  margin: 2px 0 8px;
}

.citations {
  margin-top: 6px;
}

.composer {
  border-top: 1px solid var(--app-border);
  padding: 12px 16px 14px;
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
}

.composer-hint {
  font-size: 12px;
  color: var(--app-text-muted);
}
</style>
