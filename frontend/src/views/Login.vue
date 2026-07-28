<template>
  <div class="login-container">
    <el-card class="login-card">
      <template #header>
        <div class="login-header">
          <el-icon size="32" color="#409eff"><Promotion /></el-icon>
          <h2>TG → 飞书转发系统</h2>
        </div>
      </template>

      <!-- Step 1: Username + Password -->
      <el-form v-if="step === 1" @submit.prevent="handleLogin" label-width="0">
        <el-form-item>
          <el-input
            v-model="form.username"
            placeholder="用户名"
            :prefix-icon="User"
            size="large"
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            size="large"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            :loading="loading"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>

      <!-- Step 2: OTP Code -->
      <div v-if="step === 2" class="otp-step">
        <el-alert type="info" :closable="false" show-icon class="otp-hint">
          请输入验证器 App 中显示的 6 位动态验证码
        </el-alert>
        <el-form @submit.prevent="handleOtp" label-width="0" style="margin-top: 20px">
          <el-form-item>
            <el-input
              v-model="otpCode"
              placeholder="6 位验证码"
              size="large"
              maxlength="6"
              :prefix-icon="Key"
              @keyup.enter="handleOtp"
            >
              <template #append>
                <span style="font-family: monospace; letter-spacing: 2px">{{ otpCode }}</span>
              </template>
            </el-input>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="handleOtp">
              验证并登录
            </el-button>
          </el-form-item>
          <el-form-item>
            <el-button size="default" style="width: 100%" @click="step = 1; otpCode = ''">
              返回重新登录
            </el-button>
          </el-form-item>
        </el-form>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '../api'
import { ElMessage } from 'element-plus'
import { User, Lock, Key } from '@element-plus/icons-vue'

const router = useRouter()
const step = ref(1)
const loading = ref(false)
const form = ref({ username: '', password: '' })
const otpCode = ref('')
const tempToken = ref('')

onMounted(async () => {
  // Check if setup is needed or already logged in
  try {
    const res = await authApi.status()
    if (res.data.needs_setup) {
      router.replace('/setup')
      return
    }
    if (res.data.authenticated && localStorage.getItem('token')) {
      router.replace('/')
    }
  } catch {
    // backend might be starting up — ignore
  }
})

async function handleLogin() {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res = await authApi.login(form.value.username, form.value.password)
    const data = res.data
    if (data.error) {
      ElMessage.error(data.error)
    } else if (data.requires_otp) {
      tempToken.value = data.temp_token
      step.value = 2
      ElMessage.info('请输入两步验证码')
    } else if (data.token) {
      localStorage.setItem('token', data.token)
      ElMessage.success('登录成功')
      router.replace('/')
    }
  } catch (err) {
    const msg = err.response?.data?.detail || '登录失败'
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}

async function handleOtp() {
  if (otpCode.value.length !== 6) {
    ElMessage.warning('请输入 6 位验证码')
    return
  }
  loading.value = true
  try {
    const res = await authApi.otpVerify(tempToken.value, otpCode.value)
    const data = res.data
    if (data.error) {
      ElMessage.error(data.error)
    } else if (data.token) {
      localStorage.setItem('token', data.token)
      ElMessage.success('登录成功')
      router.replace('/')
    }
  } catch (err) {
    const msg = err.response?.data?.detail || '验证失败'
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card {
  width: 400px;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
}
.login-header {
  text-align: center;
}
.login-header h2 {
  margin: 12px 0 0;
  color: #303133;
  font-size: 20px;
}
.otp-step .otp-hint {
  margin-bottom: 0;
}
</style>
