<template>
  <div class="messages-page">
    <div class="page-header">
      <span class="title">站内信</span>
      <el-button size="small" plain :disabled="unreadCount === 0" @click="readAll">
        全部已读
      </el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="messages" v-loading="loading" @row-click="onRowClick">
        <el-table-column label="" width="46">
          <template #default="{ row }">
            <el-badge is-dot :hidden="row.is_read === 1" />
          </template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.type === 'ticket_overdue' ? 'warning' : 'primary'">
              {{ typeLabel(row.type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="内容" />
        <el-table-column prop="created_at" label="时间" width="170" />
        <template #empty>
          <el-empty description="暂无站内信" />
        </template>
      </el-table>
    </el-card>

    <p class="tip">
      提醒由网关定时任务生成：会议开始前 15 分钟、工单超过 SLA；点击消息可标记已读。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

interface Message {
  msg_id: number
  type: string
  title: string
  is_read: number
  created_at: string
}

const messages = ref<Message[]>([])
const loading = ref(false)

const unreadCount = computed(() => messages.value.filter(m => m.is_read === 0).length)

const TYPE_LABELS: Record<string, string> = {
  meeting_soon: '会议提醒',
  ticket_overdue: '工单超时',
}
function typeLabel(t: string) {
  return TYPE_LABELS[t] ?? t
}

async function load() {
  loading.value = true
  try {
    const { data } = await client.get<{ messages: Message[] }>('/me/messages')
    messages.value = data.messages
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function onRowClick(row: Message) {
  if (row.is_read === 1) return
  try {
    await client.post(`/me/messages/${row.msg_id}/read`)
    row.is_read = 1
  } catch {
    /* 忽略 */
  }
}

async function readAll() {
  try {
    await client.post('/me/messages/read-all')
    messages.value.forEach(m => (m.is_read = 1))
  } catch {
    ElMessage.error('操作失败')
  }
}

let timer: number | undefined
onMounted(() => {
  load()
  timer = window.setInterval(load, 30_000)
})
onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.title {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
}
.tip {
  margin-top: 12px;
  font-size: 12px;
  color: #8c8c8c;
}
:deep(.el-table__row) {
  cursor: pointer;
}
</style>
