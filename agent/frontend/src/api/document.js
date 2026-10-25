import request from './request'

export function listDocuments(kbId) {
  return request.get(`/kb/${kbId}/documents`)
}

export function uploadDocument(kbId, file) {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/kb/${kbId}/documents`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function deleteDocument(id) {
  return request.delete(`/documents/${id}`)
}

export function documentFileUrl(id, download = false) {
  const q = download ? '?download=true' : ''
  return `/api/v1/documents/${id}/file${q}`
}