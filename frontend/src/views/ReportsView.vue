<template>
  <div class="reports-page">
    <div class="page-header">
      <span class="title">运营报表（行政 / IT）</span>
      <div class="filters">
        <el-date-picker
          v-model="range"
          type="daterange"
          value-format="YYYY-MM-DD"
          size="small"
          style="width: 260px"
        />
        <el-button type="primary" plain size="small" :loading="loading" @click="load">查询</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="never">
          <div class="card-title">会议室利用率</div>
          <el-table :data="rooms" v-loading="loading" size="small" max-height="460">
            <el-table-column prop="room_id" label="编号" width="90" />
            <el-table-column prop="name" label="会议室" />
            <el-table-column prop="booking_count" label="预订次数" width="90" />
            <el-table-column prop="booked_hours" label="占用小时" width="90" />
            <el-table-column label="热度" min-width="130">
              <template #default="{ row }">
                <el-progress :percentage="heat(row.booked_hours)" :stroke-width="10" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="never" class="mb16">
          <div class="card-title">报修热点（按类别）</div>
          <div v-for="c in repairs.by_category" :key="c.category" class="hot-line">
            <span class="hot-name">{{ c.category }}</span>
            <el-progress :percentage="heatCat(c.ticket_count)" :stroke-width="10" class="hot-bar" />
            <span class="count">{{ c.ticket_count }}</span>
          </div>
          <el-empty v-if="!repairs.by_category.length" description="区间内没有报修" :image-size="60" />
        </el-card>

        <el-card shadow="never">
          <div class="card-title">高频报修资产 Top5</div>
          <div v-for="a in repairs.top_assets" :key="a.asset_id" class="hot-line">
            <span class="hot-name">{{ a.asset_id }} {{ a.name }}</span>
            <span class="count">{{ a.ticket_count }} 次</span>
          </div>
          <el-empty v-if="!repairs.top_assets.length" description="暂无数据" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <p class="tip">
      数据链路：页面 → 网关（仅 admin）→ MCP 参数化工具 → Mock OA / 台账库；不复制业务数据，按需聚合。
    </p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

interface RoomRow {
  room_id: string
  name: string
  booking_count: number
  booked_hours: number
}
interface RepairReport {
  by_category: { category: string; ticket_count: number }[]
  top_assets: { asset_id: string; name: string; ticket_count: number }[]
}

const loading = ref(false)
const rooms = ref<RoomRow[]>([])
const repairs = ref<RepairReport>({ by_category: [], top_assets: [] })

function iso(d: Date) {
  return d.toISOString().slice(0, 10)
}
const today = new Date()
const weekAgo = new Date(Date.now() - 6 * 86400_000)
const tomorrow = new Date(Date.now() + 86400_000)
const range = ref<[string, string]>([iso(weekAgo), iso(tomorrow)])

function heat(hours: number) {
  const max = Math.max(...rooms.value.map(r => r.booked_hours), 1)
  return Math.max(Math.round((hours / max) * 100), hours > 0 ? 2 : 0)
}
function heatCat(count: number) {
  const max = Math.max(...repairs.value.by_category.map(c => c.ticket_count), 1)
  return Math.round((count / max) * 100)
}

async function load() {
  if (!range.value || range.value.length !== 2) return
  loading.value = true
  try {
    const params = { from: range.value[0], to: range.value[1] }
    const [roomsRes, repairsRes] = await Promise.all([
      client.get('/reports/rooms', { params }),
      client.get('/reports/repairs', { params }),
    ])
    rooms.value = roomsRes.data.rooms ?? []
    repairs.value = {
      by_category: repairsRes.data.by_category ?? [],
      top_assets: repairsRes.data.top_assets ?? [],
    }
  } catch {
    ElMessage.error('报表加载失败（需要行政/IT 角色）')
  } finally {
    loading.value = false
  }
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
.filters {
  display: flex;
  gap: 8px;
  align-items: center;
}
.card-title {
  font-size: 13px;
  font-weight: 600;
  color: #1677ff;
  margin-bottom: 10px;
}
.mb16 {
  margin-bottom: 16px;
}
.hot-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  font-size: 13px;
  color: #4b4b4b;
}
.hot-name {
  min-width: 110px;
}
.hot-bar {
  flex: 1;
}
.count {
  min-width: 46px;
  text-align: right;
  color: #8c8c8c;
}
.tip {
  margin-top: 12px;
  font-size: 12px;
  color: #8c8c8c;
}
</style>
