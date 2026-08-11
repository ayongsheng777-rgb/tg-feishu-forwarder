import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// --- Attach JWT to every request ---
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// --- Redirect to /login on 401 (except when already on auth pages) ---
api.interceptors.response.use(
  response => response,
  error => {
    if (error.response && error.response.status === 401) {
      // Only redirect if we're not already on login/setup pages
      const path = window.location.pathname
      if (path !== '/login' && path !== '/setup') {
        localStorage.removeItem('token')
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

// ---------------- Auth ----------------
export const authApi = {
  status: () => api.get('/auth/status'),
  setup: (username, password) => api.post('/auth/setup', { username, password }),
  login: (username, password) => api.post('/auth/login', { username, password }),
  otpVerify: (tempToken, code) => api.post('/auth/otp-verify', { temp_token: tempToken, code }),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout'),
  setupOtp: () => api.post('/auth/setup-otp'),
  enableOtp: (code) => api.post('/auth/enable-otp', { code }),
  disableOtp: (password) => api.post('/auth/disable-otp', { password }),
  changePassword: (oldPassword, newPassword) => api.post('/auth/change-password', { old_password: oldPassword, new_password: newPassword }),
}

// ---------------- System ----------------
export const systemApi = {
  health: () => api.get('/system/health'),
  info: () => api.get('/system/info'),
  env: () => api.get('/system/env'),
  restart: () => api.post('/system/restart'),
  reportPreview: () => api.get('/system/report'),
  reportSend: (chat_id) => api.post('/system/report/send', chat_id ? { chat_id } : {}),
}

// ---------------- Chat Aliases (群名称绑定) ----------------
export const aliasApi = {
  list: () => api.get('/aliases'),
  create: (data) => api.post('/aliases', data),
  update: (id, data) => api.put(`/aliases/${id}`, data),
  delete: (id) => api.delete(`/aliases/${id}`),
  channels: (id) => api.get(`/aliases/${id}/channels`),
  clone: (id, targetId) => api.post(`/aliases/${id}/clone`, { target_alias_id: targetId }),
}

// ---------------- Channels ----------------
export const channelApi = {
  list: () => api.get('/channels'),
  create: (data) => api.post('/channels', data),
  update: (id, data) => api.put(`/channels/${id}`, data),
  delete: (id) => api.delete(`/channels/${id}`),
  dialogs: () => api.get('/channels/dialogs'),
  fetch: (id, limit = 20) => api.post(`/channels/${id}/fetch`, null, { params: { limit }, timeout: 120000 }),
}

// ---------------- Rules ----------------
export const ruleApi = {
  list: () => api.get('/rules'),
  create: (data) => api.post('/rules', data),
  update: (id, data) => api.put(`/rules/${id}`, data),
  delete: (id) => api.delete(`/rules/${id}`),
}

// ---------------- Stats (Dashboard) ----------------
export const statsApi = {
  dashboard: () => api.get('/stats/dashboard'),
}

// ---------------- Logs ----------------
export const logApi = {
  list: (params) => api.get('/logs', { params }),
  stats: () => api.get('/logs/stats'),
  retry: (id) => api.post(`/logs/${id}/retry`),
  retryFailed: () => api.post('/logs/retry-failed', {}, { timeout: 120000 }),
  clear: (params) => api.delete('/logs', { params: params || {} }),
}

// ---------------- Settings ----------------
export const settingApi = {
  list: () => api.get('/settings'),
  get: (key) => api.get(`/settings/${key}`),
  update: (key, value) => api.put(`/settings/${key}`, { value }),
  bulk: (items) => api.post('/settings/bulk', items),
}

// ---------------- Telegram ----------------
export const telegramApi = {
  status: () => api.get('/telegram/status'),
  loginStart: (phone) => api.post('/telegram/login/start', { phone }),
  loginComplete: (phone, code, password = '') => api.post('/telegram/login/complete', { phone, code, password }),
  logout: () => api.post('/telegram/logout'),
  setApiCredentials: (api_id, api_hash) => api.post('/telegram/api-credentials', { api_id, api_hash }),
  startListener: () => api.post('/telegram/listener/start'),
  stopListener: () => api.post('/telegram/listener/stop'),
  listenerStatus: () => api.get('/telegram/listener/status'),
}

export default api
