<template>
  <el-container class="app-layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">
        <el-icon size="24"><Promotion /></el-icon>
        <span>TG→飞书转发</span>
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
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <span class="page-title">{{ currentPageTitle }}</span>
        </div>
        <div class="header-right">
          <el-tag :type="tgConnected ? 'success' : 'danger'" size="small">
            TG {{ tgConnected ? '已连接' : '未连接' }}
          </el-tag>
          <el-tag :type="listenerRunning ? 'success' : 'info'" size="small">
            监听 {{ listenerRunning ? '运行中' : '已停止' }}
          </el-tag>
          <el-button
            type="warning"
            size="small"
            :icon="RefreshRight"
            :loading="restarting"
            @click="handleRestart"
          >
            重启服务
          </el-button>
          <el-dropdown trigger="click" @command="handleCommand">
            <el-button size="small" :icon="UserFilled">
              {{ currentUser }} <el-icon><ArrowDown /></el-icon>
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
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { telegramApi, systemApi, authApi } from '../api'
import { ElMessageBox, ElMessage, ElNotification } from 'element-plus'
import {
  RefreshRight, UserFilled, ArrowDown, Setting, Key, SwitchButton,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const activeMenu = computed(() => route.path)
const tgConnected = ref(false)
const listenerRunning = ref(false)
const restarting = ref(false)
const currentUser = ref('')

const pageTitles = {
  '/': '仪表盘',
  '/telegram': 'Telegram 登录',
  '/channels': '频道管理',
  '/rules': '转发规则',
  '/logs': '转发日志',
  '/settings': '系统设置',
}
const currentPageTitle = computed(() => pageTitles[route.path] || 'TG→飞书转发系统')

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
.sidebar {
  background: #304156;
  overflow: hidden;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  background: #2b3648;
}
.sidebar-menu {
  border-right: none;
  background: #304156;
}
.sidebar-menu .el-menu-item {
  color: #bfcbd9;
}
.sidebar-menu .el-menu-item:hover {
  background: #263445;
  color: #409eff;
}
.sidebar-menu .el-menu-item.is-active {
  background: #1f2d3d;
  color: #409eff;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e6e6e6;
  background: #fff;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
}
.header-right {
  display: flex;
  gap: 10px;
}
.main-content {
  background: #f0f2f5;
  padding: 20px;
  overflow-y: auto;
}
</style>
