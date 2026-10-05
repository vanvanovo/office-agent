<template>
  <div class="equipment-page">
    <div class="page-header">
      <span class="title">器材台账</span>
      <div class="filters">
        <el-input
          v-model="keyword"
          placeholder="搜索名称 / 品牌 / 编号"
          clearable
          style="width: 220px"
          @keyup.enter="load"
        />
        <el-button type="primary" plain size="small" :loading="loading" @click="load">查询</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table :data="items" v-loading="loading">
        <el-table-column prop="asset_id" label="编号" width="100" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="category" label="类别" width="110" />
        <el-table-column prop="department" label="归属部门" width="100" />
        <el-table-column prop="status" label="台账状态" width="100">
          <template #default="{ row }">
            {{ row.status === 'in_stock' ? '在库' : '已领用' }}
          </template>
        </el-table-column>
        <el-table-column label="可借状态" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="row.borrowable_now ? 'success' : 'info'">
              {{ row.borrowable_now ? '可借' : (row.holder ? row.holder + '借出' : '不可借') }}
            </el-tag>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有数据" />
        </template>
      </el-table>
    </el-card>

    <p class="tip">
      数据链路：页面 → 网关 → 器材查询 Agent → 器材 MCP（静态台账每日同步 + 可借状态实时查询）。
      报修请在「在线助手」里说「报修 P004」。
    </p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

interface Equipment {
  asset_id: string
  name: string
  category: string
  department?: string
  status: string
  borrowable_now: boolean
  holder?: string | null
}

const items = ref<Equipment[]>([])
const keyword = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const { data } = await client.get<{ equipment: Equipment[] }>('/equipment', {
      params: keyword.value ? { keyword: keyword.value } : {},
    })
    items.value = data.equipment
  } catch {
    ElMessage.error('加载失败')
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
.tip {
  margin-top: 12px;
  font-size: 12px;
  color: #8c8c8c;
}
</style>
