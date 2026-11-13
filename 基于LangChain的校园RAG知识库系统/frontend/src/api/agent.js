import request from './request'

export function getAgentSettings() {
  return request.get('/admin/agent/settings')
}

export function updateAgentSettings(payload) {
  return request.patch('/admin/agent/settings', payload)
}
