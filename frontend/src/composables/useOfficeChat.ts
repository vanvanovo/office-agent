import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

export interface ChatCard {
  card_type: string
  payload: Record<string, any>
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  intents?: string[]
  slots?: Record<string, any>
  cards?: ChatCard[]
  meta?: { elapsed_ms?: number; degraded?: boolean }
  error?: boolean
}

/**
 * 办公助手对话（SSE）：
 * 事件协议：routing / progress / card / token / meta / done / error
 */
export function useOfficeChat() {
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const progressLabel = ref('')

  async function send(sessionId: string, text: string) {
    const auth = useAuthStore()
    messages.value.push({ role: 'user', content: text })
    const assistant: ChatMessage = { role: 'assistant', content: '', cards: [], intents: [], slots: {} }
    messages.value.push(assistant)

    isStreaming.value = true
    progressLabel.value = '正在连接…'

    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
    try {
      const response = await fetch(`${baseUrl}/api/v1/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${auth.token}`,
        },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      })

      if (response.status === 401) {
        auth.logout()
        window.location.href = '/login'
        return
      }
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw) continue
          let evt: any
          try {
            evt = JSON.parse(raw)
          } catch {
            continue
          }
          if (evt.type === 'routing') {
            assistant.intents = evt.intents ?? []
            assistant.slots = evt.slots ?? {}
          } else if (evt.type === 'progress') {
            progressLabel.value = evt.label ?? ''
          } else if (evt.type === 'card') {
            assistant.cards!.push({ card_type: evt.card_type, payload: evt.payload ?? {} })
          } else if (evt.type === 'token') {
            assistant.content += evt.content ?? ''
            progressLabel.value = ''
          } else if (evt.type === 'meta') {
            assistant.meta = { elapsed_ms: evt.elapsed_ms, degraded: evt.degraded }
          } else if (evt.type === 'error') {
            assistant.error = true
            assistant.content = evt.message ?? '服务异常'
          }
        }
      }
    } catch (e) {
      assistant.error = true
      assistant.content = assistant.content || (e instanceof Error ? `连接失败：${e.message}` : '连接失败')
    } finally {
      isStreaming.value = false
      progressLabel.value = ''
    }
  }

  function reset() {
    messages.value = []
  }

  return { messages, isStreaming, progressLabel, send, reset }
}
