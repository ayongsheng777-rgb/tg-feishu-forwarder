import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '../layout/MainLayout.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/setup',
    name: 'Setup',
    component: () => import('../views/Setup.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: MainLayout,
    children: [
      { path: '', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
      { path: 'channels', name: 'Channels', component: () => import('../views/Channels.vue') },
      { path: 'aliases', name: 'Aliases', component: () => import('../views/Aliases.vue') },
      { path: 'rules', name: 'Rules', component: () => import('../views/Rules.vue') },
      { path: 'logs', name: 'Logs', component: () => import('../views/Logs.vue') },
      { path: 'settings', name: 'Settings', component: () => import('../views/Settings.vue') },
      { path: 'telegram', name: 'Telegram', component: () => import('../views/TelegramLogin.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Navigation guard: redirect to /login if not authenticated
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  const isPublic = to.meta.public === true

  if (isPublic) {
    // If already logged in and visiting /login, redirect to dashboard
    if (to.path === '/login' && token) {
      next('/')
    } else {
      next()
    }
  } else if (!token) {
    next('/login')
  } else {
    next()
  }
})

export default router
