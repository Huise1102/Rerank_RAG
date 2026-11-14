import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 60000
})

request.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    const isLoginRequest = String(error.config?.url || '').includes('/auth/login')
    if (status === 401) {
      sessionStorage.removeItem('token')
      sessionStorage.removeItem('user')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    } else if (isLoginRequest && (status === 403 || status === 429)) {
      // 登录页自行展示失败原因，避免重复提示
    } else if (status === 403) {
      ElMessage.error(detail || '无权限访问')
    } else if (detail) {
      ElMessage.error(String(detail).slice(0, 120))
    } else if (status !== undefined) {
      ElMessage.error(`请求失败，状态码 ${status}`)
    }
    return Promise.reject(error)
  }
)

export default request
