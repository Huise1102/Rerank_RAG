import request from './request'

// 会话管理
export function listConversations(kbId) {
  return request.get('/conversations', { params: { kb_id: kbId } })
}

export function listMessages(conversationId) {
  return request.get(`/conversations/${conversationId}/messages`)
}

export function deleteConversation(conversationId) {
  return request.delete(`/conversations/${conversationId}`)
}

// 非流式问答
export function chat(payload) {
  return request.post('/chat', payload)
}

/**
 * 流式问答：POST /chat/stream 是 POST，EventSource 不支持 POST，
 * 因此用 fetch + ReadableStream 手动解析 SSE（按 data: 行增量渲染）。
 *
 * @param {Object}   opts
 * @param {number}   opts.kbId
 * @param {number?}  opts.conversationId
 * @param {string}   opts.question
 * @param {string?}  [opts.image]    图片 data URL（data:image/png;base64,...）
 * @param {Function} [opts.onDelta]  收到增量文本时回调 (delta: string)
 * @param {Function} [opts.onMeta]   收到 meta 事件时回调 (meta: object)
 * @param {AbortSignal} [opts.signal]
 * @returns {Promise<Object>} 结束时返回完整结果，用于引用解析与展示
 */
export async function chatStream({
  kbId,
  conversationId,
  question,
  image,
  onDelta,
  onMeta,
  signal,
}) {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({
      kb_id: kbId,
      conversation_id: conversationId || null,
      question: question || '',
      image: image || null,
    }),
    signal,
  })

  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`
    try {
      const data = await response.json()
      if (data && data.detail) {
        message = Array.isArray(data.detail) ? JSON.stringify(data.detail) : String(data.detail)
      }
    } catch (e) {
      /* 忽略解析失败 */
    }
    throw new Error(message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let fullText = ''
  let final = null
  let meta = null

  const consume = (rawData) => {
    const payload = rawData.trim()
    if (!payload || payload === '[DONE]') return
    let obj = null
    try {
      obj = JSON.parse(payload)
    } catch (e) {
      obj = null
    }
    if (obj && typeof obj === 'object') {
      const type = obj.type
      if (type === 'meta') {
        meta = obj
        onMeta?.(obj)
        return
      }
      if (type === 'delta') {
        const delta = obj.delta ?? obj.content ?? ''
        fullText += delta
        onDelta?.(delta)
        return
      }
      if (type === 'done') {
        final = obj.response || obj
        return
      }
      if (type === 'error') {
        throw new Error(obj.error || obj.message || '生成失败')
      }
      if (typeof obj.answer === 'string') {
        final = obj
        return
      }
      if (typeof obj.delta === 'string') {
        fullText += obj.delta
        onDelta?.(obj.delta)
        return
      }
      return
    }
    // 纯文本分片
    fullText += payload
    onDelta?.(payload)
  }

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const rawLine of lines) {
      const line = rawLine.replace(/\r$/, '')
      if (!line.startsWith('data:')) continue
      consume(line.slice(5))
    }
  }
  if (buffer.startsWith('data:')) consume(buffer.slice(5))

  const result = final || {}
  return {
    answer: result.answer ?? fullText,
    citations: result.citations ?? (meta && meta.citations) ?? [],
    retrieved_docs: result.retrieved_docs ?? (meta && meta.retrieved_docs) ?? [],
    latency: result.latency ?? (meta && meta.latency) ?? {},
    strategy: result.strategy ?? (meta && meta.strategy) ?? '',
    stats: result.stats ?? (meta && meta.stats) ?? null,
    conversation_id: result.conversation_id ?? (meta && meta.conversation_id) ?? conversationId ?? null,
    wasFinal: !!final,
  }
}
