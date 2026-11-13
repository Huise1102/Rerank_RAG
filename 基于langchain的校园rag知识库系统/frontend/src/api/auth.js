import request from './request'

export function login(payload) {
  return request.post('/auth/login', payload)
}

export function logout() {
  return request.post('/auth/logout')
}

export function getMe() {
  return request.get('/auth/me')
}

export function changePassword(payload) {
  return request.post('/auth/change-password', payload)
}
