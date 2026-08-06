<template>
  <div class="settings-page">
    <el-row :gutter="20">
      <!-- Feishu Settings -->
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>飞书应用配置</span>
              <el-tag :type="feishuConfigured ? 'success' : 'danger'" size="small">
                {{ feishuConfigured ? '已配置' : '未配置' }}
              </el-tag>
            </div>
          </template>
          <el-form label-width="120px">
            <el-form-item label="App ID">
              <el-input v-model="feishuForm.app_id" placeholder="cli_xxxxxx" />
            </el-form-item>
            <el-form-item label="App Secret">
              <el-input
                v-model="feishuForm.app_secret"
                :placeholder="feishuConfigured ? '已配置（留空则不修改）' : '飞书应用密钥'"
                show-password
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveFeishu">保存</el-button>
            </el-form-item>
          </el-form>
          <el-alert type="info" :closable="false" show-icon>
            前往飞书开放平台 → 创建企业自建应用 → 获取 App ID 和 App Secret。
            需要开启「机器人」能力并添加到目标群聊。
          </el-alert>
        </el-card>
      </el-col>

      <!-- Proxy Settings -->
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>代理配置</span>
              <el-tag :type="proxyConfigured ? 'success' : 'info'" size="small">
                {{ proxyConfigured ? '已配置' : '未配置' }}
              </el-tag>
            </div>
          </template>
          <el-form label-width="120px">
            <el-form-item label="代理地址">
              <el-input v-model="proxyForm.url" placeholder="socks5://host:port 或 http://host:port" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveProxy">保存</el-button>
            </el-form-item>
          </el-form>
          <el-alert type="warning" :closable="false" show-icon>
            代理地址修改后需重启服务才能生效（Telethon 客户端在启动时读取代理）。
            如需在容器内连接宿主机代理，请使用 host.docker.internal:端口。
          </el-alert>
        </el-card>
      </el-col>
    </el-row>

    <!-- Status Report Settings -->
    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="12">
        <el-card shadow="hover" class="report-card">
          <template #header>
            <div class="card-header">
              <span>运行状态报告</span>
              <el-tag :type="reportForm.chat_id ? 'success' : 'info'" size="small">
                {{ reportForm.chat_id ? '已配置目标群' : '未配置' }}
              </el-tag>
            </div>
          </template>
          <el-form label-width="120px">
            <el-form-item label="报告目标群">
              <el-select
                v-model="reportForm.chat_id"
                placeholder="选择已绑定的群，或直接输入群ID"
                filterable
                allow-create
                default-first-option
                clearable
                style="width: 100%"
              >
                <el-option
                  v-for="a in aliasOptions"
                  :key="a.feishu_chat_id"
                  :label="`${a.name}（${a.feishu_chat_id.slice(0, 18)}...）`"
                  :value="a.feishu_chat_id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="定时推送间隔">
              <el-input-number v-model="reportForm.interval" :min="0" :max="1440" style="width: 160px" />
              <span style="margin-left: 10px; color: #6b7a88; font-size: 13px">分钟（0 = 关闭定时推送）</span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveReport">保存</el-button>
              <el-button :loading="reportPreviewing" @click="previewReport">预览报告</el-button>
              <el-button :loading="reportSending" @click="sendReportNow">立即发送</el-button>
            </el-form-item>
          </el-form>
          <el-alert type="info" :closable="false" show-icon>
            也可在飞书群直接发送 <code>tg状态</code> 获取报告；<code>tg状态 群名称</code> 可推送到指定群。
          </el-alert>
        </el-card>
      </el-col>
    </el-row>

    <!-- Env info -->
    <el-card shadow="hover" style="margin-top: 20px">
      <template #header>当前环境配置</template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="TG API ID">{{ env.TG_API_ID || '未设置' }}</el-descriptions-item>
        <el-descriptions-item label="TG API Hash">{{ env.TG_API_HASH ? '已设置' : '未设置' }}</el-descriptions-item>
        <el-descriptions-item label="飞书 App ID">{{ env.FEISHU_APP_ID || '未设置' }}</el-descriptions-item>
        <el-descriptions-item label="飞书 App Secret">{{ env.FEISHU_APP_SECRET ? '已设置' : '未设置' }}</el-descriptions-item>
        <el-descriptions-item label="代理 URL">{{ env.PROXY_URL || '未设置' }}</el-descriptions-item>
        <el-descriptions-item label="Redis URL">{{ env.REDIS_URL || '默认' }}</el-descriptions-item>
        <el-descriptions-item label="数据库 URL">{{ env.DB_URL || '默认' }}</el-descriptions-item>
        <el-descriptions-item label="媒体缓存目录">{{ env.MEDIA_CACHE_DIR || '/app/media_cache' }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
    <!-- Security / 2FA -->
    <el-card shadow="hover" style="margin-top: 20px">
      <template #header>
        <div class="card-header">
          <span>安全设置</span>
          <el-tag :type="otpEnabled ? 'success' : 'info'" size="small">
            两步验证 {{ otpEnabled ? '已启用' : '未启用' }}
          </el-tag>
        </div>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="当前用户">{{ user.username || '-' }}</el-descriptions-item>
        <el-descriptions-item label="两步验证">
          <el-tag :type="otpEnabled ? 'success' : 'warning'" size="small">
            {{ otpEnabled ? '已启用' : '未启用' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <div style="margin-top: 16px; display: flex; gap: 10px">
        <el-button
          v-if="!otpEnabled"
          type="primary"
          :icon="Key"
          @click="startEnableOtp"
        >
          启用两步验证
        </el-button>
        <el-button
          v-else
          type="danger"
          :icon="CircleClose"
          @click="showDisableOtpDialog"
        >
          关闭两步验证
        </el-button>
      </div>
    </el-card>

    <!-- OTP Enable Dialog -->
    <el-dialog
      v-model="otpDialogVisible"
      title="启用两步验证 (TOTP)"
      width="420px"
      :close-on-click-modal="false"
    >
      <div v-if="otpQrCode" class="otp-dialog-content">
        <p style="text-align: center; color: #606266; margin-bottom: 16px">
          用 Google Authenticator / Microsoft Authenticator 扫描二维码
        </p>
        <div style="text-align: center; margin-bottom: 16px">
          <img :src="otpQrCode" alt="QR Code" style="width: 200px; height: 200px; border: 1px solid #eee; border-radius: 8px" />
        </div>
        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
          <template #title>无法扫码？手动输入密钥：</template>
          <code style="font-size: 14px; word-break: break-all">{{ otpSecret }}</code>
        </el-alert>
        <el-input
          v-model="otpVerifyCode"
          placeholder="输入验证器中的 6 位验证码"
          size="large"
          maxlength="6"
          style="letter-spacing: 4px; text-align: center"
          @keyup.enter="confirmEnableOtp"
        />
      </div>
      <template #footer>
        <el-button @click="otpDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="otpLoading" @click="confirmEnableOtp">
          确认启用
        </el-button>
      </template>
    </el-dialog>

    <!-- OTP Disable Dialog -->
    <el-dialog
      v-model="disableOtpDialogVisible"
      title="关闭两步验证"
      width="380px"
      :close-on-click-modal="false"
    >
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 16px">
        关闭后登录将不再需要验证码，安全性会降低。
      </el-alert>
      <el-input
        v-model="disableOtpPassword"
        type="password"
        placeholder="请输入当前密码以确认"
        show-password
        size="large"
      />
      <template #footer>
        <el-button @click="disableOtpDialogVisible = false">取消</el-button>
        <el-button type="danger" :loading="otpLoading" @click="confirmDisableOtp">
          确认关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { systemApi, settingApi, authApi, aliasApi } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Key, CircleClose } from '@element-plus/icons-vue'

const feishuForm = ref({ app_id: '', app_secret: '' })
const proxyForm = ref({ url: '' })
const env = ref({})
const feishuConfigured = ref(false)
const proxyConfigured = ref(false)

// --- Status report ---
const reportForm = ref({ chat_id: '', interval: 0 })
const aliasOptions = ref([])
const reportSending = ref(false)
const reportPreviewing = ref(false)

// --- 2FA state ---
const user = ref({})
const otpEnabled = ref(false)
const otpDialogVisible = ref(false)
const otpQrCode = ref('')
const otpSecret = ref('')
const otpVerifyCode = ref('')
const otpLoading = ref(false)
const disableOtpDialogVisible = ref(false)
const disableOtpPassword = ref('')

async function loadAll() {
  // Load DB-stored settings for form fields
  try {
    const res = await settingApi.list()
    const dbMap = {}
    for (const item of res.data) {
      dbMap[item.key] = item.value
    }
    feishuForm.value.app_id = dbMap.feishu_app_id || ''
    feishuForm.value.app_secret = ''  // never pre-fill secret
    proxyForm.value.url = dbMap.proxy_url || ''
    feishuConfigured.value = !!(dbMap.feishu_app_id && dbMap.feishu_app_secret)
    proxyConfigured.value = !!dbMap.proxy_url
    reportForm.value.chat_id = dbMap.report_chat_id || ''
    reportForm.value.interval = parseInt(dbMap.report_interval_minutes || '0', 10) || 0
  } catch {
    // fall back to env
  }

  // Load alias options for the report target selector
  try {
    const aliasRes = await aliasApi.list()
    aliasOptions.value = aliasRes.data.filter(a => a.enabled)
  } catch {
    // ignore
  }

  // Load env info for the display card (synced from DB on backend startup)
  try {
    const envRes = await systemApi.env()
    env.value = envRes.data
  } catch {
    // ignore
  }

  // Load current user info (for 2FA status)
  try {
    const res = await authApi.me()
    if (res.data.ok) {
      user.value = res.data.user
      otpEnabled.value = res.data.user.otp_enabled
    }
  } catch {
    // ignore
  }
}

async function promptRestart(msg) {
  try {
    await ElMessageBox.confirm(
      `${msg}\n\n部分配置（如代理地址）需要重启服务才能完全生效，是否立即重启？`,
      '建议重启',
      {
        confirmButtonText: '立即重启',
        cancelButtonText: '稍后手动重启',
        type: 'warning',
        distinguishCancelAndClose: true,
      }
    )
    // user clicked "立即重启"
    await doRestart()
  } catch (action) {
    // user chose "稍后" or closed — they can use the top-right button later
    if (action === 'cancel') {
      ElMessage.info('可随时点击右上角「重启服务」按钮')
    }
  }
}

async function doRestart() {
  const notif = ElMessage.success({ message: '正在重启...', duration: 0 })
  try {
    await systemApi.restart()
  } catch {
    // expected
  }
  // poll health
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000))
    try {
      await systemApi.health()
      notif.close()
      ElMessage.success('服务已重启完成')
      loadAll()
      return
    } catch {
      // still down
    }
  }
  notif.close()
  ElMessage.error('重启超时，请检查容器状态')
}

async function saveFeishu() {
  try {
    const payload = { 'feishu_app_id': feishuForm.value.app_id }
    // Only update secret if the user actually typed something
    if (feishuForm.value.app_secret) {
      payload['feishu_app_secret'] = feishuForm.value.app_secret
    }
    await settingApi.bulk(payload)
    await promptRestart('飞书配置已保存')
  } catch {
    ElMessage.error('保存失败')
  }
}

async function saveProxy() {
  try {
    await settingApi.update('proxy_url', proxyForm.value.url)
    await promptRestart('代理配置已保存')
  } catch {
    ElMessage.error('保存失败')
  }
}

// --- Status report functions ---
async function saveReport() {
  try {
    await settingApi.bulk({
      report_chat_id: reportForm.value.chat_id || '',
      report_interval_minutes: String(reportForm.value.interval || 0),
    })
    ElMessage.success('报告配置已保存（定时推送即时生效，无需重启）')
  } catch {
    ElMessage.error('保存失败')
  }
}

async function previewReport() {
  reportPreviewing.value = true
  try {
    const res = await systemApi.reportPreview()
    await ElMessageBox.alert(
      res.data.text || '（空报告）',
      '状态报告预览',
      { confirmButtonText: '关闭' }
    )
  } catch {
    ElMessage.error('生成预览失败')
  } finally {
    reportPreviewing.value = false
  }
}

async function sendReportNow() {
  if (!reportForm.value.chat_id) {
    ElMessage.warning('请先选择报告目标群')
    return
  }
  reportSending.value = true
  try {
    await saveReport()
    const res = await systemApi.reportSend(reportForm.value.chat_id)
    if (res.data.success) {
      ElMessage.success('状态报告已发送')
    } else {
      ElMessage.error(res.data.message || '发送失败')
    }
  } catch {
    ElMessage.error('发送失败')
  } finally {
    reportSending.value = false
  }
}

// --- 2FA functions ---
async function startEnableOtp() {
  otpVerifyCode.value = ''
  otpDialogVisible.value = true
  try {
    const res = await authApi.setupOtp()
    if (res.data.ok) {
      otpSecret.value = res.data.secret
      otpQrCode.value = res.data.qr_code
    } else {
      ElMessage.error(res.data.error || '生成失败')
      otpDialogVisible.value = false
    }
  } catch {
    ElMessage.error('生成二维码失败')
    otpDialogVisible.value = false
  }
}

async function confirmEnableOtp() {
  if (otpVerifyCode.value.length !== 6) {
    ElMessage.warning('请输入 6 位验证码')
    return
  }
  otpLoading.value = true
  try {
    const res = await authApi.enableOtp(otpVerifyCode.value)
    if (res.data.ok) {
      otpEnabled.value = true
      otpDialogVisible.value = false
      ElMessage.success('两步验证已启用')
      loadAll()
    } else {
      ElMessage.error(res.data.error || '验证码不正确')
    }
  } catch {
    ElMessage.error('验证失败，请重试')
  } finally {
    otpLoading.value = false
  }
}

function showDisableOtpDialog() {
  disableOtpPassword.value = ''
  disableOtpDialogVisible.value = true
}

async function confirmDisableOtp() {
  if (!disableOtpPassword.value) {
    ElMessage.warning('请输入密码')
    return
  }
  otpLoading.value = true
  try {
    const res = await authApi.disableOtp(disableOtpPassword.value)
    if (res.data.ok) {
      otpEnabled.value = false
      disableOtpDialogVisible.value = false
      ElMessage.success('两步验证已关闭')
      loadAll()
    } else {
      ElMessage.error(res.data.error || '关闭失败')
    }
  } catch {
    ElMessage.error('操作失败')
  } finally {
    otpLoading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
