<template>
  <div class="dashboard">
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>总转发数</template>
          <div class="stat-value">{{ stats.total }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>成功</template>
          <div class="stat-value" style="color: #67c23a">{{ stats.success }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>失败</template>
          <div class="stat-value" style="color: #f56c6c">{{ stats.failed }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>跳过(规则过滤)</template>
          <div class="stat-value" style="color: #909399">{{ stats.skipped }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>系统状态</template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="TG API 已配置">
              <el-tag :type="info.tg_configured ? 'success' : 'danger'">
                {{ info.tg_configured ? '是' : '否' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="飞书已配置">
              <el-tag :type="info.feishu_configured ? 'success' : 'danger'">
                {{ info.feishu_configured ? '是' : '否' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="代理已配置">
              <el-tag :type="info.proxy_configured ? 'success' : 'info'">
                {{ info.proxy_configured ? '是' : '否' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="TG 已连接">
              <el-tag :type="info.tg_connected ? 'success' : 'danger'">
                {{ info.tg_connected ? '是' : '否' }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>快速操作</template>
          <div class="quick-actions">
            <el-button type="primary" @click="$router.push('/telegram')">
              <el-icon><Connection /></el-icon> TG 登录/连接
            </el-button>
            <el-button type="success" @click="toggleListener">
              <el-icon><VideoPlay /></el-icon>
              {{ listenerRunning ? '停止监听' : '开始监听' }}
            </el-button>
            <el-button @click="$router.push('/channels')">
              <el-icon><ChatDotSquare /></el-icon> 管理频道
            </el-button>
            <el-button @click="$router.push('/rules')">
              <el-icon><Filter /></el-icon> 配置规则
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { logApi, systemApi, telegramApi } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const stats = ref({ total: 0, success: 0, failed: 0, skipped: 0 })
const info = ref({ tg_configured: false, feishu_configured: false, proxy_configured: false, tg_connected: false })
const listenerRunning = ref(false)

async function loadData() {
  try {
    const [s, i, l] = await Promise.all([
      logApi.stats(),
      systemApi.info(),
      telegramApi.listenerStatus(),
    ])
    stats.value = s.data
    info.value = i.data
    listenerRunning.value = l.data.running
  } catch (e) {
    ElMessage.error('加载数据失败')
  }
}

async function toggleListener() {
  try {
    if (listenerRunning.value) {
      await telegramApi.stopListener()
      ElMessage.success('监听已停止')
    } else {
      await telegramApi.startListener()
      ElMessage.success('监听已启动')
    }
    loadData()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

onMounted(loadData)
</script>

<style scoped>
.stat-value {
  font-size: 36px;
  font-weight: 700;
  text-align: center;
  padding: 10px 0;
}
.quick-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.quick-actions .el-button {
  margin: 0;
}
</style>
