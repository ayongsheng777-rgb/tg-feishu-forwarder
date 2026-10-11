<template>
  <el-container class="app-layout">
    <el-aside width="232px" class="sidebar" :class="{ 'drawer-open': drawerOpen }">
      <div class="logo">
        <img src="/qingniao.png" alt="青鸟" class="logo-img" />
        <div class="logo-text">
          <span class="logo-title">青鸟</span>
          <span class="logo-sub">TG → 飞书转发</span>
        </div>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        class="sidebar-menu"
      >
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/telegram">
          <el-icon><Connection /></el-icon>
          <span>TG 登录</span>
        </el-menu-item>
        <el-menu-item index="/channels">
          <el-icon><ChatDotSquare /></el-icon>
          <span>频道管理</span>
        </el-menu-item>
        <el-menu-item index="/aliases">
          <el-icon><Link /></el-icon>
          <span>群绑定</span>
        </el-menu-item>
        <el-menu-item index="/rules">
          <el-icon><Filter /></el-icon>
          <span>转发规则</span>
        </el-menu-item>
        <el-menu-item index="/logs">
          <el-icon><Document /></el-icon>
          <span>转发日志</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>系统设置</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <div class="mini-status">
          <span class="status-dot" :class="tgConnected ? 'on' : 'off'"></span>
          <span>TG {{ tgConnected ? '已连接' : '未连接' }}</span>
        </div>
        <div class="mini-status">
          <span class="status-dot" :class="listenerRunning ? 'on' : 'off'"></span>
          <span>监听 {{ listenerRunning ? '运行中' : '已停止' }}</span>
        </div>
      </div>
    </el-aside>

    <el-container>
      <el-header class="header" :class="ambientClass">
        <div class="header-left">
          <button
            class="nav-toggle"
            type="button"
            aria-label="打开菜单"
            @click="drawerOpen = true"
          >
            <el-icon><Menu /></el-icon>
          </button>
          <span class="page-title">{{ currentPageTitle }}</span>
        </div>
        <div class="header-right">
          <div class="status-pill" :class="listenerRunning ? 'pill-ok' : 'pill-off'">
            <span class="status-dot" :class="listenerRunning ? 'on' : 'off'"></span>
            <span class="pill-text">{{ listenerRunning ? '监听运行中' : '监听已停止' }}</span>
          </div>
          <el-button
            type="warning"
            size="small"
            :icon="RefreshRight"
            :loading="restarting"
            @click="handleRestart"
          >
            <span class="btn-label">重启服务</span>
          </el-button>
          <el-dropdown trigger="click" @command="handleCommand">
            <el-button size="small" :icon="UserFilled">
              <span class="btn-label">{{ currentUser }}</span> <el-icon><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="settings">
                  <el-icon><Setting /></el-icon> 系统设置
                </el-dropdown-item>
                <el-dropdown-item command="changePassword">
                  <el-icon><Key /></el-icon> 修改密码
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon> 退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>

    <!-- 移动端抽屉遮罩：点击关闭 -->
    <div v-if="drawerOpen" class="sidebar-backdrop" @click="drawerOpen = false"></div>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { telegramApi, systemApi, authApi } from '../api'
import { ElMessageBox, ElMessage, ElNotification } from 'element-plus'
import {
  RefreshRight, UserFilled, ArrowDown, Setting, Key, SwitchButton, Menu,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const activeMenu = computed(() => route.path)
const tgConnected = ref(false)
const listenerRunning = ref(false)
const restarting = ref(false)
const currentUser = ref('')

// 移动端抽屉式侧边栏（≤768px 生效，见样式里的媒体查询）
const drawerOpen = ref(false)

// 切换页面后自动收起，否则抽屉会盖住刚打开的页面
watch(() => route.path, () => { drawerOpen.value = false })

const pageTitles = {
  '/': '仪表盘',
  '/telegram': 'Telegram 登录',
  '/channels': '频道管理',
  '/aliases': '群名称绑定',
  '/rules': '转发规则',
  '/logs': '转发日志',
  '/settings': '系统设置',
}
const currentPageTitle = computed(() => pageTitles[route.path] || '青鸟 · TG→飞书转发')

// 状态感知氛围：全绿=森林绿宁静 / 断连=血橙红聚焦 / 部分异常=落日橘
const ambientClass = computed(() => {
  if (!tgConnected.value) return 'ambient-error'
  if (!listenerRunning.value) return 'ambient-warn'
  return 'ambient-ok'
})

let pollTimer = null

async function pollStatus() {
  try {
    const [tg, listener] = await Promise.all([
      telegramApi.status(),
      telegramApi.listenerStatus(),
    ])
    tgConnected.value = tg.data.authorized
    listenerRunning.value = listener.data.running
  } catch {
    // ignore
  }
}

async function loadUserInfo() {
  try {
    const res = await authApi.me()
    if (res.data.ok) {
      currentUser.value = res.data.user.username
    }
  } catch {
    // ignore
  }
}

async function handleCommand(command) {
  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出登录吗？', '退出登录', {
        confirmButtonText: '退出',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      return
    }
    try {
      await authApi.logout()
    } catch {
      // ignore — JWT is stateless
    }
    localStorage.removeItem('token')
    ElMessage.success('已退出登录')
    router.replace('/login')
  } else if (command === 'settings') {
    router.push('/settings')
  } else if (command === 'changePassword') {
    showChangePasswordDialog()
  }
}

async function showChangePasswordDialog() {
  try {
    const { value: oldPwd } = await ElMessageBox.prompt('请输入当前密码', '修改密码', {
      inputType: 'password',
      inputPlaceholder: '当前密码',
      inputPattern: /.+/,
      inputErrorMessage: '请输入当前密码',
    })
    const { value: newPwd } = await ElMessageBox.prompt('请输入新密码（至少 6 位）', '修改密码', {
      inputType: 'password',
      inputPlaceholder: '新密码',
      inputPattern: /^.{6,}$/,
      inputErrorMessage: '密码至少 6 个字符',
    })
    const res = await authApi.changePassword(oldPwd, newPwd)
    if (res.data.ok) {
      ElMessage.success('密码已修改，请重新登录')
      localStorage.removeItem('token')
      setTimeout(() => router.replace('/login'), 1500)
    } else {
      ElMessage.error(res.data.error || '修改失败')
    }
  } catch {
    // user cancelled
  }
}

async function handleRestart() {
  try {
    await ElMessageBox.confirm(
      '重启将短暂中断 API 和 TG 监听（约 5–10 秒），确定现在重启吗？',
      '重启服务',
      {
        confirmButtonText: '确认重启',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch {
    return  // user cancelled
  }

  restarting.value = true
  const notif = ElNotification({
    title: '正在重启',
    message: '服务正在重启，请稍候...',
    type: 'info',
    duration: 0,  // persistent
  })

  try {
    await systemApi.restart()
  } catch {
    // expected — connection drops during restart
  }

  // Poll health until the service comes back
  let recovered = false
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000))
    try {
      await systemApi.health()
      recovered = true
      break
    } catch {
      // still down
    }
  }

  notif.close()
  restarting.value = false

  if (recovered) {
    ElNotification({
      title: '重启完成',
      message: '服务已恢复运行',
      type: 'success',
      duration: 3000,
    })
    pollStatus()
  } else {
    ElNotification({
      title: '重启超时',
      message: '服务未在预期时间内恢复，请检查容器状态',
      type: 'error',
      duration: 5000,
    })
  }
}

onMounted(() => {
  loadUserInfo()
  pollStatus()
  pollTimer = setInterval(pollStatus, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.app-layout {
  height: 100vh;
}

/* --- 侧边栏：晨雾液态玻璃 --- */
.sidebar {
  background: linear-gradient(180deg, rgba(45, 66, 82, 0.92), rgba(38, 56, 70, 0.95));
  backdrop-filter: blur(20px) saturate(1.2);
  -webkit-backdrop-filter: blur(20px) saturate(1.2);
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.logo {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.logo-img {
  width: 42px;
  height: 42px;
  border-radius: 13px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
  flex-shrink: 0;
}
.logo-text {
  display: flex;
  flex-direction: column;
}
.logo-title {
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.logo-sub {
  color: rgba(255, 255, 255, 0.45);
  font-size: 11px;
  letter-spacing: 0.06em;
}
.sidebar-menu {
  border-right: none;
  background: transparent;
  flex: 1;
  padding: 10px;
}
.sidebar-menu .el-menu-item {
  color: rgba(255, 255, 255, 0.68);
  border-radius: 12px;
  margin-bottom: 4px;
  height: 44px;
  transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}
.sidebar-menu .el-menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #d3eacc;
  transform: translateX(3px);
}
.sidebar-menu .el-menu-item.is-active {
  background: linear-gradient(135deg, rgba(143, 201, 127, 0.28), rgba(94, 163, 99, 0.18));
  color: #b4dba8;
  font-weight: 600;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12);
}
.sidebar-footer {
  padding: 16px 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mini-status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(255, 255, 255, 0.65);
  font-size: 12px;
}

/* --- 头部：状态感知色温 --- */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255, 254, 248, 0.55);
  backdrop-filter: blur(18px) saturate(1.2);
  -webkit-backdrop-filter: blur(18px) saturate(1.2);
  border-bottom: 1px solid rgba(255, 255, 255, 0.6);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;              /* 让标题可省略，而不是把顶栏顶宽 */
}
.page-title {
  font-size: 18px;
  font-weight: 700;
  color: #33414e;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 1;
  min-width: 0;
}
.status-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  transition: all 0.4s;
}
.pill-ok {
  background: rgba(143, 201, 127, 0.18);
  color: #2d6a4f;
  border: 1px solid rgba(143, 201, 127, 0.4);
}
.pill-off {
  background: rgba(231, 115, 74, 0.12);
  color: #b0542f;
  border: 1px solid rgba(231, 115, 74, 0.35);
}

.main-content {
  padding: 22px;
  overflow-y: auto;
  background: transparent;
}

/* ============================================================
   移动端适配
   ------------------------------------------------------------
   此前整个应用外壳一个 @media 都没有：侧边栏固定 232px 不折叠、
   顶栏 .header-right 不换行也不收缩，窗口 <480px 时顶栏撑破页面
   （documentElement.scrollWidth 530 vs 视口 480）。
   ≤768px 起侧边栏改为抽屉（汉堡按钮 + 遮罩），主区因此拿到整屏宽度；
   ≤560px 进一步收起顶栏按钮文字，彻底消除横向滚动。
   ============================================================ */
.nav-toggle {
  display: none;                     /* 桌面隐藏，仅窄屏出现 */
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  padding: 0;
  border: 1px solid rgba(51, 65, 78, 0.12);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.6);
  color: #33414e;
  cursor: pointer;
  transition: background 0.2s;
}
.nav-toggle:hover { background: rgba(255, 255, 255, 0.95); }

.sidebar-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1999;
  background: rgba(18, 26, 34, 0.45);
  -webkit-backdrop-filter: blur(2px);
  backdrop-filter: blur(2px);
}

@media (max-width: 768px) {
  .nav-toggle { display: inline-flex; }

  /* 抽屉：position:fixed 脱离文档流，主区自然占满整屏 */
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(232px, 78vw);
    z-index: 2000;
    transform: translateX(-100%);
    transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
  }
  .sidebar.drawer-open {
    transform: translateX(0);
    box-shadow: 0 0 40px rgba(0, 0, 0, 0.45);
  }

  .header { padding: 0 12px; }
  .main-content { padding: 14px; }
  .page-title { font-size: 16px; }
}

@media (max-width: 560px) {
  /* 只留图标：这是消除横向溢出的关键一步 */
  .pill-text { display: none; }
  .status-pill { padding: 6px 10px; }
  .btn-label { display: none; }
  .header-right { gap: 8px; }
  .main-content { padding: 10px; }
}
</style>
