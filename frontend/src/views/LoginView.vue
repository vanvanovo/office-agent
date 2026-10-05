<template>
  <div class="login-page">
    <el-card class="login-card">
      <div class="login-header">
        <div class="login-logo">🏢</div>
        <h2>多智能体办公助手</h2>
        <p>选择身份进入（演示环境）· 生产环境接企业 SSO</p>
      </div>

      <div v-loading="loading" class="emp-list">
        <div v-for="emp in employees" :key="emp.emp_id" class="emp-item" @click="handleLogin(emp)">
          <div class="emp-avatar">{{ emp.name.slice(0, 1) }}</div>
          <div class="emp-info">
            <div class="emp-name">
              {{ emp.name }}
              <el-tag v-if="emp.role === 'admin'" size="small" type="warning" effect="plain">
                行政 / IT
              </el-tag>
            </div>
            <div class="emp-dept">{{ emp.emp_id }} · {{ emp.department ?? '—' }}</div>
          </div>
          <el-icon class="emp-arrow"><ArrowRight /></el-icon>
        </div>
        <el-empty v-if="!loading && employees.length === 0" description="无法加载员工列表（请确认后端已启动）" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'
import { authApi, type Employee } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const employees = ref<Employee[]>([])
const loading = ref(false)

onMounted(async () => {
  try {
    const { data } = await authApi.employees()
    employees.value = data.employees
  } catch {
    ElMessage.error('后端未启动或数据库未就绪')
  }
})

async function handleLogin(emp: Employee) {
  loading.value = true
  try {
    const { data } = await authApi.login(emp.emp_id)
    auth.login(data.access_token, data.emp)
    router.push('/chat')
  } catch {
    ElMessage.error('登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1d3557 0%, #457b9d 100%);
}
.login-card {
  width: 440px;
  border-radius: 12px;
}
.login-header {
  text-align: center;
  margin-bottom: 20px;
}
.login-logo {
  font-size: 44px;
  margin-bottom: 8px;
}
.login-header h2 {
  margin: 0 0 4px;
  font-size: 22px;
  color: #1a1a1a;
}
.login-header p {
  margin: 0;
  color: #8c8c8c;
  font-size: 13px;
}
.emp-list {
  min-height: 120px;
}
.emp-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.2s;
}
.emp-item:hover {
  border-color: #1677ff;
  background: #f0f7ff;
}
.emp-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #1677ff;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  flex-shrink: 0;
}
.emp-info {
  flex: 1;
}
.emp-name {
  font-size: 14px;
  color: #1a1a1a;
  display: flex;
  align-items: center;
  gap: 6px;
}
.emp-dept {
  font-size: 12px;
  color: #8c8c8c;
  margin-top: 2px;
}
.emp-arrow {
  color: #c0c4cc;
}
</style>
