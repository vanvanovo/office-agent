<template>
  <div class="bookings-page">
    <div class="page-header">
      <span class="title">我的会议室预订</span>
      <el-button type="primary" plain size="small" :loading="loading" @click="load">
        刷新
      </el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="bookings" v-loading="loading" size="default">
        <el-table-column prop="booking_id" label="预订编号" width="200" />
        <el-table-column prop="room_id" label="会议室" width="110" />
        <el-table-column prop="date" label="日期" width="130" />
        <el-table-column label="时段" width="140">
          <template #default="{ row }">{{ row.start_time }}-{{ row.end_time }}</template>
        </el-table-column>
        <el-table-column prop="purpose" label="用途" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button size="small" type="danger" link @click="cancel(row.booking_id)">
              取消
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无进行中的预订，去「在线助手」订一间吧" />
        </template>
      </el-table>
    </el-card>

    <p class="tip">提示：取消操作也可以直接在「在线助手」里说「取消预订 BK…」。</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'

interface Booking {
  booking_id: string
  room_id: string
  date: string
  start_time: string
  end_time: string
  purpose?: string
}

const router = useRouter()
const bookings = ref<Booking[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const { data } = await client.get<{ bookings: Booking[] }>('/me/bookings')
    bookings.value = data.bookings
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function cancel(bookingId: string) {
  try {
    await ElMessageBox.confirm(`确定取消预订 ${bookingId} 吗？`, '取消预订', { type: 'warning' })
  } catch {
    return
  }
  // 交给「在线助手」走一遍对话链路（顺带演示 A2A 取消能力）
  router.push({ path: '/chat' })
  ElMessage.info(`请在对话里说：「取消预订 ${bookingId}」`)
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
