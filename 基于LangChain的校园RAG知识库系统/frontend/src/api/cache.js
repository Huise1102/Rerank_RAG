import request from './request'

export function getCacheSettings() {
  return request.get('/admin/cache/settings')
}

export function updateCacheSettings(payload) {
  return request.patch('/admin/cache/settings', payload)
}

export function getCacheStats(days = 7) {
  return request.get('/admin/cache/stats', { params: { days } })
}

export function clearCache(payload) {
  return request.post('/admin/cache/clear', payload)
}

export function listCacheSamples(limit = 20) {
  return request.get('/admin/cache/samples', { params: { limit } })
}

export function flagCacheSample(key) {
  return request.post('/admin/cache/samples/flag', { key })
}
