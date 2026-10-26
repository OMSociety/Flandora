import { toast } from './store'

/**
 * 统一的 fetch 封装。返回 { ok, status, data }。
 * 不会对非 2xx 抛异常（调用方自行判断 ok）；GET 出错时也会 toast。
 */
export async function request(path, options = {}) {
  const opts = { headers: {}, ...options }
  if (opts.body && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body)
    opts.headers['Content-Type'] = 'application/json'
  }
  const silent = !!opts.silent
  let res
  try {
    res = await fetch(path, opts)
  } catch (err) {
    // fetch 抛 TypeError 就是连不上（面板进程停了、端口关了），别把这句原文丢给用户
    console.warn('请求失败：', path, err)
    if (!silent) toast('无法连接桥接服务：面板进程可能已停止，先从托盘里确认它在运行', 'error')
    return { ok: false, status: 0, data: null }
  }
  let data = null
  try {
    data = await res.json()
  } catch (err) {
    data = null
  }
  // 会话过期或没登录：交给 App.vue 切到登录页，别在这儿连弹一串错误
  if (res.status === 401 && data && data.auth_required) {
    window.dispatchEvent(new Event('flandora:auth-required'))
    return { ok: false, status: 401, data }
  }
  if (!silent && !res.ok && !(data && typeof data === 'object' && 'ok' in data)) {
    toast(`请求失败 (${res.status})`, 'error')
  }
  return { ok: res.ok, status: res.status, data }
}

export async function get(path, silent = false) {
  return request(path, { method: 'GET', silent })
}

export async function post(path, body, silent = false) {
  return request(path, { method: 'POST', body, silent })
}

export async function put(path, body, silent = false) {
  return request(path, { method: 'PUT', body, silent })
}
