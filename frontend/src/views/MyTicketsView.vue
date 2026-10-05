<template>
  <div class="tickets-page">
    <div class="page-header">
      <span class="title">我的报修工单</span>
      <el-button type="primary" plain size="small" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="tickets" v-loading="loading">
        <el-table-column prop="ticket_no" label="工单号" width="180" />
        <el-table-column prop="asset_id" label="设备" width="100">
          <template #default="{ row }">{{ row.asset_id || '—' }}</template>
        </el-table-column>
        <el-table-column prop="fault_desc" label="故障描述" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'done' ? 'success' : 'warning'">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="sla_due_at" label="SLA 截止" width="170" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="urge(row.ticket_no)">催单</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无工单，设备坏了去「在线助手」说「报修 P004」" />
        </template>
      </el-table>
    </el-card>

    <p class="tip">提示：催单也可以直接在「在线助手」里说「帮我催一下 RT…」。</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

interface Ticket {
  ticket_no: string
  asset_id?: string | null
  fault_desc?: string
  status: string
  sla_due_at?: string
}

const router = useRouter()
const tickets = ref<Ticket[]>([])
const loading = ref(false)

const STATUS: Record<string, string> = {
  open: '待处理',
  in_progress: '处理中',
  done: '已完成',
  closed: '已关闭',
}
function statusLabel(s: string) {
  return STATUS[s] ?? s
}

async function load() {
  loading.value = true
  try {
    const { data } = await client.get<{ tickets: Ticket[] }>('/me/tickets')
    tickets.value = data.tickets
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

function urge(ticketNo: string) {
  router.push({ path: '/chat' })
  ElMessage.info(`请在对话里说：「帮我催一下 ${ticketNo}」`)
}

onMounted(load)
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
</style>
