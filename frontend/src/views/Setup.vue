<template>
  <div class="setup-container">
    <el-card class="setup-card">
      <template #header>
        <div class="setup-header">
          <img src="/qingniao.png" alt="青鸟" style="width: 64px; height: 64px; border-radius: 18px; box-shadow: 0 6px 18px rgba(45,66,82,.25)" />
          <h2>青鸟 · 初始化管理员账户</h2>
          <p class="setup-desc">首次使用，请创建管理员账号</p>
        </div>
      </template>

      <el-form @submit.prevent="handleSetup" label-width="0">
        <el-form-item>
          <el-input
            v-model="form.username"
            placeholder="用户名（至少 2 个字符）"
            :prefix-icon="User"
            size="large"
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码（至少 6 个字符）"
            :prefix-icon="Lock"
            size="large"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="确认密码"
            :prefix-icon="Lock"
            size="large"
            show-password
            @keyup.enter="handleSetup"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            :loading="loading"
            @click="handleSetup"
          >
            创建账户并登录
          </el-button>
        </el-form-item>
      </el-form>

      <el-alert type="warning" :closable="false" show-icon>
        创建后可在「系统设置」中启用两步验证 (OTP)。
      </el-alert>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '../api'
import { ElMessage } from 'element-plus'
import { User, Lock, CircleCheck } from '@element-plus/icons-vue'

const router = useRouter()
const loading = ref(false)
const form = ref({ username: '', password: '', confirmPassword: '' })

onMounted(async () => {
  try {
    const res = await authApi.status()
    if (!res.data.needs_setup) {
      // Already set up — go to login
      router.replace('/login')
    }
  } catch {
    // ignore
  }
})

async function handleSetup() {
  if (form.value.username.length < 2) {
    ElMessage.warning('用户名至少 2 个字符')
    return
  }
  if (form.value.password.length < 6) {
    ElMessage.warning('密码至少 6 个字符')
    return
  }
  if (form.value.password !== form.value.confirmPassword) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    const res = await authApi.setup(form.value.username, form.value.password)
    const data = res.data
    if (data.error) {
      ElMessage.error(data.error)
    } else if (data.token) {
      localStorage.setItem('token', data.token)
      ElMessage.success('账户创建成功，已自动登录')
      router.replace('/')
    }
  } catch (err) {
    const msg = err.response?.data?.detail || '创建失败'
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.setup-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
}
.setup-card {
  width: 420px;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
}
.setup-header {
  text-align: center;
}
.setup-header h2 {
  margin: 12px 0 4px;
  color: #303133;
  font-size: 20px;
}
.setup-desc {
  margin: 0;
  color: #909399;
  font-size: 13px;
}
</style>
