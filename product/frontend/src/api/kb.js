import request from './request'

// 知识库管理
export function listKbs() {
  return request.get('/kb')
}

export function createKb(data) {
  return request.post('/kb', data)
}

export function getKb(kbId) {
  return request.get(`/kb/${kbId}`)
}

export function updateKb(kbId, data) {
  return request.put(`/kb/${kbId}`, data)
}

// 单独更新构建块开关（vector / bm25 / fusion / rerank）
export function updateStrategies(kbId, strategies) {
  return request.patch(`/kb/${kbId}/strategies`, strategies)
}

export function deleteKb(kbId) {
  return request.delete(`/kb/${kbId}`)
}

// 检索调试：只检索不生成
export function retrieve(kbId, data) {
  return request.post(`/kb/${kbId}/retrieve`, data)
}
