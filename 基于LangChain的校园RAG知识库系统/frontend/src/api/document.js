import request from './request'

export function listAdminDocuments(params) {
  return request.get('/admin/documents', { params })
}

export function getAdminDocument(id) {
  return request.get(`/admin/documents/${id}`)
}

export function updateAdminDocument(id, payload) {
  return request.patch(`/admin/documents/${id}`, payload)
}

export function createAdminDocument(form) {
  return request.post('/admin/documents', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000
  })
}

export function createDocumentVersion(id, form) {
  return request.post(`/admin/documents/${id}/versions`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000
  })
}

export function parseDocumentVersion(id) {
  return request.post(`/admin/versions/${id}/parse`, null, { timeout: 600000 })
}

export function checkVersionConsistency(id) {
  return request.get(`/admin/versions/${id}/consistency`)
}

export function publishDocumentVersion(id) {
  return request.post(`/admin/versions/${id}/publish`, null, { timeout: 120000 })
}

export function repealDocumentVersion(id, reason) {
  return request.post(`/admin/versions/${id}/repeal`, { reason }, { timeout: 120000 })
}

export function rollbackDocumentVersion(id) {
  return request.post(`/admin/versions/${id}/rollback`, null, { timeout: 120000 })
}

export function archiveAdminDocument(id, reason) {
  return request.post(`/admin/documents/${id}/archive`, { reason }, { timeout: 120000 })
}

export function deleteAdminDocument(id) {
  return request.delete(`/admin/documents/${id}`)
}

export function previewDocumentBlob(id, versionId) {
  return request.get(`/admin/documents/${id}/preview`, {
    params: versionId ? { version_id: versionId } : {},
    responseType: 'blob',
    timeout: 120000
  })
}
