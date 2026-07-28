<template>
  <div class="telegram-login">
    <el-row :gutter="20">
      <!-- TG API Credentials -->
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>Telegram API 凭证</span>
              <el-tag :type="apiConfigured ? 'success' : 'danger'" size="small">
                {{ apiConfigured ? '已配置' : '未配置' }}
              </el-tag>
            </div>
          </template>
          <el-form label-width="100px">
            <el-form-item label="API ID">
              <el-input v-model="apiForm.api_id" placeholder="从 my.telegram.org 获取" type="number" />
            </el-form-item>
            <el-form-item label="API Hash">
              <el-input v-model="apiForm.api_hash" placeholder="从 my.telegram.org 获取" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveApiCredentials">保存并应用</el-button>
            </el-form-item>
          </el-form>
          <el-alert type="info" :closable="false" show-icon>
            请前往 https://my.telegram.org → API development tools 获取 API ID 和 Hash
          </el-alert>
        </el-card>
      </el-col>

      <!-- TG Session Status -->
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>Telegram 会话状态</span>
              <el-tag :type="status.authorized ? 'success' : 'danger'" size="small">
                {{ status.authorized ? '已登录' : '未登录' }}
              </el-tag>
            </div>
          </template>

          <div v-if="status.user" class="user-info">
            <el-descriptions :column="1" border>
              <el-descriptions-item label="用户ID">{{ status.user.id }}</el-descriptions-item>
              <el-descriptions-item label="昵称">{{ status.user.first_name }}</el-descriptions-item>
              <el-descriptions-item label="用户名">{{ status.user.username || '无' }}</el-descriptions-item>
              <el-descriptions-item label="手机号">{{ status.user.phone }}</el-descriptions-item>
            </el-descriptions>
            <el-button type="danger" style="margin-top: 15px" @click="logout">退出登录</el-button>
          </div>

          <div v-else>
            <el-form label-width="100px">
              <el-form-item label="手机号">
                <el-input v-model="loginForm.phone" placeholder="+8613800138000" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="sendCode" :loading="sending">发送验证码</el-button>
              </el-form-item>
            </el-form>

            <el-divider v-if="codeSent" />

            <el-form v-if="codeSent" label-width="100px">
              <el-form-item label="验证码">
                <el-input v-model="loginForm.code" placeholder="收到的验证码" />
              </el-form-item>
              <el-form-item label="两步密码">
                <el-input v-model="loginForm.password" placeholder="如未设置两步验证则留空" show-password />
              </el-form-item>
              <el-form-item>
                <el-button type="success" @click="completeLogin" :loading="logging">完成登录</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Listener Control -->
    <el-card shadow="hover" style="margin-top: 20px">
      <template #header>
        <div class="card-header">
          <span>消息监听控制</span>
          <el-tag :type="listenerRunning ? 'success' : 'info'" size="small">
            {{ listenerRunning ? '运行中' : '已停止' }}
          </el-tag>
        </div>
      </template>
      <div class="listener-control">
        <el-button type="success" @click="startListener" :disabled="listenerRunning">
          <el-icon><VideoPlay /></el-icon> 启动监听
        </el-button>
        <el-button type="danger" @click="stopListener" :disabled="!listenerRunning">
          <el-icon><VideoPause /></el-icon> 停止监听
        </el-button>
        <el-alert
          v-if="!status.authorized"
          type="warning"
          :closable="false"
          show-icon
          style="margin-top: 15px"
        >
          请先完成 Telegram 登录，然后配置频道后才能启动监听
        </el-alert>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { telegramApi, systemApi } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const apiForm = ref({ api_id: '', api_hash: '' })
const apiConfigured = ref(false)
const status = ref({ authorized: false, connected: false, user: null })
const loginForm = ref({ phone: '', code: '', password: '' })
const codeSent = ref(false)
const sending = ref(false)
const logging = ref(false)
const listenerRunning = ref(false)

async function loadStatus() {
  try {
    const [s, info, l] = await Promise.all([
      telegramApi.status(),
      systemApi.info(),
      telegramApi.listenerStatus(),
    ])
    status.value = s.data
    apiConfigured.value = info.data.tg_configured
    listenerRunning.value = l.data.running

    // Pre-fill API ID from env if available
    if (!apiForm.value.api_id) {
      const env = await systemApi.env()
      if (env.data.TG_API_ID) {
        apiForm.value.api_id = env.data.TG_API_ID
        apiForm.value.api_hash = env.data.TG_API_HASH ? '********' : ''
      }
    }
  } catch {
    // ignore
  }
}

async function saveApiCredentials() {
  if (!apiForm.value.api_id || !apiForm.value.api_hash) {
    ElMessage.warning('请填写 API ID 和 API Hash')
    return
  }
  // If hash is masked, don't send it
  const hash = apiForm.value.api_hash === '********' ? '' : apiForm.value.api_hash
  if (!hash) {
    ElMessage.warning('请填写完整的 API Hash')
    return
  }
  try {
    await telegramApi.setApiCredentials(parseInt(apiForm.value.api_id), hash)
    ElMessage.success('API 凭证已保存')
    loadStatus()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}

async function sendCode() {
  if (!loginForm.value.phone) {
    ElMessage.warning('请输入手机号')
    return
  }
  sending.value = true
  try {
    await telegramApi.loginStart(loginForm.value.phone)
    codeSent.value = true
    ElMessage.success('验证码已发送')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  } finally {
    sending.value = false
  }
}

async function completeLogin() {
  logging.value = true
  try {
    const res = await telegramApi.loginComplete(
      loginForm.value.phone,
      loginForm.value.code,
      loginForm.value.password
    )
    if (res.data.success) {
      ElMessage.success('登录成功')
      codeSent.value = false
      loginForm.value = { phone: '', code: '', password: '' }
      loadStatus()
    } else {
      ElMessage.error(res.data.message || '登录失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    logging.value = false
  }
}

async function logout() {
  try {
    await ElMessageBox.confirm('确定要退出 Telegram 登录吗？', '提示', { type: 'warning' })
    await telegramApi.logout()
    ElMessage.success('已退出登录')
    loadStatus()
  } catch {
    // cancelled
  }
}

async function startListener() {
  try {
    await telegramApi.startListener()
    ElMessage.success('监听已启动')
    loadStatus()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  }
}

async function stopListener() {
  try {
    await telegramApi.stopListener()
    ElMessage.success('监听已停止')
    loadStatus()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '停止失败')
  }
}

onMounted(loadStatus)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.user-info {
  text-align: left;
}
.listener-control {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
