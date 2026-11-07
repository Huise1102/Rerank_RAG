import request from './request'

export function listRetrievalLogs(params) {
  return request.get('/logs/retrieval', { params })
}

export function systemLogs(params) {
  return request.get('/logs/system', { params })
}

export function clearRetrievalLogs() {
  return request.delete('/logs/retrieval')
}

export function clearSystemLogs() {
  return request.delete('/logs/system')
}