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
    if (!silent) toast('网络请求失败: ' + (err.message || err), 'error')
    return { ok: false, status: 0, data: null }
  }
  let data = null
  try {
    data = await res.json()
  } catch (err) {
    data = null
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
