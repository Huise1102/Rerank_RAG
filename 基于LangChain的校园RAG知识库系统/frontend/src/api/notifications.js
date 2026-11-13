import request from './request'

export function listNotifications(params = {}) {
  return request.get('/notifications', { params })
}

export function readNotification(id) {
  return request.patch(`/notifications/${id}`)
}

export function readAllNotifications() {
  return request.patch('/notifications/read-all')
}
