import request from './request'

export function listKbs() {
  return request.get('/kb')
}

export function createKb(data) {
  return request.post('/kb', data)
}

export function updateKb(id, data) {
  return request.put(`/kb/${id}`, data)
}

export function deleteKb(id) {
  return request.delete(`/kb/${id}`)
}

// 更新知识库检索构建块开关（vector/bm25/fusion/rerank）
export function updateStrategies(id, switches) {
  return request.patch(`/kb/${id}/strategies`, switches)
}