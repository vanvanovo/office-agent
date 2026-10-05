<template>
  <div class="sidebar">
    <div class="logo">
      <span>🏢 办公助手</span>
    </div>
    <nav class="nav-list">
      <RouterLink to="/chat" class="nav-item" :class="{ 'nav-item--active': isActive('/chat') }">
        <el-icon><ChatDotRound /></el-icon>
        <span>在线助手</span>
      </RouterLink>
      <RouterLink to="/bookings" class="nav-item" :class="{ 'nav-item--active': isActive('/bookings') }">
        <el-icon><Calendar /></el-icon>
        <span>我的预订</span>
      </RouterLink>
      <RouterLink to="/equipment" class="nav-item" :class="{ 'nav-item--active': isActive('/equipment') }">
        <el-icon><Box /></el-icon>
        <span>器材台账</span>
      </RouterLink>
      <RouterLink to="/tickets" class="nav-item" :class="{ 'nav-item--active': isActive('/tickets') }">
        <el-icon><Tickets /></el-icon>
        <span>我的工单</span>
      </RouterLink>
      <RouterLink to="/messages" class="nav-item" :class="{ 'nav-item--active': isActive('/messages') }">
        <el-icon><Bell /></el-icon>
        <span>站内信</span>
      </RouterLink>
      <template v-if="auth.isAdmin">
        <div class="nav-divider" />
        <RouterLink to="/reports" class="nav-item" :class="{ 'nav-item--active': isActive('/reports') }">
          <el-icon><DataAnalysis /></el-icon>
          <span>运营报表</span>
        </RouterLink>
      </template>
      <div class="nav-divider" />
      <div class="nav-note">
        <p>支持：会议室查/订/改/取消</p>
        <p>器材查询 / 报修 / 催单</p>
      </div>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { ChatDotRound, Calendar, Box, Tickets, DataAnalysis, Bell } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()

function isActive(prefix: string) {
  return route.path === prefix || route.path.startsWith(prefix + '/')
}
</script>

<style scoped>
.sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  border-bottom: 1px solid #ffffff1a;
  flex-shrink: 0;
}
.nav-list {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
  flex: 1;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 20px;
  color: #ffffffa6;
  text-decoration: none;
  font-size: 14px;
  cursor: pointer;
  transition: background-color 0.2s, color 0.2s;
  user-select: none;
}
.nav-item:hover {
  background-color: #ffffff14;
  color: #fff;
}
.nav-item--active {
  background-color: #1677ff;
  color: #fff;
}
.nav-item .el-icon {
  font-size: 16px;
  flex-shrink: 0;
}
.nav-divider {
  border: none;
  border-top: 1px solid #ffffff1a;
  margin: 8px 0;
}
.nav-note {
  padding: 8px 20px;
  color: #ffffff59;
  font-size: 12px;
  line-height: 1.8;
}
.nav-note p {
  margin: 0;
}
</style>
