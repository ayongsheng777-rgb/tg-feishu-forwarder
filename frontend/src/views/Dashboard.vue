<template>
  <div class="dashboard">
    <div class="bento-grid">
      <!-- 监听控制大卡片 -->
      <div class="bento-card span-4 listener-card" :class="listenerCardClass">
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

      <!-- 统计卡片：鲜果篮（当前时段） -->
      <div class="bento-card span-2 stat-cell accent-muscat">
        <div class="bento-title">{{ periodLabel }}总转发</div>
        <div class="stat-big" :class="digitsClass(currentStats.total)" style="--c1:#2d6a4f;--c2:#8fc97f">{{ currentStats.total }}</div>
        <div class="stat-sub">{{ periodSub }}</div>
      </div>
      <div class="bento-card span-2 stat-cell accent-spring">
        <div class="bento-title">成功</div>
        <div class="stat-big" :class="digitsClass(currentStats.success)" style="--c1:#2f6d8c;--c2:#6cb4d6">{{ currentStats.success }}</div>
        <div class="stat-sub">山泉蓝 · 顺利送达</div>
      </div>
      <div class="bento-card span-2 stat-cell accent-peach">
        <div class="bento-title">失败</div>
        <div class="stat-big" :class="digitsClass(currentStats.failed)" style="--c1:#c0392b;--c2:#f49bab">{{ currentStats.failed }}</div>
        <div class="stat-sub">蜜桃粉 · 需要关注</div>
      </div>
      <div class="bento-card span-2 stat-cell accent-citrus">
        <div class="bento-title">跳过</div>
        <div class="stat-big" :class="digitsClass(currentStats.skipped)" style="--c1:#9a6a10;--c2:#f2c14e">{{ currentStats.skipped }}</div>
        <div class="stat-sub">柑橘黄 · 规则过滤</div>
      </div>

      <!-- 群消息转发统计（按飞书群分组，可展开频道明细） -->
      <div class="bento-card span-12 accent-berry">
        <div class="panel-header">
          <div class="bento-title">
            <el-icon><DataAnalysis /></el-icon> 群消息转发统计
          </div>
          <el-radio-group v-model="activePeriod" size="small">
            <el-radio-button value="today">今日</el-radio-button>
            <el-radio-button value="this_week">本周</el-radio-button>
            <el-radio-button value="this_month">本月</el-radio-button>
            <el-radio-button value="all_time">累计</el-radio-button>
          </el-radio-group>
        </div>

        <!-- 群列表 -->
        <div v-if="groupRows.length === 0" class="empty-hint">暂无转发群，请先在「频道管理」中配置频道及目标飞书群</div>
        <div v-else class="group-list">
          <div
            v-for="group in groupRows"
            :key="group.feishu_chat_id"
            class="group-card"
          >
            <!-- 群头部 -->
            <div
              class="group-header"
              :class="{ clickable: group.channel_count > 0 }"
              @click="group.channel_count > 0 && toggleGroup(group.feishu_chat_id)"
            >
              <div class="group-info">
                <el-icon
                  v-if="group.channel_count > 0"
                  class="expand-icon"
                  :class="{ expanded: expandedGroups.has(group.feishu_chat_id) }"
                >
                  <ArrowRight />
                </el-icon>
                <span v-else class="expand-icon placeholder"></span>
                <span class="group-name">{{ group.group_name }}</span>
                <el-tag size="small" effect="plain" round>{{ group.channel_count }} 个频道</el-tag>
              </div>
              <div class="group-stats">
                <div class="gs-item">
                  <span class="gs-num">{{ group.total }}</span>
                  <span class="gs-label">转发</span>
                </div>
                <div class="gs-divider"></div>
                <div class="gs-item ok">
                  <span class="gs-num">{{ group.success }}</span>
                  <span class="gs-label">成功</span>
                </div>
                <div class="gs-divider"></div>
                <div class="gs-item" :class="{ err: group.failed > 0 }">
                  <span class="gs-num">{{ group.failed }}</span>
                  <span class="gs-label">失败</span>
                </div>
                <div class="gs-divider"></div>
                <div class="gs-item skip">
                  <span class="gs-num">{{ group.skipped }}</span>
                  <span class="gs-label">跳过</span>
                </div>
                <div class="gs-divider"></div>
                <div class="gs-rate">
                  <el-progress
                    :percentage="group.successRate"
                    :color="rateColor(group.successRate)"
                    :stroke-width="6"
                    :show-text="false"
                    style="width:60px"
                  />
                  <span class="rate-pct">{{ group.successRate }}%</span>
                </div>
              </div>
            </div>

            <!-- 频道明细（可展开，仅在有频道时显示） -->
            <div v-if="group.channel_count > 0 && expandedGroups.has(group.feishu_chat_id)" class="channel-rows">
              <div class="channel-row header-row">
                <span class="cr-name">频道名称</span>
                <span class="cr-stat">转发</span>
                <span class="cr-stat">成功</span>
                <span class="cr-stat">失败</span>
                <span class="cr-stat">跳过</span>
                <span class="cr-rate-col">成功率</span>
              </div>
              <div
                v-for="ch in group.channels"
                :key="ch.id"
                class="channel-row"
              >
                <span class="cr-name">
                  <span class="ch-dot" :class="ch.enabled ? 'on' : 'off'"></span>
                  {{ ch.channel_title }}
                </span>
                <span class="cr-stat">{{ ch.total }}</span>
                <span class="cr-stat ok">{{ ch.success }}</span>
                <span class="cr-stat" :class="{ err: ch.failed > 0 }">{{ ch.failed }}</span>
                <span class="cr-stat skip">{{ ch.skipped }}</span>
                <span class="cr-rate-col">
                  <el-progress
                    :percentage="ch.successRate"
                    :color="rateColor(ch.successRate)"
                    :stroke-width="5"
                    :show-text="false"
                    style="width:40px"
                  />
                  <span class="rate-pct-sm">{{ ch.successRate }}%</span>
                </span>
              </div>
            </div>
          </div>
        </div>

        <div class="panel-footer">
          共 <strong>{{ groupRows.length }}</strong> 个转发群
          · {{ activePeriod === 'all_time' ? '历史累计' : '本时段' }}合计转发 <strong>{{ groupTotal }}</strong> 条
        </div>
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
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { statsApi, systemApi, telegramApi } from '../api'
import { ElMessage } from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'

// ---- period ----
const activePeriod = ref('today')
const periodLabels = { today: '今日', this_week: '本周', this_month: '本月', all_time: '累计' }
const periodSubs = { today: '今日 00:00 起', this_week: '本周一 00:00 起', this_month: '本月 1 日 00:00 起', all_time: '历史累计' }
const periodLabel = computed(() => periodLabels[activePeriod.value])
const periodSub = computed(() => periodSubs[activePeriod.value])

// ---- raw data ----
const dashboardData = ref({ overall: {}, by_period: {}, groups: [] })
const info = ref({ tg_configured: false, feishu_configured: false, proxy_configured: false, tg_connected: false })
const listenerRunning = ref(false)
const listenerToggling = ref(false)
const reportSending = ref(false)

// ---- expand / collapse ----
const expandedGroups = reactive(new Set())

function toggleGroup(fid) {
  if (expandedGroups.has(fid)) {
    expandedGroups.delete(fid)
  } else {
    expandedGroups.add(fid)
  }
}

function rateColor(pct) {
  if (pct >= 90) return '#8fc97f'
  if (pct >= 70) return '#f2c14e'
  return '#f49bab'
}

// ---- current-period overall stats ----
// 大数字按位数加类名（d1/d2/…/d7），CSS 据此选择字号缩放系数，
// 位数变多时字号自动变小，不会被卡片裁掉（见 styles/theme.css 的 .stat-big）
const digitsClass = (v) => 'd' + String(v ?? 0).length

const currentStats = computed(() => {
  const dp = dashboardData.value.by_period || {}
  if (activePeriod.value === 'all_time') {
    return dashboardData.value.overall || { total: 0, success: 0, failed: 0, skipped: 0 }
  }
  return dp[activePeriod.value] || { total: 0, success: 0, failed: 0, skipped: 0 }
})

// ---- group rows for current period ----
const groupRows = computed(() => {
  const groups = dashboardData.value.groups || []
  return groups.map(g => {
    const stats = activePeriod.value === 'all_time'
      ? (g.all_time || {})
      : (g[activePeriod.value] || {})
    const total = stats.total || 0
    const success = stats.success || 0
    const failed = stats.failed || 0
    const skipped = stats.skipped || 0
    const successRate = total > 0 ? Math.round((success / total) * 100) : 100

    // enrich channels with current-period stats
    const channels = (g.channels || []).map(ch => {
      const chStats = activePeriod.value === 'all_time'
        ? (ch.all_time || {})
        : (ch[activePeriod.value] || {})
      const chTotal = chStats.total || 0
      const chSuccess = chStats.success || 0
      return {
        ...ch,
        total: chTotal,
        success: chSuccess,
        failed: chStats.failed || 0,
        skipped: chStats.skipped || 0,
        successRate: chTotal > 0 ? Math.round((chSuccess / chTotal) * 100) : 100,
      }
    })

    return {
      feishu_chat_id: g.feishu_chat_id,
      group_name: g.group_name,
      channel_count: g.channel_count,
      total,
      success,
      failed,
      skipped,
      successRate,
      channels,
    }
  })
})

const groupTotal = computed(() => groupRows.value.reduce((s, g) => s + g.total, 0))

// ---- listener ----
const listenerCardClass = computed(() =>
  listenerRunning.value ? 'accent-muscat' : 'accent-peach'
)

// 防止轮询堆积：上一轮还没回来时直接跳过本次触发
let inflight = false

async function loadData(silent = false) {
  if (inflight) return
  inflight = true
  try {
    const [d, i, l] = await Promise.all([
      statsApi.dashboard(),
      systemApi.info(),
      telegramApi.listenerStatus(),
    ])
    dashboardData.value = d.data
    info.value = i.data
    if (!listenerToggling.value) {
      listenerRunning.value = l.data.running
    }
  } catch (e) {
    if (!silent) ElMessage.error('加载数据失败')
  } finally {
    inflight = false
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
    listenerRunning.value = !val
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
  // 统计是聚合查询，30s 刷新足够；太密只会白跑（旧值 10s）
  timer = setInterval(() => loadData(true), 30000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
/* ---- existing ---- */
.stat-cell { text-align: left; }

/* 顶部卡片的响应式排布 —— 保证每一行都刚好占满 12 列，不留空缺，且数字保持可读。
 *
 * 基准（>1200px）：监听卡 4 列 + 4 张统计卡各 2 列 = 12，正好一行。
 * 但全局 .span-* 工具类在 ≤1200px 会把 .span-4 改成 6 列，于是排成
 * 6+2+2+2=12 后第 4 张统计卡被挤到下一行，右侧空出 10 列（很明显的断裂）。
 * 这里用 scoped 选择器覆盖（带 [data-v-*]，优先级高于全局 .span-*），按宽度分四档：
 *
 *   >1200px    监听 4 + 统计 2×4      → 一行 5 张
 *   ≤1200px    监听 12 + 统计 3×4     → 两行，各占满
 *   ≤900px     监听 12 + 统计 6×4     → 两行 + 2×2
 *
 * ≤768px 时侧边栏变成抽屉（见 layout/MainLayout.vue），内容区回到整屏宽，
 * 2×2 的统计卡在最窄的 360px 视口下仍有 117px 内容宽（4 位数约占 87%），
 * 所以**不再需要单列档**。900px 这个分界是按「4 位数还能不能保持可读」
 * 算出来的：统计卡内容宽 ≈ 视口/4 − 130，900px 时约 95px，4 位数刚好还有
 * ~31px 字号；再窄就该换成 2×2 让字号回到满格 40px。
 * 数字本身的缩放见 styles/theme.css 的 .stat-big。
 */
@media (max-width: 1200px) {
  .listener-card { grid-column: span 12; }
  .stat-cell { grid-column: span 3; }
}
@media (max-width: 900px) {
  .stat-cell { grid-column: span 6; }
}
.listener-body { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.listener-state { display: flex; align-items: center; gap: 10px; }
.listener-label { font-size: 26px; font-weight: 800; color: #33414e; }
.status-rows { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.status-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-radius: 12px; background: rgba(255, 255, 255, 0.5); font-size: 13px; color: #4a5a66; }
.quick-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.quick-actions .el-button { margin: 0; }

/* ---- panel header (title + period tabs) ---- */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.panel-header .bento-title { margin-bottom: 0; }

/* ---- group cards ---- */
.empty-hint {
  text-align: center;
  color: var(--ink-soft);
  padding: 32px 0;
  font-size: 14px;
}
.group-list {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.group-card {
  border: 1px solid rgba(51, 65, 78, 0.07);
  border-radius: 14px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.45);
  transition: box-shadow 0.2s;
}
.group-card:hover {
  box-shadow: 0 4px 16px rgba(51, 65, 78, 0.07);
}

/* group header row */
.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  user-select: none;
  gap: 16px;
  flex-wrap: wrap;
}
.group-header.clickable { cursor: pointer; }
.group-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.expand-icon {
  color: var(--ink-soft);
  transition: transform 0.25s;
  flex-shrink: 0;
}
.expand-icon.expanded { transform: rotate(90deg); }
.expand-icon.placeholder { width: 16px; flex-shrink: 0; }
.group-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* inline stats bar */
.group-stats {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.gs-item {
  text-align: center;
  min-width: 44px;
}
.gs-num {
  display: block;
  font-size: 18px;
  font-weight: 700;
  color: var(--ink);
  line-height: 1.2;
}
.gs-label {
  font-size: 11px;
  color: var(--ink-soft);
}
.gs-item.ok .gs-num { color: #2d6a4f; }
.gs-item.err .gs-num { color: #c0392b; }
.gs-item.skip .gs-num { color: #9a6a10; }
.gs-divider {
  width: 1px;
  height: 28px;
  background: rgba(51, 65, 78, 0.10);
}
.gs-rate {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
}
.rate-pct {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-soft);
}

/* ---- channel rows (expanded) ---- */
.channel-rows {
  border-top: 1px solid rgba(51, 65, 78, 0.06);
  padding: 4px 0;
}
.channel-row {
  display: flex;
  align-items: center;
  padding: 8px 18px 8px 40px;
  gap: 8px;
  font-size: 13px;
}
.channel-row.header-row {
  font-size: 11px;
  color: var(--ink-soft);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  border-bottom: 1px solid rgba(51, 65, 78, 0.04);
}
.cr-name {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cr-stat {
  width: 52px;
  text-align: center;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.cr-stat.ok { color: #2d6a4f; }
.cr-stat.err { color: #c0392b; font-weight: 600; }
.cr-stat.skip { color: #9a6a10; }
.cr-rate-col {
  width: 80px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.rate-pct-sm { font-size: 11px; color: var(--ink-soft); }

.ch-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.ch-dot.on { background: var(--muscat-green); }
.ch-dot.off { background: #b0b8bf; }

/* ---- footer ---- */
.panel-footer {
  margin-top: 14px;
  text-align: right;
  font-size: 12px;
  color: var(--ink-soft);
  border-top: 1px solid rgba(51, 65, 78, 0.06);
  padding-top: 12px;
}
.panel-footer strong { color: var(--ink); }

/* ---- 窄屏适配：群统计行改为整行铺开 ----
 * .group-stats 是 flex-shrink:0 的定宽条（4 个数字 + 3 条分隔线 + 进度条约 332px），
 * 卡片在 ≤700px 时内容区不足 332px，而 .bento-card 是 overflow:hidden，
 * 于是右侧的成功率整块被裁掉。这里让它换到第二行、并压缩不必要装饰。
 */
@media (max-width: 768px) {
  .group-header { flex-direction: column; align-items: stretch; gap: 10px; }
  .group-info { width: 100%; }
  /* 用 flex-wrap 而不是压缩：宁可换行，也不让数字互相重叠 */
  .group-stats { width: 100%; flex-wrap: wrap; justify-content: flex-start; gap: 8px 10px; flex-shrink: 1; }
  .gs-divider { display: none; }
  .gs-item { flex: 0 0 auto; min-width: 40px; }
  .gs-rate { flex: 0 0 auto; margin-left: 0; }
  .gs-rate .el-progress { display: none; }   /* 窄屏只保留百分比数字 */
  .channel-row { padding-left: 18px; }
  .cr-stat { width: 42px; }
  .cr-rate-col { width: 58px; }
}
</style>
