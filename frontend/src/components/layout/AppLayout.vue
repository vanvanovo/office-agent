<template>
  <el-container class="app-layout">
    <el-aside width="200px" class="sidebar-aside">
      <Sidebar />
    </el-aside>
    <el-container direction="vertical">
      <el-header class="app-header">
        <span class="header-title">多智能体办公助手</span>
        <div class="header-right">
          <el-badge :value="unread" :hidden="unread === 0" :max="9">
            <el-button text :icon="Bell" @click="router.push('/messages')" />
          </el-badge>
          <el-tag size="small" type="info" effect="plain">{{ auth.user?.department ?? '' }}</el-tag>
          <span class="username">{{ auth.user?.name ?? auth.user?.emp_id }}</span>
          <el-dropdown @command="handleCommand">
            <el-button text :icon="ArrowDown" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="switch">切换身份</el-dropdown-item>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <div class="nav-progress-bar" :class="{ active: navigating }" />
      <el-main class="app-main">
        <router-view v-slot="{ Component }" :key="routerViewKey">
          <keep-alive :include="['UnifiedChatView']">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, onErrorCaptured, onMounted, onUnmounted } from 'vue'
import { ArrowDown, Bell } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import client from '@/api/client'
import Sidebar from './Sidebar.vue'

const auth = useAuthStore()
const router = useRouter()

// 站内信未读角标（30 秒轮询）
const unread = ref(0)
async function loadUnread() {
  try {
    const { data } = await client.get<{ messages: { is_read: number }[] }>('/me/messages')
    unread.value = data.messages.filter(m => m.is_read === 0).length
  } catch {
    /* 忽略 */
  }
}
let unreadTimer: number | undefined
onMounted(() => {
  loadUnread()
  unreadTimer = window.setInterval(loadUnread, 30_000)
})
onUnmounted(() => {
  if (unreadTimer) window.clearInterval(unreadTimer)
})

const navigating = ref(false)
router.beforeEach(() => { navigating.value = true })
router.afterEach(() => { navigating.value = false })

// 错误边界：渲染崩溃时强制重挂 RouterView
const routerViewKey = ref(0)
let lastRecoveryAt = 0
onErrorCaptured(() => {
  const now = Date.now()
  if (now - lastRecoveryAt > 500) {
    lastRecoveryAt = now
    routerViewKey.value++
  }
  return false
})

function handleCommand(cmd: string) {
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  } else if (cmd === 'switch') {
    auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.app-layout {
  height: 100vh;
}
.sidebar-aside {
  background: #001529;
  overflow: hidden;
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
  padding: 0 24px;
  height: 56px;
}
.header-title {
  font-size: 18px;
  font-weight: 600;
  color: #1677ff;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.username {
  font-size: 14px;
  color: #595959;
}
.nav-progress-bar {
  height: 3px;
  background: transparent;
  overflow: hidden;
  flex-shrink: 0;
}
.nav-progress-bar.active {
  background: #e8f0fe;
}
.nav-progress-bar.active::after {
  content: '';
  display: block;
  height: 100%;
  width: 40%;
  background: #1677ff;
  animation: nav-scan 0.9s ease-in-out infinite;
}
@keyframes nav-scan {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}
.app-main {
  background: #f5f7fa;
  overflow-y: auto;
  padding: 24px;
}
</style>
