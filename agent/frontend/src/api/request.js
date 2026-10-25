import axios from 'axios'
import { message } from '../utils/toast'

// 后端统一返回 { detail: "..." } 的 FastAPI 错误体，这里尽量摘出人类可读文案并提示。
export function extractError(error) {
  const resp = error?.response
  const data = resp?.data
  if (data && typeof data === 'object') {
    const detail = data.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const first = detail[0]
      if (first?.msg) return first.msg.replace(/^Value error,\s*/, '')
    }
  }
  if (typeof data === 'string' && data) return data
  return error?.message || '请求失败，请稍后重试'
}

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
})

request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const msg = extractError(error)
    message.error(msg)
    return Promise.reject(new Error(msg))
  }
)

export default request