<template>
  <div class="logs-page">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>转发日志</span>
          <div class="filters">
            <el-select v-model="filterStatus" placeholder="全部状态" clearable style="width: 120px" @change="loadLogs">
              <el-option label="成功" value="success" />
              <el-option label="失败" value="failed" />
              <el-option label="跳过" value="skipped" />
            </el-select>
            <el-button @click="loadLogs">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
            <el-button type="warning" @click="retryAllFailed" :loading="retrying" v-if="stats.failed > 0">
              <el-icon><RefreshRight /></el-icon> 重试全部失败 ({{ stats.failed }})
            </el-button>
          </div>
        </div>
      </template>

      <!-- Stats Summary -->
      <el-row :gutter="12" style="margin-bottom: 15px">
        <el-col :span="6">
          <el-statistic title="总计" :value="stats.total" />
        </el-col>
        <el-col :span="6">
          <el-statistic title="成功" :value="stats.success" />
        </el-col>
        <el-col :span="6">
          <el-statistic title="失败" :value="stats.failed" />
        </el-col>
        <el-col :span="6">
          <el-statistic title="跳过" :value="stats.skipped" />
        </el-col>
      </el-row>

      <el-table :data="logs" v-loading="loading" stripe max-height="600">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="channel_id" label="频道" width="80" />
        <el-table-column prop="tg_message_id" label="TG消息ID" width="100" />
        <el-table-column prop="tg_sender" label="发送者" width="120" show-overflow-tooltip />
        <el-table-column prop="media_type" label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="mediaColor(row.media_type)">{{ row.media_type || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message_text" label="消息内容" show-overflow-tooltip min-width="200" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="statusColor(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error_message" label="错误信息" show-overflow-tooltip width="200" />
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'failed'" type="primary" size="small" @click="retryOne(row.id)" :loading="row._retrying">
              重试
            </el-button>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        style="margin-top: 15px; text-align: right"
        @current-change="loadLogs"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { logApi } from '../api'
import { ElMessage } from 'element-plus'

const logs = ref([])
const stats = ref({ total: 0, success: 0, failed: 0, skipped: 0 })
const loading = ref(false)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
const filterStatus = ref('')
const retrying = ref(false)

function statusColor(s) {
  const map = { success: 'success', failed: 'danger', skipped: 'info', pending: 'warning' }
  return map[s] || ''
}

function statusLabel(s) {
  const map = { success: '成功', failed: '失败', skipped: '跳过', pending: '处理中' }
  return map[s] || s
}

function mediaColor(m) {
  const map = { text: '', photo: 'success', video: 'warning', document: 'info', sticker: 'danger' }
  return map[m] || ''
}

function formatDate(d) {
  if (!d) return '—'
  return new Date(d).toLocaleString('zh-CN')
}

async function loadLogs() {
  loading.value = true
  try {
    const params = { page: page.value, size: pageSize.value }
    if (filterStatus.value) params.status = filterStatus.value
    const res = await logApi.list(params)
    logs.value = res.data
    // Estimate total from stats
    total.value = stats.value.total
  } catch {
    ElMessage.error('加载日志失败')
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    const res = await logApi.stats()
    stats.value = res.data
    total.value = res.data.total
  } catch {
    // ignore
  }
}

onMounted(() => {
  loadStats()
  loadLogs()
})

async function retryOne(id) {
  const row = logs.value.find(r => r.id === id)
  if (row) row._retrying = true
  try {
    const res = await logApi.retry(id)
    if (res.data.success) {
      ElMessage.success('重试成功')
    } else {
      ElMessage.error(res.data.message || '重试失败')
    }
    loadStats()
    loadLogs()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重试失败')
  } finally {
    if (row) row._retrying = false
  }
}

async function retryAllFailed() {
  retrying.value = true
  const notif = ElMessage.info({ message: '正在重试全部失败转发...', duration: 0 })
  try {
    const res = await logApi.retryFailed()
    notif.close()
    const { succeeded, failed: failCount, total: totalCount } = res.data
    ElMessage.success(`重试完成: ${succeeded}/${totalCount} 成功${failCount > 0 ? `, ${failCount} 条仍失败` : ''}`)
    loadStats()
    loadLogs()
  } catch (e) {
    notif.close()
    ElMessage.error(e.response?.data?.detail || '重试失败')
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.filters {
  display: flex;
  gap: 10px;
}
</style>
