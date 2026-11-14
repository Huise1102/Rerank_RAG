import { defineStore } from 'pinia'
import { getMe } from '../api/auth'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: sessionStorage.getItem('token') || '',
    user: JSON.parse(sessionStorage.getItem('user') || 'null')
  }),
  getters: {
    isAdmin: (state) => state.user?.role === 'admin',
    displayName: (state) => state.user?.display_name || state.user?.username || ''
  },
  actions: {
    setAuth(token, user) {
      this.token = token
      this.user = user
      sessionStorage.setItem('token', token)
      sessionStorage.setItem('user', JSON.stringify(user))
    },
    clear() {
      this.token = ''
      this.user = null
      sessionStorage.removeItem('token')
      sessionStorage.removeItem('user')
    },
    async refresh() {
      const user = await getMe()
      this.user = user
      sessionStorage.setItem('user', JSON.stringify(user))
      return user
    }
  }
})
