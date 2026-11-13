import request from './request'

export function myFeedback() {
  return request.get('/feedback/mine')
}

export function myFeedbackDetail(id) {
  return request.get(`/feedback/mine/${id}`)
}

export function adminFeedbackList(params) {
  return request.get('/admin/feedback', { params })
}

export function adminFeedbackStats() {
  return request.get('/admin/feedback/stats')
}

export function adminFeedbackDetail(id) {
  return request.get(`/admin/feedback/${id}`)
}

export function handleFeedback(id, payload) {
  return request.patch(`/admin/feedback/${id}`, payload)
}
