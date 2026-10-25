// OneBot 入口的共用定义：页面标签、编辑弹窗与测试都从这里取。
// 字段与默认值对齐后端 flandora/ob_network.py 的 normalize_node()。

export const NODE_TYPES = [
  {
    value: 'ws_server', tab: 'WS 服务端', dialog: 'WebSocket 服务', icon: 'Plug', kind: 'server',
    hint: '本地监听 WebSocket 端口，客户端建立持久连接。',
  },
  {
    value: 'http_server', tab: 'HTTP 服务端', dialog: 'HTTP 服务', icon: 'Server', kind: 'server',
    hint: '本地监听 HTTP 端口，客户端每次 POST 一个动作。',
  },
  {
    value: 'ws_client', tab: '反向 WS', dialog: '反向 WebSocket', icon: 'Cable', kind: 'client',
    hint: '主动连到机器人框架的 WebSocket 地址。',
  },
  {
    value: 'http_client', tab: 'HTTP 上报', dialog: 'HTTP 上报', icon: 'Upload', kind: 'client',
    hint: '把事件 POST 到机器人框架的 HTTP 端点。',
  },
]

export const ROLES = [
  { value: 'universal', label: '两者都做' },
  { value: 'api', label: '只收动作' },
  { value: 'event', label: '只推事件' },
]

// 运行态取值来自后端 NetworkManager.status()：disabled/stopped 灰、listening/connected/ready 绿、
// starting/reconnecting 黄、error 红。
const STATES = {
  disabled: { label: '未启用', tone: 'off' },
  stopped: { label: '已停止', tone: 'off' },
  starting: { label: '启动中', tone: 'warn' },
  listening: { label: '监听中', tone: 'ok' },
  connected: { label: '已连接', tone: 'ok' },
  ready: { label: '就绪', tone: 'ok' },
  reconnecting: { label: '重连中', tone: 'warn' },
  error: { label: '出错', tone: 'err' },
}

export function typeMeta(value) {
  return NODE_TYPES.find((t) => t.value === value) || NODE_TYPES[0]
}

export function isServerNode(node) {
  return node.type === 'ws_server' || node.type === 'http_server'
}

export function nodeTarget(node) {
  if (isServerNode(node)) return `${node.host || '127.0.0.1'}:${node.port || 0}${node.path || '/'}`
  return node.url || ''
}

export function stateInfo(state, enabled) {
  if (state && STATES[state]) return STATES[state]
  if (enabled === false) return STATES.disabled
  return { label: '未上报', tone: 'off' }
}

export function blankNode(type, index) {
  const base = {
    type,
    name: `${type}-${index + 1}`,
    enabled: true,
    role: 'universal',
    access_token: '',
    message_format: '',
    path: '/',
    host: '127.0.0.1',
    port: 0,
    url: '',
    timeout_ms: 5000,
    reconnect_interval_ms: 5000,
  }
  if (type === 'ws_server') return { ...base, port: 7999 }
  if (type === 'http_server') return { ...base, port: 5700 }
  if (type === 'ws_client') return { ...base, url: 'ws://127.0.0.1:6199/ws' }
  return { ...base, url: 'http://127.0.0.1:8080/onebot' }
}

// 名字用于状态展示与日志，后端拒绝重名，这里先把冲突躲开。
export function uniqueName(type, nodes) {
  const used = new Set((nodes || []).map((n) => n.name))
  let i = (nodes || []).length + 1
  while (used.has(`${type}-${i}`)) i += 1
  return `${type}-${i}`
}

// 提交前先按后端规则自查一遍，避免整页保存换回一个 400。
export function nodeProblem(node) {
  if (!String(node.name || '').trim()) return '名字不能为空'
  const port = Number(node.port)
  if (isServerNode(node)) {
    if (!Number.isInteger(port) || port < 1 || port > 65535) return '端口取 1–65535'
    const path = String(node.path || '')
    if (path && !path.startsWith('/')) return '路径要以 / 开头'
    return ''
  }
  const url = String(node.url || '').trim()
  if (!url) return '目标地址不能为空'
  if (node.type === 'ws_client' && !/^wss?:\/\//.test(url)) return '反向 WS 的地址要以 ws:// 或 wss:// 开头'
  if (node.type === 'http_client' && !/^https?:\/\//.test(url)) return 'HTTP 上报的地址要以 http:// 或 https:// 开头'
  return ''
}

export function randomToken() {
  const bytes = new Uint8Array(24)
  crypto.getRandomValues(bytes)
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
}
