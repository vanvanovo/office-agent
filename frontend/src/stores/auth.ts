import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Employee } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('office-agent-token'))
  const user = ref<Employee | null>((() => {
    try {
      return JSON.parse(localStorage.getItem('office-agent-user') ?? 'null')
    } catch {
      return null
    }
  })())

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function login(accessToken: string, emp: Employee) {
    token.value = accessToken
    user.value = emp
    localStorage.setItem('office-agent-token', accessToken)
    localStorage.setItem('office-agent-user', JSON.stringify(emp))
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('office-agent-token')
    localStorage.removeItem('office-agent-user')
  }

  return { token, user, isLoggedIn, isAdmin, login, logout }
})
