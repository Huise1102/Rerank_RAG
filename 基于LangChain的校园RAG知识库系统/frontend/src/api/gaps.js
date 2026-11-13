import request from './request'

export function listGaps(params) {
  return request.get('/admin/gaps', { params })
}

export function scanGaps(days = 30) {
  return request.post('/admin/gaps/scan', null, { params: { days } })
}

export function updateGap(id, payload) {
  return request.patch(`/admin/gaps/${id}`, payload)
}

export function exportGaps() {
  return request.get('/admin/gaps/export', { responseType: 'blob' })
}
