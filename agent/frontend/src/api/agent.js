import request from './request'
import { parseSSE } from './chat'

export function agentChat(payload) {
  return request.post('/agent/chat', payload)
}

/**
 * Agent 流式问答。
 * @param {object}  opts
 * @param {number}  opts.kbId
 * @param {number?} opts.conversationId
 * @param {string}  opts.question
 * @param {string?} opts.image  图片 data URL（可只发图不发文）
 * @param {(step: object) => void}  opts.onStep  agent_step 事件（每轮决策/工具结果）
 * @param {(meta: object) => void}  opts.onMeta  meta 事件（conversation_id 等）
 * @param {(delta: string) => void} opts.onDelta delta 事件（最终答案正文）
 * @param {AbortSignal?} opts.signal
 */
export async function agentChatStream({ kbId, conversationId, question, image, onStep, onMeta, onDelta, signal }) {
  const response = await fetch('/api/v1/agent/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      kb_id: kbId,
      conversation_id: conversationId || null,
      question: question || '',
      image: image || null,
    }),
    signal,
  })

  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const data = await response.json()
      if (data?.detail) detail = typeof data.detail === 'string' ? data.detail : detail
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail)
  }

  let meta = null
  let final = null
  let fullText = ''

  await parseSSE(response, (type, payload) => {
    if (type === 'meta') {
      meta = payload
      onMeta?.(payload)
      return
    }
    if (type === 'agent_step') {
      onStep?.(payload.step)
      return
    }
    if (type === 'delta') {
      fullText += payload.delta || ''
      onDelta?.(payload.delta || '')
      return
    }
    if (type === 'done') {
      final = payload.response || payload
      return
    }
    if (type === 'error') {
      throw new Error(payload.error || '生成失败')
    }
  })

  const result = final || {}
  return {
    answer: result.answer ?? fullText,
    citations: result.citations ?? [],
    retrieved_docs: result.retrieved_docs ?? [],
    latency: result.latency ?? {},
    agent_trace: result.agent_trace ?? [],
    rounds: result.rounds ?? 0,
    is_clarification: result.is_clarification ?? false,
    clarification_question: result.clarification_question ?? null,
    conversation_id: result.conversation_id ?? meta?.conversation_id ?? conversationId ?? null,
  }
}