import request from './request'

export function listRetrievalLogs(params) {
  return request.get('/logs/retrieval', { params })
}

export function systemLogs(params) {
  return request.get('/logs/system', { params })
}