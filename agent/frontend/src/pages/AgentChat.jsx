import { useEffect, useRef, useState } from 'react'
import { useKbStore } from '../stores/kbStore'
import { useChatStore, nextMessageId } from '../stores/chatStore'
import { useAgentStore } from '../stores/agentStore'
import { agentChatStream } from '../api/agent'
import { message } from '../utils/toast'
import ChatMessage from '../components/ChatMessage'
import CitationCard from '../components/CitationCard'
import DecisionTrace from '../components/DecisionTrace'

const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
const MAX_IMAGE_BYTES = 8 * 1024 * 1024

export default function AgentChat() {
  const kb = useKbStore()
  const cs = useChatStore()
  const as = useAgentStore()

  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [imageDataUrl, setImageDataUrl] = useState('')
  const scrollRef = useRef(null)
  const traceScrollRef = useRef(null)
  const abortRef = useRef(null)
  const fileInputRef = useRef(null)

  const activeKbs = kb.kbs.filter((k) => k.is_active)
  const currentKbId = kb.currentKbId

  useEffect(() => {
    ;(async () => {
      await kb.fetchKbs()
      const cid = useKbStore.getState().currentKbId
      if (cid) await useChatStore.getState().fetchConversations(cid)
    })()
    return () => abortRef.current?.abort()
  }, [])

  // 切换消息/会话时，轨迹面板回到顶部，保证从第 1 轮开始阅读
  useEffect(() => {
    const el = traceScrollRef.current
    if (el) el.scrollTop = 0
  }, [as.selectedMessageId, cs.currentConversationId])

  // 把提问的顶部对齐到消息区顶部；用于新回答开始生成时「从头看」。
  // 回答刚出现时内容高度可能不足，滚动会被最大值截断，因此保留锚点，
  // 随正文增长反复校正，直到真正对齐后解除。
  const anchorPendingRef = useRef(null)
  useEffect(() => {
    const id = anchorPendingRef.current
    const box = scrollRef.current
    if (!id || !box) return
    const target = box.querySelector(`[data-msg-id="${id}"]`)
    if (!target) return
    const delta = target.getBoundingClientRect().top - box.getBoundingClientRect().top
    box.scrollTop += delta - 12
    const remaining = target.getBoundingClientRect().top - box.getBoundingClientRect().top
    if (Math.abs(remaining - 12) <= 2) anchorPendingRef.current = null
  }, [cs.messages])

  const selectedMsg = (() => {
    const list = cs.messages
    if (as.selectedMessageId) {
      const hit = list.find((m) => m.id === as.selectedMessageId && m.role === 'assistant')
      if (hit) return hit
    }
    for (let i = list.length - 1; i >= 0; i -= 1) {
      if (list[i].role === 'assistant') return list[i]
    }
    return null
  })()

  function selectMsg(m) {
    if (m.role === 'assistant') as.setSelectedMessageId(m.id)
  }

  async function onKbChange(e) {
    const id = e.target.value ? Number(e.target.value) : null
    kb.setCurrentKb(id)
    cs.reset()
    as.reset()
    if (id) await cs.fetchConversations(id)
  }

  async function newConversation() {
    cs.newConversation()
    as.reset()
  }

  async function selectConversation(id) {
    if (id === cs.currentConversationId) return
    try {
      await cs.selectConversation(id)
      as.reset()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  async function removeConversation(id) {
    if (!window.confirm('确定删除该会话及其全部消息吗？')) return
    try {
      await cs.removeConversation(id)
      as.reset()
      message.success('已删除会话')
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  async function clearAll() {
    if (!cs.conversations.length) return
    if (!window.confirm('确定清空当前知识库下的所有对话吗？此操作不可恢复。')) return
    try {
      await cs.clearAll(currentKbId)
      as.reset()
      message.success('已清空所有对话')
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  async function send() {
    const q = input.trim()
    const image = imageDataUrl
    if ((!q && !image) || !currentKbId || sending) return
    setSending(true)
    setInput('')
    setImageDataUrl('')

    const conversationId = cs.currentConversationId
    const userMsg = { id: nextMessageId(), role: 'user', content: q, image }
    const asstId = nextMessageId()
    cs.appendMessage(userMsg)
    cs.appendMessage({
      id: asstId,
      role: 'assistant',
      content: '',
      citations: [],
      retrieved_docs: [],
      latency: {},
      agent_trace: [],
      streaming: true,
      is_clarification: false,
    })
    as.setSelectedMessageId(asstId)
    // 以「提问」为锚点：问题与回答开头保持可见，之后正文增长不再自动滚动
    anchorPendingRef.current = userMsg.id

    const traceAccum = []
    let answerAccum = ''

    try {
      const result = await agentChatStream({
        kbId: currentKbId,
        conversationId,
        question: q,
        image,
        signal: abortRef.current?.signal,
        onStep: (step) => {
          traceAccum.push(step)
          cs.updateMessage(asstId, { agent_trace: traceAccum.slice() })
        },
        onDelta: (delta) => {
          answerAccum += delta
          cs.updateMessage(asstId, { content: answerAccum })
        },
        onMeta: (meta) => {
          if (!conversationId && meta?.conversation_id) {
            cs.setCurrentConversationId(meta.conversation_id)
          }
        },
      })

      cs.updateMessage(asstId, {
        content: result.answer || answerAccum,
        citations: result.citations || [],
        retrieved_docs: result.retrieved_docs || [],
        latency: result.latency || {},
        agent_trace: result.agent_trace?.length ? result.agent_trace : traceAccum,
        streaming: false,
        is_clarification: result.is_clarification,
      })

      if (result.conversation_id && !conversationId) {
        cs.setCurrentConversationId(result.conversation_id)
      }
      await cs.fetchConversations(currentKbId)
    } catch (e) {
      cs.updateMessage(asstId, {
        content: `生成失败：${e?.message || '请稍后重试'}`,
        streaming: false,
      })
    } finally {
      setSending(false)
    }
  }

  function onKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  function onPickImage(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
      message.error('仅支持 JPEG / PNG / GIF / WebP 格式的图片')
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      message.error('图片不能超过 8 MB')
      return
    }
    const reader = new FileReader()
    reader.onload = () => setImageDataUrl(String(reader.result || ''))
    reader.onerror = () => message.error('图片读取失败，请重试')
    reader.readAsDataURL(file)
  }

  return (
    <div className="chat-page">
      <div className="chat-toolbar">
        <div className="toolbar-left">
          <span className="toolbar-label">知识库</span>
          <select className="kb-select" value={currentKbId ?? ''} onChange={onKbChange}>
            {activeKbs.length === 0 && <option value="">无可用知识库</option>}
            {activeKbs.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name}
              </option>
            ))}
          </select>
        </div>
        <div className="toolbar-right">
          <span className="toolbar-hint">检索策略由智能体逐轮决策</span>
          <button className="btn btn--sm" onClick={() => as.toggleTrace()}>
            {as.traceVisible ? '收起轨迹' : '决策轨迹'}
          </button>
        </div>
      </div>

      <div className={`chat-body${as.traceVisible ? ' chat-body--with-trace' : ''}`}>
        <aside className="conv-panel">
          <button className="btn btn--primary conv-new" onClick={newConversation}>
            新建会话
          </button>
          <ul className="conv-list scroll-area">
            {cs.conversations.map((c) => (
              <li
                key={c.id}
                className={`conv-item${c.id === cs.currentConversationId ? ' is-active' : ''}`}
                onClick={() => selectConversation(c.id)}
              >
                <span className="conv-title">{c.title || '新会话'}</span>
                <button
                  className="icon-btn conv-del"
                  title="删除会话"
                  onClick={(e) => {
                    e.stopPropagation()
                    removeConversation(c.id)
                  }}
                >
                  ×
                </button>
              </li>
            ))}
            {cs.conversations.length === 0 && <li className="conv-empty">暂无历史会话</li>}
          </ul>
          <button className="btn btn--danger conv-clear" disabled={!cs.conversations.length} onClick={clearAll}>
            清空所有对话
          </button>
        </aside>

        <section className="conv-main">
          <div ref={scrollRef} className="msg-scroll scroll-area">
            {cs.messages.length === 0 && (
              <div className="empty-hint">
                <div className="empty-hint__title serif">开始你的提问</div>
                <div className="empty-hint__sub">
                  选择知识库后输入问题，智能体会自动判断闲聊、澄清还是检索，
                  并在右侧实时展示逐轮决策轨迹。
                </div>
              </div>
            )}

            {cs.messages.map((msg) => (
              <div
                key={msg.id}
                className="msg-block"
                data-msg-id={msg.id}
                onClick={() => selectMsg(msg)}
              >
                <ChatMessage message={msg} onCite={() => {}} />
                {msg.role === 'assistant' && !msg.streaming && (
                  <div className="assistant-extras">
                    {msg.is_clarification && (
                      <div className="latency-line">（智能体正在等待你补充信息）</div>
                    )}
                    {msg.citations?.length ? (
                      <div className="citations">
                        <div className="section-label">引用来源（{msg.citations.length} 条）</div>
                        {msg.citations.map((c, i) => (
                          <CitationCard
                            key={i}
                            citation={c}
                            index={c.index ?? i + 1}
                            id={`cite-${msg.id}-${c.index ?? i + 1}`}
                          />
                        ))}
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="composer">
            {imageDataUrl && (
              <div className="image-preview">
                <img src={imageDataUrl} alt="待发送的图片" />
                <button
                  type="button"
                  className="image-remove"
                  title="移除图片"
                  onClick={() => setImageDataUrl('')}
                >
                  ✕
                </button>
              </div>
            )}
            <textarea
              rows={3}
              value={input}
              disabled={!currentKbId || sending}
              placeholder={
                !currentKbId
                  ? '请先选择知识库'
                  : imageDataUrl
                    ? '可补充你的问题，留空则让系统描述图片内容（Enter 发送）'
                    : '请输入你的问题…（Enter 发送，Shift+Enter 换行）'
              }
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeydown}
            />
            <div className="composer-actions">
              <button
                type="button"
                className="btn btn--icon"
                disabled={!currentKbId || sending}
                onClick={() => fileInputRef.current?.click()}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7">
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                  <circle cx="8.5" cy="9.5" r="1.5" />
                  <path d="m21 15-5-5L5 20" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span>图片</span>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/gif,image/webp"
                hidden
                onChange={onPickImage}
              />
              <button
                className="btn btn--primary"
                disabled={!currentKbId || (!input.trim() && !imageDataUrl) || sending}
                onClick={send}
              >
                {sending ? '思考中…' : '发送'}
              </button>
            </div>
          </div>
        </section>

        {as.traceVisible && (
          <aside className="detail-panel">
            <div className="detail-panel__head">
              <span className="detail-panel__title">决策轨迹</span>
              {selectedMsg?.rounds ? <span className="detail-panel__tag">{selectedMsg.rounds} 轮</span> : null}
            </div>
            <div ref={traceScrollRef} className="detail-panel__body scroll-area">
              {selectedMsg ? (
                <DecisionTrace trace={selectedMsg.agent_trace || []} />
              ) : (
                <div className="detail-empty">
                  提问后，这里会逐轮展示智能体的决策轨迹：意图判断、改写 query、所选策略、
                  命中文档块与最终作答。
                </div>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}