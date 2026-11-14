import request from './request'

export function adminKbList() {
  return request.get('/kb')
}

export function createKb(payload) {
  return request.post('/kb', payload)
}

export function updateKb(id, payload) {
  return request.patch(`/kb/${id}`, payload)
}

export function deleteKb(id) {
  return request.delete(`/kb/${id}`)
}

export function listDocuments(kbId) {
  return request.get(`/kb/${kbId}/documents`)
}

export function uploadDocument(kbId, file) {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/kb/${kbId}/documents`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000
  })
}

export function ingestDocument(kbId, documentId) {
  return request.post(`/kb/${kbId}/documents/${documentId}/ingest`, null, { timeout: 600000 })
}

export function rebuildKb(kbId) {
  return request.post(`/kb/${kbId}/rebuild`, null, { timeout: 600000 })
}

export function deleteDocument(kbId, documentId) {
  return request.delete(`/kb/${kbId}/documents/${documentId}`)
}

export function adminStrategies() {
  return request.get('/strategies')
}

export function createStrategy(payload) {
  return request.post('/admin/strategies', payload)
}

export function updateStrategy(id, payload) {
  return request.patch(`/admin/strategies/${id}`, payload)
}

export function deleteStrategy(id) {
  return request.delete(`/admin/strategies/${id}`)
}

export function getModelConfig() {
  return request.get('/admin/model-config')
}

export function saveModelConfig(payload) {
  return request.put('/admin/model-config', payload)
}

export function testModelConfig(payload) {
  return request.post('/admin/model-config/test', payload, { timeout: 600000 })
}

export function listOllamaModels(baseUrl) {
  return request.get('/admin/model-config/ollama-models', {
    params: { base_url: baseUrl },
    timeout: 30000
  })
}
