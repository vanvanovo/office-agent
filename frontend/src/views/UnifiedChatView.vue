<template>
  <div class="chat-page">
    <div class="chat-header">
      <div>
        <span class="title">办公助手</span>
        <span class="subtitle">查 / 订 / 改 / 取消会议室 · 我的预订</span>
      </div>
      <el-button text @click="newSession">新会话</el-button>
    </div>

    <div ref="listRef" class="chat-list">
      <div v-if="messages.length === 0" class="empty">
        <p>你好，我是办公助手。试试这样说：</p>
        <div class="quick">
          <el-tag v-for="q in quickPrompts" :key="q" class="quick-item" @click="sendText(q)">
            {{ q }}
          </el-tag>
        </div>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.content }}</div>

        <div v-else class="assistant-block">
          <div v-if="m.intents && m.intents.length" class="intent-row">
            <el-tag v-for="it in m.intents" :key="it" size="small" effect="plain">
              {{ intentLabel(it) }}
            </el-tag>
          </div>

          <div v-if="m.content" class="bubble assistant-bubble" :class="{ 'bubble-error': m.error }">
            <MarkdownRenderer :content="m.content" />
          </div>
          <div v-else-if="isStreaming && i === messages.length - 1"
               class="bubble assistant-bubble typing">
            {{ progressLabel || '思考中…' }}
          </div>

          <template v-for="(c, ci) in m.cards || []" :key="ci">
            <div v-if="c.card_type === 'room_list'" class="card">
              <div class="card-title">可用会议室</div>
              <el-table :data="c.payload.rooms" size="small">
                <el-table-column prop="room_id" label="编号" width="90" />
                <el-table-column prop="name" label="名称" />
                <el-table-column prop="capacity" label="人数" width="70" />
                <el-table-column label="操作" width="90">
                  <template #default="{ row }">
                    <el-button size="small" type="primary" link @click="bookRoom(row, m.slots)">
                      预订
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div v-else-if="c.card_type === 'booking'" class="card">
              <div class="card-title">预订凭证</div>
              <div class="card-line">编号：{{ c.payload.booking_id }}</div>
              <div class="card-line">
                {{ c.payload.date }} {{ c.payload.start_time }}-{{ c.payload.end_time }} ·
                {{ c.payload.room_id }}
              </div>
              <el-button size="small" type="danger" link @click="cancelBooking(c.payload.booking_id)">
                取消此预订
              </el-button>
            </div>

            <div v-else-if="c.card_type === 'booking_list'" class="card">
              <div class="card-title">我的预订（{{ (c.payload.bookings || []).length }}）</div>
              <div v-for="b in c.payload.bookings" :key="b.booking_id" class="card-line">
                {{ b.date }} {{ b.start_time }}-{{ b.end_time }} · {{ b.room_id }} · {{ b.booking_id }}
                <el-button size="small" type="danger" link @click="cancelBooking(b.booking_id)">
                  取消
                </el-button>
              </div>
            </div>

            <div v-else-if="c.card_type === 'equipment_list'" class="card">
              <div class="card-title">器材台账</div>
              <el-table :data="c.payload.equipment" size="small">
                <el-table-column prop="asset_id" label="编号" width="90" />
                <el-table-column prop="name" label="名称" />
                <el-table-column prop="department" label="部门" width="90" />
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.borrowable_now ? 'success' : 'info'">
                      {{ row.borrowable_now ? '可借' : (row.holder ? row.holder + '借出' : '不可借') }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="80">
                  <template #default="{ row }">
                    <el-button size="small" type="warning" link @click="repairAsset(row)">
                      报修
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div v-else-if="c.card_type === 'repair_ticket'" class="card">
              <div class="card-title">报修工单</div>
              <div class="card-line">工单号：{{ c.payload.ticket_no }}</div>
              <div class="card-line">
                {{ c.payload.asset_id || '—' }} · {{ c.payload.fault_desc }} ·
                {{ ticketStatusLabel(c.payload.status) }}
              </div>
              <div class="card-line">SLA：{{ c.payload.sla_due_at || '—' }}</div>
              <el-button size="small" type="primary" link
                         @click="sendText(`帮我催一下 ${c.payload.ticket_no}`)">
                催单
              </el-button>
            </div>

            <div v-else-if="c.card_type === 'ticket_list'" class="card">
              <div class="card-title">我的工单（{{ (c.payload.tickets || []).length }}）</div>
              <div v-for="t in c.payload.tickets" :key="t.ticket_no" class="card-line">
                {{ t.ticket_no }} · {{ t.asset_id || '—' }} · {{ ticketStatusLabel(t.status) }}
                <el-button size="small" type="primary" link
                           @click="sendText(`帮我催一下 ${t.ticket_no}`)">催单</el-button>
              </div>
            </div>

            <div v-else-if="c.card_type === 'rag_sources'" class="card">
              <div class="card-title">知识来源（{{ (c.payload.sources || []).length }}）</div>
              <div v-for="(s, si) in c.payload.sources" :key="si" class="card-line source-line">
                <el-tag size="small" effect="plain">{{ s.source_name }}</el-tag>
                <span class="source-snippet">{{ s.snippet }}</span>
              </div>
            </div>

            <div v-else-if="c.card_type === 'recurring_result'" class="card">
              <div class="card-title">周期预订结果</div>
              <div class="card-line">
                {{ c.payload.room_id }} · 每周{{ weekdayLabel(c.payload.weekday) }}
                {{ c.payload.slot }} · 截止 {{ c.payload.until }}
              </div>
              <div class="card-line">
                成功 {{ c.payload.booked }} 次 / 冲突 {{ c.payload.conflict }} 次 ·
                系列号 {{ c.payload.series_id }}
              </div>
              <div v-if="conflictDates(c.payload).length" class="card-line">
                冲突日期：{{ conflictDates(c.payload).join('、') }}
              </div>
            </div>
          </template>

          <div v-if="m.meta" class="meta-line">
            用时 {{ m.meta.elapsed_ms }}ms
            <el-tag v-if="m.meta.degraded" size="small" type="warning">部分服务降级</el-tag>
          </div>
        </div>
      </div>
    </div>

    <div class="chat-input">
      <el-input
        v-model="input"
        :disabled="isStreaming"
        placeholder="说说你的办公需求，例如：明天下午3点A栋有哪些空会议室"
        @keyup.enter="sendText()"
      />
      <el-button type="primary" :loading="isStreaming" @click="sendText()">发送</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'
import { useOfficeChat } from '@/composables/useOfficeChat'

const { messages, isStreaming, progressLabel, send, reset } = useOfficeChat()

const input = ref('')
const listRef = ref<HTMLElement>()
const sessionId = ref('')

const quickPrompts = [
  '明天下午3点A栋有哪些空会议室',
  '帮我订个10人的会议室',
  '设计部还有几台投影仪可以借',
  'P004 投影仪屏幕坏了帮我报修',
  '每周五10点到11点帮我订4周B-601',
  '报销流程是什么',
]

const INTENT_LABELS: Record<string, string> = {
  meeting_query: '查会议室',
  meeting_book: '订会议室',
  meeting_reschedule: '改期',
  meeting_cancel: '取消预订',
  meeting_my: '我的预订',
  chat: '闲聊',
  human: '人工',
  out_of_scope: '超范围',
}
function intentLabel(it: string) {
  return INTENT_LABELS[it] ?? it
}

onMounted(() => {
  let sid = localStorage.getItem('office-session-id')
  if (!sid) {
    sid = crypto.randomUUID()
    localStorage.setItem('office-session-id', sid)
  }
  sessionId.value = sid
})

function newSession() {
  const sid = crypto.randomUUID()
  localStorage.setItem('office-session-id', sid)
  sessionId.value = sid
  reset()
}

async function scrollToBottom() {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

watch(messages, scrollToBottom, { deep: true })

async function sendText(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || isStreaming.value) return
  input.value = ''
  await send(sessionId.value, content)
}

function bookRoom(row: Record<string, any>, slots?: Record<string, any>) {
  const parts = [`订 ${row.room_id}`]
  if (slots?.date) parts.push(slots.date)
  if (slots?.start_time) parts.push(`${slots.start_time}-${slots.end_time ?? ''}`)
  sendText(parts.join(' '))
}

function cancelBooking(bookingId: string) {
  sendText(`取消预订 ${bookingId}`)
}

function repairAsset(row: Record<string, any>) {
  sendText(`报修 ${row.asset_id}`)
}

const TICKET_STATUS: Record<string, string> = {
  open: '待处理',
  in_progress: '处理中',
  done: '已完成',
  closed: '已关闭',
}
function ticketStatusLabel(s: string) {
  return TICKET_STATUS[s] ?? s ?? '—'
}

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
function weekdayLabel(w: number) {
  return WEEKDAYS[(w ?? 1) - 1] ?? w
}
function conflictDates(payload: Record<string, any>): string[] {
  return (payload?.results ?? [])
    .filter((r: Record<string, any>) => r.status === 'conflict')
    .map((r: Record<string, any>) => r.date)
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 104px);
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
  overflow: hidden;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid #f0f0f0;
}
.title {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
  margin-right: 10px;
}
.subtitle {
  font-size: 12px;
  color: #8c8c8c;
}
.chat-list {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
}
.empty {
  color: #8c8c8c;
  font-size: 14px;
  padding-top: 40px;
  text-align: center;
}
.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 12px;
}
.quick-item {
  cursor: pointer;
}
.msg {
  display: flex;
  margin-bottom: 16px;
}
.msg.user {
  justify-content: flex-end;
}
.assistant-block {
  max-width: 86%;
}
.bubble {
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}
.user-bubble {
  background: #1677ff;
  color: #fff;
  max-width: 70%;
  white-space: pre-wrap;
}
.assistant-bubble {
  background: #f5f7fa;
  color: #1a1a1a;
}
.bubble-error {
  background: #fff2f0;
  color: #cf1322;
}
.typing {
  color: #8c8c8c;
}
.intent-row {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
}
.card {
  margin-top: 10px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafcff;
}
.card-title {
  font-size: 13px;
  font-weight: 600;
  color: #1677ff;
  margin-bottom: 8px;
}
.card-line {
  font-size: 13px;
  color: #4b4b4b;
  line-height: 2;
}
.meta-line {
  margin-top: 6px;
  font-size: 12px;
  color: #b0b0b0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.source-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.source-snippet {
  color: #7a7a7a;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 70%;
}
.chat-input {
  display: flex;
  gap: 10px;
  padding: 12px 20px;
  border-top: 1px solid #f0f0f0;
  background: #fff;
}
</style>
