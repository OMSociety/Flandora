export const statusFixture = {
  running: true,
  paused: false,
  uptime_sec: 7384,
  pid: 4280,
  version: '1.0.0',
  web_port: 8766,
  send_method: 'uia',
  ob: { host: '127.0.0.1', port: 7999, connected: true, clients: 1 },
  inbound: { source: 'local', ready: true },
  counters: { recv: 128, pushed: 116, sent: 42, dropped: 0 },
  window: { title: '微信', pid: 6132, hwnd: 123456 },
  window_locked: false,
}

export const configFixture = {
  bot_nicknames: ['芙兰朵露', '兰兰露'],
  bot_wxid: '',
  self_name: '芙兰朵露',
  send_method: 'uia',
  buffer_seconds: 5,
  web_port: 8766,
  webui_password_set: false,
  ob_server_host: '127.0.0.1',
  ob_server_port: 7999,
  ob_server_token: '',
  ob_message_format: 'array',
  ob_networks: [
    {
      type: 'ws_server',
      name: 'default',
      enabled: true,
      host: '127.0.0.1',
      port: 7999,
      path: '/',
      role: 'universal',
      access_token: '',
      message_format: '',
    },
  ],
  astrbot_ob_url: '',
  astrbot_attachments: '',
  group_name_map: { 'example@chatroom': '测试群聊' },
}

export const wizardFixture = {
  ok: true,
  current: 'dbkey',
  disclaimer_accepted: true,
  db_key: { present: false },
  image_key: { present: false },
  manual_rva: 0,
  keytool_running: false,
  imagekey_running: false,
  steps: {
    disclaimer: { state: 'ok', message: '', log: [] },
    dbkey: { state: 'idle', message: '等待开始取钥', log: [] },
    imagekey: { state: 'idle', message: '', log: [] },
    verify: { state: 'idle', message: '', log: [] },
  },
}

export async function mockApi(page, options = {}) {
  const requests = []
  const desktopStatus = {
    ok: true, enabled: true, running: false, logged_in: false,
    state: 'absent', desktop: 'WeBotSandbox', exe_found: true, error: '',
    ...(options.desktopStatus || {}),
  }
  // options.auth = { password } 时模拟「面板设了密码」：登录前所有接口都回 401。
  let authed = !options.auth
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    requests.push({ path: url.pathname, method: request.method(), body: request.postDataJSON() })
    const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname === '/api/auth') return json({ enabled: !!options.auth, authenticated: authed })
    if (url.pathname === '/api/login') {
      const body = request.postDataJSON() || {}
      if (options.auth && body.password === options.auth.password) {
        authed = true
        return json({ ok: true, enabled: true })
      }
      return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ ok: false, error: '密码不对' }) })
    }
    if (options.auth && !authed) {
      return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ ok: false, error: '未登录', auth_required: true }) })
    }
    if (url.pathname === '/api/status') return json(statusFixture)
    if (url.pathname === '/api/config' && request.method() === 'GET') return json(configFixture)
    if (url.pathname === '/api/config' && request.method() === 'PUT') return json({ ok: true, restart_required: ['ob_server_port'] })
    if (url.pathname === '/api/stats') return json({
      counters: { recv: 128, pushed: 116, sent: 42 },
      hourly: Array.from({ length: 24 }, (_, i) => ({ hour: String(i), in: (i * 7) % 21, out: (i * 3) % 12 })),
      top_contacts: [{ contact: '文件传输助手', count: 46 }, { contact: '周末读书会', count: 31 }, { contact: '桥接测试群', count: 18 }],
    })
    if (url.pathname === '/api/messages') return json({ messages: [
      { ts: 1770000000, dir: 'in', kind: 'group', contact: '周末读书会', sender: '林同学', text: '今晚的讨论还是八点开始，记得带上上次的笔记。' },
      { ts: 1770000010, dir: 'out', kind: 'group', contact: '周末读书会', text: '收到，我会准时参加。' },
      { ts: 1770000020, dir: 'in', kind: 'private', contact: '文件传输助手', text: '桥接连接测试' },
    ] })
    if (url.pathname === '/api/logs') return json({ logs: [
      { ts: 1, level: 'INFO', text: '2026-10-24 09:30:01 [INFO] OneBot WebSocket listening on 127.0.0.1:7999' },
      { ts: 2, level: 'INFO', text: '2026-10-24 09:30:03 [INFO] MaiBot adapter connected (1 client)' },
      { ts: 3, level: 'WARNING', text: '2026-10-24 09:30:09 [WARNING] Example diagnostic entry for layout verification' },
      { ts: 4, level: 'ERROR', text: '2026-10-24 09:30:12 [ERROR] Example recoverable connection error' },
    ] })
    if (url.pathname === '/api/wizard/status') return json(wizardFixture)
    if (url.pathname === '/api/desktop/status') return json(desktopStatus)
    if (url.pathname === '/api/contacts') return json({ contacts: [{ id: 'filehelper', name: '文件传输助手', kind: 'private' }, { id: 'example@chatroom', name: '测试群聊', kind: 'group' }] })
    if (url.pathname === '/api/send') return json({ ok: true })
    if (url.pathname === '/api/control' || url.pathname === '/api/mode' || url.pathname === '/api/windows/select') return json({ ok: true })
    if (url.pathname === '/api/windows') return json({ windows: [] })
    return json({ ok: true })
  })
  await page.route('**/api/desktop/screenshot**', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: '测试环境未提供画面' }) }))
  return requests
}
