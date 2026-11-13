import request from './request'

export function listEvaluationReports() {
  return request.get('/admin/evaluation/reports')
}

export function getEvaluationReport(id) {
  return request.get(`/admin/evaluation/reports/${id}`)
}

export function validateTestset(payload) {
  return request.post('/admin/evaluation/validate', payload, { timeout: 120000 })
}

export function runEvaluation(payload) {
  return request.post('/admin/evaluation/run', payload, { timeout: 1800000 })
}
