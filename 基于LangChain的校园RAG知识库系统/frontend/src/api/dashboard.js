import request from './request'

export function getDashboardSummary(params) {
  return request.get('/admin/dashboard/summary', { params })
}

export function getDashboardTrends(params) {
  return request.get('/admin/dashboard/trends', { params })
}

export function getDepartmentComparison(params) {
  return request.get('/admin/dashboard/departments', { params })
}

export function listReviewDue(days = 30) {
  return request.get('/admin/review/due', { params: { days } })
}

export function reviewDocument(documentId, payload) {
  return request.post(`/admin/review/${documentId}/action`, payload)
}

export function notifyReviewDue(days = 30) {
  return request.post('/admin/review/notify', null, { params: { days } })
}
