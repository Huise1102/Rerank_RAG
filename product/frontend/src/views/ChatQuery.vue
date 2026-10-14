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
          <el-option v-for="kb in kbStore.activeKbs" :key="kb.id" :label="kb.name" :value="kb.id" />
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

          <div
            v-for="msg in chatStore.messages"
            :key="msg.id"
            class="msg-block"
            :class="{ 'msg-block--selectable': msg.role === 'assistant' }"
            @click="selectDetail(msg)"
          >
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
            </div>
          </div>
        </div>

        <div class="composer">
          <div v-if="imageDataUrl" class="image-preview">
            <img :src="imageDataUrl" alt="待发送的图片" />
            <el-button class="image-remove" link @click="clearImage">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            :disabled="!kbStore.currentKbId"
            :placeholder="
              imageDataUrl
                ? '可补充你的问题，留空则让系统描述图片内容（Enter 发送）'
                : '请输入你的问题…（Enter 发送，Shift+Enter 换行）'
            "
            @keydown="onKeydown"
          />
          <div class="composer-actions">
            <el-button :disabled="!kbStore.currentKbId" @click="pickImage">
              <el-icon><Picture /></el-icon>
              <span>图片</span>
            </el-button>
            <input
              ref="fileInput"
              type="file"
              accept="image/jpeg,image/png,image/gif,image/webp"
              hidden
              @change="onPickImage"
            />
            <el-button
              type="primary"
              :loading="sending"
              :disabled="!kbStore.currentKbId || (!input.trim() && !imageDataUrl)"
              @click="handleSend"
            >
              <el-icon v-if="!sending"><Promotion /></el-icon>
              <span>发送</span>
            </el-button>
          </div>
        </div>
      </section>

      <!-- 右：检索详情（3/9），展示当前所选回答的召回与耗时 -->
      <aside class="detail-panel">
        <div class="detail-panel__head">
          <span class="detail-panel__title">检索详情</span>
          <span v-if="detailMsg && detailMsg.strategy" class="detail-panel__tag">
            {{ detailMsg.strategy }}
          </span>
        </div>
        <div class="detail-panel__body scroll-area">
          <template v-if="detailMsg && docsOf(detailMsg).length">
            <div class="detail-panel__stat">
              Top-{{ docsOf(detailMsg).length }} 文档块 · 两路召回得分
            </div>
            <RetrievalDetail
              :docs="docsOf(detailMsg)"
              :latency="detailMsg.latency"
              :stats="detailMsg.stats"
            />
          </template>
          <div v-else class="detail-empty">
            提问后，这里会展示该次回答召回的文档块、两路召回得分与耗时分解。<br />
            上方回答可点击切换，默认显示最近一次回答。
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
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
const fileInput = ref(null)
//: 待发送图片的 data URL（data:image/png;base64,...）
const imageDataUrl = ref('')
let abortController = null

//: 用户在对话区点选的那条回答，决定右侧「检索详情」栏展示哪一次检索
const selectedDetailId = ref(null)

// 优先展示点选的那条回答，否则回退到最近一条回答（新回答生成时自动跟随）
const detailMsg = computed(() => {
  const list = chatStore.messages
  if (selectedDetailId.value) {
    const hit = list.find((m) => m.id === selectedDetailId.value && m.role === 'assistant')
    if (hit) return hit
  }
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i].role === 'assistant') return list[i]
  }
  return null
})

function selectDetail(msg) {
  if (msg.role === 'assistant') selectedDetailId.value = msg.id
}

//: DeepSeek 多模态接口支持的图片格式
const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
const MAX_IMAGE_BYTES = 8 * 1024 * 1024

onMounted(async () => {
  await kbStore.fetchKbs()
  ensureActiveKb()
  if (kbStore.currentKbId) {
    await chatStore.fetchConversations(kbStore.currentKbId)
  }
})

// 只在「已启用」的知识库范围内选中，避免停用后仍可问答
function ensureActiveKb() {
  const active = kbStore.activeKbs
  if (!active.length) {
    kbStore.setCurrentKb(null)
    return
  }
  if (active.some((k) => k.id === kbStore.currentKbId)) return
  kbStore.setCurrentKb(active[0].id)
}

async function onKbChange() {
  chatStore.reset()
  selectedDetailId.value = null
  if (kbStore.currentKbId) {
    await chatStore.fetchConversations(kbStore.currentKbId)
  }
}

function newConversation() {
  chatStore.newConversation()
  selectedDetailId.value = null
}

async function selectConversation(id) {
  if (id === chatStore.currentConversationId) return
  try {
    await chatStore.selectConversation(id)
    selectedDetailId.value = null
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
  // 点引用时同步把右侧检索详情切到该条回答
  selectedDetailId.value = msgId
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

// ------------------------------- 图片问答 ---------------------------------- //

function pickImage() {
  fileInput.value?.click()
}

function onPickImage(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // 允许再次选择同一文件
  if (!file) return
  if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
    ElMessage.error('仅支持 JPEG / PNG / GIF / WebP 格式的图片')
    return
  }
  if (file.size > MAX_IMAGE_BYTES) {
    ElMessage.error('图片不能超过 8 MB')
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    imageDataUrl.value = String(reader.result || '')
  }
  reader.onerror = () => ElMessage.error('图片读取失败，请重试')
  reader.readAsDataURL(file)
}

function clearImage() {
  imageDataUrl.value = ''
}

async function handleSend() {
  const text = input.value.trim()
  const image = imageDataUrl.value
  if ((!text && !image) || sending.value || !kbStore.currentKbId) return

  input.value = ''
  imageDataUrl.value = ''
  // 新提问后详情栏跟随这一次检索，不再停留在之前点选的回答上
  selectedDetailId.value = null
  chatStore.messages.push({
    id: nextMessageId(),
    role: 'user',
    content: text,
    image,
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
      image,
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

/* 左：会话列表 —— 占 1/9 */
.conv-panel {
  flex: 1 1 0;
  min-width: 0;
  box-sizing: border-box;
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

/* 中：主对话区 —— 占 5/9 */
.conv-main {
  flex: 5 1 0;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border: 1px solid var(--app-border);
  border-radius: 6px;
}

/* 右：检索详情 —— 占 3/9 */
.detail-panel {
  flex: 3 1 0;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border: 1px solid var(--app-border);
  border-radius: 6px;
}

.detail-panel__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--app-border);
}

.detail-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-secondary);
}

.detail-panel__tag {
  padding: 1px 6px;
  font-size: 12px;
  border-radius: 3px;
  color: var(--app-accent-dark);
  background-color: var(--app-accent-soft);
}

.detail-panel__body {
  flex: 1;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 12px 14px;
}

.detail-panel__stat {
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--app-text-muted);
}

.detail-empty {
  padding: 40px 10px;
  text-align: center;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--app-text-muted);
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

/* 回答可点击切换右侧检索详情 */
.msg-block--selectable {
  cursor: pointer;
  border-radius: 6px;
}

.msg-block--selectable:hover {
  background-color: #faf7f0;
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
  justify-content: flex-end;
  gap: 10px;
  margin-top: 10px;
}

.image-preview {
  position: relative;
  display: inline-block;
  margin-bottom: 10px;
}

.image-preview img {
  display: block;
  max-height: 120px;
  max-width: 220px;
  border-radius: 4px;
  border: 1px solid var(--app-border);
}

.image-remove {
  position: absolute;
  top: -10px;
  right: -10px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background-color: #ffffff;
  border: 1px solid var(--app-border);
  color: var(--app-text-muted);
}

.image-remove:hover {
  color: var(--app-danger);
}
</style>
