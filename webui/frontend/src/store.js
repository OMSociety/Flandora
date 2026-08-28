import { reactive } from 'vue'
import { get } from './api'

// ============ toasts ============
export const toasts = reactive([])
let toastId = 0
export function toast(message, type = 'info', duration = 3000) {
  const t = { id: ++toastId, message, type }
  toasts.push(t)
  setTimeout(() => {
    const i = toasts.findIndex((x) => x.id === t.id)
    if (i > -1) toasts.splice(i, 1)
  }, duration)
}

// ============ status ============
export const status = reactive({
  running: false,
  paused: false,
  uptime_sec: null,
  pid: null,
  version: '',
  web_port: null,
  send_method: 'uia',
  ob: { host: '', port: 0, connected: false, clients: 0 },
  weflow: { base_url: '', connected: false },
  group_reply_mode: 'mention',
  counters: { recv: 0, pushed: 0, sent: 0, dropped: 0 },
  window: null,
  window_locked: false,
})

let statusTimer = null

export async function refreshStatus(silent = false) {
  const res = await get('/api/status', silent)
  if (res.ok && res.data) {
    Object.assign(status, res.data)
    status.ob = Object.assign({ host: '', port: 0, connected: false, clients: 0 }, res.data.ob)
    status.weflow = Object.assign({ base_url: '', connected: false }, res.data.weflow)
    status.counters = Object.assign(
      { recv: 0, pushed: 0, sent: 0, dropped: 0 },
      res.data.counters
    )
    status.window = res.data.window && typeof res.data.window === 'object' ? res.data.window : null
    status.window_locked = !!res.data.window_locked
  }
}

export function startStatusPolling(interval = 5000) {
  stopStatusPolling()
  refreshStatus()
  statusTimer = setInterval(() => refreshStatus(true), interval)
}

export function stopStatusPolling() {
  if (statusTimer) clearInterval(statusTimer)
  statusTimer = null
}

// ============ messages (newest first) ============
const MSG_CAP = 500
export const messages = reactive([])

export function setMessages(list) {
  messages.splice(0, messages.length, ...(Array.isArray(list) ? list : []))
}

export function addMessage(m) {
  if (!m || typeof m !== 'object') return
  const top = messages[0]
  if (
    top &&
    top.ts === m.ts &&
    top.dir === m.dir &&
    top.contact === m.contact &&
    top.text === m.text
  ) {
    return
  }
  messages.unshift(m)
  if (messages.length > MSG_CAP) messages.length = MSG_CAP
}

export function clearMessages() {
  messages.splice(0, messages.length)
}

export async function loadInitialMessages(limit = 200) {
  const res = await get(`/api/messages?limit=${limit}`)
  if (res.ok && res.data && Array.isArray(res.data.messages) && res.data.messages.length) {
    // 仅在未被 WS 快照填充时使用 REST 作为初始种子
    if (messages.length === 0) setMessages(res.data.messages)
  }
}

// ============ logs (oldest first) ============
const LOG_CAP = 1000
export const logs = reactive([])

export function setLogs(list) {
  logs.splice(0, logs.length, ...(Array.isArray(list) ? list : []))
}

export function addLog(l) {
  if (!l || typeof l !== 'object') return
  const last = logs[logs.length - 1]
  if (last && last.ts === l.ts && last.level === l.level && last.text === l.text) return
  logs.push(l)
  if (logs.length > LOG_CAP) logs.splice(0, logs.length - LOG_CAP)
}

export function clearLogs() {
  logs.splice(0, logs.length)
}

export async function loadInitialLogs(limit = 300) {
  const res = await get(`/api/logs?limit=${limit}`)
  if (res.ok && res.data && Array.isArray(res.data.logs) && res.data.logs.length) {
    if (logs.length === 0) setLogs(res.data.logs)
  }
}
