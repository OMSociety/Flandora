/**
 * CQ 码预览用的小工具。
 *
 * 只服务于面板里的「一眼看到发出去会是什么样」：真正解析与转义在服务端
 * （`flandora/ob_segments.py`），这里保持一致即可，不参与发送。
 */

const ESCAPE = [
  ['&', '&amp;'],
  ['[', '&#91;'],
  [']', '&#93;'],
  [',', '&#44;'],
]

export function escapeText(text) {
  let out = String(text ?? '')
  for (const [from, to] of ESCAPE) out = out.split(from).join(to)
  return out
}

export function segmentToCq(segment) {
  const type = segment?.type || ''
  const data = segment?.data || {}
  if (type === 'text') return escapeText(data.text)
  const params = Object.entries(data)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${key}=${escapeText(value)}`)
  return `[CQ:${type}${params.length ? ',' + params.join(',') : ''}]`
}

export function segmentsToCq(segments) {
  return (segments || []).map(segmentToCq).join('')
}
