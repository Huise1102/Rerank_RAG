import request from './request'

export function listConversations(kbId) {
  return request.get('/conversations', { params: { kb_id: kbId } })
}

export function listMessages(conversationId) {
  return request.get(`/conversations/${conversationId}/messages`)
}

export function deleteConversation(id) {
  return request.delete(`/conversations/${id}`)
}

export function clearConversations(kbId) {
  return request.delete('/conversations', { params: { kb_id: kbId } })
}

// 通用 SSE 解析器：逐行读 data:，回调 onEvent(type, payload)
export async function parseSSE(response, onEvent) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const consume = (raw) => {
    const payload = raw.trim()
    if (!payload || payload === '[DONE]') return
    let obj = null
    try {
      obj = JSON.parse(payload)
    } catch (e) {
      obj = null
    }
    if (obj && typeof obj === 'object' && obj.type) {
      onEvent?.(obj.type, obj)
    } else if (obj === null) {
      onEvent?.('raw', payload)
    }
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      const l = line.replace(/\r$/, '')
      if (!l.startsWith('data:')) continue
      consume(l.slice(5))
    }
  }
  if (buffer.startsWith('data:')) consume(buffer.slice(5))
}