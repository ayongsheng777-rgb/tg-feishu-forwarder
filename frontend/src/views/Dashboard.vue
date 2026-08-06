<template>
  <div class="dashboard">
    <div class="bento-grid">
      <!-- 监听控制大卡片：默认自动开启，点击即可关闭 -->
      <div class="bento-card span-4" :class="listenerCardClass">
        <div class="bento-title">
          <el-icon><Radar /></el-icon> 消息监听
        </div>
        <div class="listener-body">
          <div class="listener-state">
            <span class="status-dot" :class="listenerRunning ? 'on' : 'off'"></span>
            <span class="listener-label">{{ listenerRunning ? '运行中' : '已停止' }}</span>
          </div>
          <el-switch
            v-model="listenerRunning"
            size="large"
            :loading="listenerToggling"
            inline-prompt
            active-text="开"
            inactive-text="关"
            @change="toggleListener"
          />
        </div>
        <div class="stat-sub">服务启动后自动开启 · 点击开关即可关闭</div>
        <div v-if="listenerToggling" class="juice-bar" style="margin-top: 12px"><div></div></div>
      </div>

      <!-- 统计卡片：鲜果篮 -->
      <div class="bento-card span-2 stat-cell accent-muscat">
        <div class="bento-title">总转发</div>
        <div class="stat-big" style="--c1:#2d6a4f;--c2:#8fc97f">{{ stats.total }}</div>
        <div class="stat-sub">历史累计</div>
      </div>
      <div class="bento-card span-2 stat-cell accent-spring">
        <div class="bento-title">成功</div>
        <div class="stat-big" style="--c1:#2f6d8c;--c2:#6cb4d6">{{ stats.success }}</div>
        <div class="stat-sub">山泉蓝 · 顺利送达</div>
      </div>
      <div class="bento-card span-2 stat-cell accent-peach">
        <div class="bento-title">失败</div>
        <div class="stat-big" style="--c1:#c0392b;--c2:#f49bab">{{ stats.failed }}</div>
        <div class="stat-sub">蜜桃粉 · 需要关注</div>
      </div>
      <div class="bento-card span-2 stat-cell accent-citrus">
        <div class="bento-title">跳过</div>
        <div class="stat-big" style="--c1:#9a6a10;--c2:#f2c14e">{{ stats.skipped }}</div>
        <div class="stat-sub">柑橘黄 · 规则过滤</div>
      </div>

      <!-- 系统状态 -->
      <div class="bento-card span-6 accent-berry">
        <div class="bento-title">
          <el-icon><Monitor /></el-icon> 系统状态
        </div>
        <div class="status-rows">
          <div class="status-row">
            <span>TG API 配置</span>
            <el-tag :type="info.tg_configured ? 'success' : 'danger'" size="small">
              {{ info.tg_configured ? '已配置' : '未配置' }}
            </el-tag>
          </div>
          <div class="status-row">
            <span>飞书应用</span>
            <el-tag :type="info.feishu_configured ? 'success' : 'danger'" size="small">
              {{ info.feishu_configured ? '已配置' : '未配置' }}
            </el-tag>
          </div>
          <div class="status-row">
            <span>代理</span>
            <el-tag :type="info.proxy_configured ? 'success' : 'info'" size="small">
              {{ info.proxy_configured ? '已配置' : '未配置' }}
            </el-tag>
          </div>
          <div class="status-row">
            <span>TG 连接</span>
            <el-tag :type="info.tg_connected ? 'success' : 'danger'" size="small">
              {{ info.tg_connected ? '已连接' : '未连接' }}
            </el-tag>
          </div>
        </div>
      </div>

      <!-- 快捷操作 -->
      <div class="bento-card span-6 accent-muscat">
        <div class="bento-title">
          <el-icon><MagicStick /></el-icon> 快捷操作
        </div>
        <div class="quick-actions">
          <el-button type="primary" @click="$router.push('/telegram')">
            <el-icon><Connection /></el-icon> TG 登录/连接
          </el-button>
          <el-button @click="$router.push('/channels')">
            <el-icon><ChatDotSquare /></el-icon> 管理频道
          </el-button>
          <el-button @click="$router.push('/aliases')">
            <el-icon><Link /></el-icon> 群绑定
          </el-button>
          <el-button @click="$router.push('/rules')">
            <el-icon><Filter /></el-icon> 配置规则
          </el-button>
          <el-button :loading="reportSending" @click="sendReport">
            <el-icon><Bell /></el-icon> 发送状态报告
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { logApi, systemApi, telegramApi } from '../api'
import { ElMessage } from 'element-plus'

const stats = ref({ total: 0, success: 0, failed: 0, skipped: 0 })
const info = ref({ tg_configured: false, feishu_configured: false, proxy_configured: false, tg_connected: false })
const listenerRunning = ref(false)
const listenerToggling = ref(false)
const reportSending = ref(false)

// 监听卡片状态感知：运行=青提绿 / 停止=落日橘
const listenerCardClass = computed(() =>
  listenerRunning.value ? 'accent-muscat' : 'accent-peach'
)

async function loadData(silent = false) {
  try {
    const [s, i, l] = await Promise.all([
      logApi.stats(),
      systemApi.info(),
      telegramApi.listenerStatus(),
    ])
    stats.value = s.data
    info.value = i.data
    if (!listenerToggling.value) {
      listenerRunning.value = l.data.running
    }
  } catch (e) {
    if (!silent) ElMessage.error('加载数据失败')
  }
}

async function toggleListener(val) {
  listenerToggling.value = true
  try {
    if (val) {
      await telegramApi.startListener()
      ElMessage.success('监听已启动')
    } else {
      await telegramApi.stopListener()
      ElMessage.success('监听已停止')
    }
  } catch (e) {
    listenerRunning.value = !val  // revert
    ElMessage.error(e.response?.data?.detail || '操作失败')
  } finally {
    listenerToggling.value = false
    loadData(true)
  }
}

async function sendReport() {
  reportSending.value = true
  try {
    const res = await systemApi.reportSend()
    if (res.data.success) {
      ElMessage.success('状态报告已发送到指定飞书群')
    } else {
      ElMessage.warning(res.data.message || '发送失败：请先在系统设置中配置报告目标群')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  } finally {
    reportSending.value = false
  }
}

let timer = null
onMounted(() => {
  loadData()
  timer = setInterval(() => loadData(true), 10000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.stat-cell {
  text-align: left;
}
.listener-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.listener-state {
  display: flex;
  align-items: center;
  gap: 10px;
}
.listener-label {
  font-size: 26px;
  font-weight: 800;
  color: #33414e;
}
.status-rows {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.5);
  font-size: 13px;
  color: #4a5a66;
}
.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.quick-actions .el-button {
  margin: 0;
}
</style>
