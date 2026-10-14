import axios from 'axios'
import { ElMessage } from 'element-plus'

// 统一 Axios 实例：无鉴权拦截器，仅做响应解包与统一错误提示
const request = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
})

function extractError(error) {
  const response = error?.response
  const detail = response?.data?.detail
  if (Array.isArray(detail)) {
    // pydantic 校验错误
    return detail
      .map((d) => `${(d.loc || []).join('.')}: ${d.msg || ''}`)
      .join('; ')
  }
  if (typeof detail === 'string' && detail) return detail
  if (response) return `请求失败（HTTP ${response.status}）`
  if (error?.code === 'ECONNABORTED') return '请求超时'
  return error?.message || '网络异常，请检查后端服务是否已启动'
}

request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    ElMessage.error(extractError(error))
    return Promise.reject(error)
  }
)

export { extractError }
export default request
