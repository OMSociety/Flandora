import { reactive } from 'vue'

export const wsState = reactive({ state: 'connecting' })

// type -> Set<cb>
const subscribers = new Map()
let socket = null
let reconnectTimer = null
let attempts = 0
const MAX_BACKOFF = 10000
let manuallyClosed = false

function emit(type, data) {
  const set = subscribers.get(type)
  if (!set) return
  set.forEach((cb) => {
    try {
      cb(data)
    } catch (err) {
      console.error('[ws] subscriber error', err)
    }
  })
}

export function on(type, cb) {
  if (!subscribers.has(type)) subscribers.set(type, new Set())
  subscribers.get(type).add(cb)
  return () => {
    const set = subscribers.get(type)
    if (set) set.delete(cb)
  }
}

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

function connect() {
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  ) {
    return
  }
  wsState.state = attempts === 0 ? 'connecting' : 'reconnecting'
  try {
    socket = new WebSocket(wsUrl())
  } catch (err) {
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    attempts = 0
    wsState.state = 'connected'
  }

  socket.onmessage = (ev) => {
    let data
    try {
      data = JSON.parse(ev.data)
    } catch (err) {
      return
    }
    if (data && data.type) emit(data.type, data)
  }

  socket.onclose = () => {
    if (manuallyClosed) {
      wsState.state = 'closed'
      return
    }
    scheduleReconnect()
  }

  socket.onerror = () => {
    // 触发 onclose 完成重连调度
    try {
      socket.close()
    } catch (err) {
      /* ignore */
    }
  }
}

function scheduleReconnect() {
  wsState.state = 'reconnecting'
  const backoff = Math.min(1000 * Math.pow(2, attempts), MAX_BACKOFF)
  attempts++
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => connect(), backoff)
}

export function connectWS() {
  manuallyClosed = false
  connect()
}

export function closeWS() {
  manuallyClosed = true
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (socket) {
    try {
      socket.close()
    } catch (err) {
      /* ignore */
    }
  }
  wsState.state = 'closed'
}

export function sendWS(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload))
  }
}
