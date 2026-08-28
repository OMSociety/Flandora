export function pad(n) {
  return String(n).padStart(2, '0')
}

// epoch 秒 -> HH:MM:SS
export function formatClock(ts) {
  if (ts == null) return '--:--:--'
  const d = new Date(Number(ts) * 1000)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// epoch 秒 -> 运行时长 x天 xh xm
export function formatUptime(sec) {
  if (sec == null || sec < 0) return '--'
  const s = Math.floor(sec)
  const days = Math.floor(s / 86400)
  const hours = Math.floor((s % 86400) / 3600)
  const mins = Math.floor((s % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时 ${mins}分`
  if (hours > 0) return `${hours}小时 ${mins}分`
  return `${mins}分 ${s % 60}秒`
}

// 千分位
export function fmt(n) {
  return Number(n || 0).toLocaleString('zh-CN')
}

export function modeLabel(mode) {
  const map = { mention: '仅@回复', all: '全部回复', batch: '批处理' }
  return map[mode] || mode || '--'
}

export function kindLabel(kind) {
  const map = { private: '私聊', group: '群聊', manual: '手动' }
  return map[kind] || kind || '--'
}

// 窗口句柄 -> 十六进制，如 0x100E9E
export function hexHwnd(n) {
  const v = Number(n)
  if (!v || Number.isNaN(v)) return '0x0'
  return '0x' + v.toString(16).toUpperCase()
}
