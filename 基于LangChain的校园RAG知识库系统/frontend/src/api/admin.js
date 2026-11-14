import request from './request'

export function listUsers(params) {
  return request.get('/admin/users', { params })
}

export function createUser(payload) {
  return request.post('/admin/users', payload)
}

export function updateUser(id, payload) {
  return request.patch(`/admin/users/${id}`, payload)
}

export function resetPassword(id, payload) {
  return request.post(`/admin/users/${id}/reset-password`, payload)
}

export function disableUser(id) {
  return request.delete(`/admin/users/${id}`)
}

export function closeUser(id) {
  return request.post(`/admin/users/${id}/close`)
}

export function listDepartments() {
  return request.get('/admin/departments')
}

export function createDepartment(payload) {
  return request.post('/admin/departments', payload)
}

export function updateDepartment(id, payload) {
  return request.patch(`/admin/departments/${id}`, payload)
}

export function listRetrievalLogs(params) {
  return request.get('/admin/logs/retrieval', { params })
}

export function listAuditLogs(params) {
  return request.get('/admin/logs/audit', { params })
}

export function listSystemLogs(params) {
  return request.get('/admin/logs/system', { params })
}

export function clearLogs(payload) {
  return request.post('/admin/logs/clear', payload)
}
