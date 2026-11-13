import request from './request'

export function listConversations(appKey) {
  return request.get('/conversations', { params: { app_key: appKey } })
}

export function createConversation(appKey) {
  return request.post('/conversations', null, { params: { app_key: appKey } })
}

export function deleteConversation(id) {
  return request.delete(`/conversations/${id}`)
}

export function listMessages(id) {
  return request.get(`/conversations/${id}/messages`)
}

export function getRetrievalDetail(messageId) {
  return request.get(`/messages/${messageId}/retrieval-detail`)
}

export function listKnowledgeBases() {
  return request.get('/kb')
}

export function listStrategies() {
  return request.get('/strategies')
}

export function submitFeedback(payload) {
  return request.post('/feedback', payload)
}
