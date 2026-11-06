import request from './request'

export function getModelConfig() {
  return request.get('/model-config')
}

export function saveModelConfig(payload) {
  return request.put('/model-config', payload)
}

export function testModelConfig(payload) {
  return request.post('/model-config/test', payload)
}

export function listOllamaModels(baseUrl) {
  return request.get('/model-config/ollama-models', { params: { base_url: baseUrl } })
}
