import request from './request'

// 检索日志（分页 + 按 kb 过滤）
export function getRetrievalLogs(params) {
  return request.get('/logs/retrieval', { params })
}

// 系统日志（条数 + 级别 / 关键字过滤）
export function getSystemLogs(params) {
  return request.get('/logs/system', { params })
}

// 清空检索日志
export function clearRetrievalLogs() {
  return request.delete('/logs/retrieval')
}

// 清空系统日志文件
export function clearSystemLogs() {
  return request.delete('/logs/system')
}
