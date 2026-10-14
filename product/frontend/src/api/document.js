import request from './request'

// 文档管理（pdf / docx / txt / md）
export function uploadDocument(kbId, file) {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/kb/${kbId}/documents`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function listDocuments(kbId) {
  return request.get(`/kb/${kbId}/documents`)
}

export function getDocument(docId) {
  return request.get(`/documents/${docId}`)
}

// 原始文档直链：默认内联预览，download=true 时作为附件下载
export function documentFileUrl(docId, download = false) {
  return `/api/v1/documents/${docId}/file${download ? '?download=true' : ''}`
}

export function deleteDocument(docId) {
  return request.delete(`/documents/${docId}`)
}
