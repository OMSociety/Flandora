export const statusFixture = {
  running: true,
  paused: false,
  uptime_sec: 7384,
  pid: 4280,
  version: '1.0.0',
  web_port: 4950,
  send_method: 'uia',
  ob: {
    host: '127.0.0.1',
    port: 7999,
    connected: true,
    clients: 1,
    nodes: [{ type: 'ws_server', name: 'default', state: 'listening', connections: 1, error: '', role: 'universal' }],
  },
  inbound: { source: 'local', ready: true },
  counters: { recv: 128, pushed: 116, sent: 42, dropped: 0 },
  window: { title: '微信', pid: 6132, hwnd: 123456 },
  window_locked: false,
  self: { wxid: 'wxid_demouser10001', nickname: '芙兰朵露' },
  webui: { host: '127.0.0.1', port: 4950, tls: false, tls_cert_set: false, tls_key_set: false, password_set: false },
}

export const updateFixture = {
  ok: true,
  mode: 'source',
  current: '1.0.0',
  app_dir: 'C:/Flandora',
  app_dir_writable: true,
  latest: null,
  pending: null,
}

export const configFixture = {
  send_method: 'uia',
  web_port: 4950,
  web_bind_host: '127.0.0.1',
  webui_tls: false,
  webui_password_set: false,
  log_max_mb: 8,
  log_backup_count: 5,
  ob_server_host: '127.0.0.1',
  ob_server_port: 7999,
  ob_server_token: '',
  ob_message_format: 'array',
  ob_heartbeat_seconds: 30,
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
  client_attachments: 'D:/wechat/attachments',
  group_name_map: { 'example@chatroom': '测试群聊' },
}

export const storageFixture = {
  ok: true,
  data_dir: 'D:/WorkSpace/Github/Flandora/data',
  total_bytes: 4773947,
  total_files: 44,
  other_bytes: 9659,
  logs: {
    path: 'D:/WorkSpace/Github/Flandora/data/flandora.log',
    bytes: 128426,
    rotated_bytes: 20480,
    rotated_files: 2,
    files: [
      { name: 'flandora.log.1', bytes: 10240 },
      { name: 'flandora.log.2', bytes: 10240 },
    ],
    max_mb: 8,
    backup_count: 5,
  },
  messages: {
    path: 'D:/WorkSpace/Github/Flandora/data/messages.db',
    bytes: 2691072,
    rows: 32,
    oldest: 1792832171,
    newest: 1792837968,
  },
  msg_ids: { path: 'D:/WorkSpace/Github/Flandora/data/msg_ids.db', bytes: 20480, rows: 35 },
  images: { bytes: 490710, files: 20 },
  wx_cache: { bytes: 1433600, files: 15, root: 'D:/WorkSpace/Github/Flandora/data/wx_cache' },
  updates: { path: 'D:/WorkSpace/Github/Flandora/data/updates', bytes: 0, files: 0 },
  generated_at: '2026-10-24 22:43:45',
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
    state: 'absent', desktop: 'FlandoraSandbox', exe_found: true, error: '',
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
    if (url.pathname === '/api/update/status') return json(updateFixture)
    if (url.pathname === '/api/update/check') return json({ ok: true, current: '1.0.0', newer: false, latest: { version: '1.0.0', tag: 'v1.0.0', url: 'https://github.com/OMSociety/Flandora/releases', asset: '' } })
    if (url.pathname === '/api/config' && request.method() === 'GET') return json(configFixture)
    if (url.pathname === '/api/config' && request.method() === 'PUT') return json({ ok: true, restart_required: ['ob_server_port', 'ob_networks'] })
    if (url.pathname === '/api/config/backup') {
      return json({
        version: '1.0.0',
        created_at: '2026-10-24T22:40:00',
        include_sensitive: url.searchParams.get('include_sensitive') === '1',
        excluded_sensitive: url.searchParams.get('include_sensitive') === '1' ? [] : ['webui_password', 'ob_server_token'],
        config: { web_port: 4950, group_name_map: { 'example@chatroom': '测试群聊' } },
      })
    }
    if (url.pathname === '/api/config/restore') {
      return json({
        ok: true,
        restored: ['web_port'],
        restart_required: ['web_port'],
        password_restored: false,
        backup_path: 'D:/WorkSpace/Github/Flandora/data/config.json.bak-20261024-224000',
      })
    }
    if (url.pathname === '/api/storage') return json(storageFixture)
    if (url.pathname === '/api/storage/clean') {
      const target = (request.postDataJSON() || {}).target
      const results = {
        images: { removed_files: 20, freed_bytes: 490710 },
        wx_cache: {
          removed_files: 15, freed_bytes: 1433600,
          note: '桥接当前没有运行中的收件源，下次启动时会重新解密',
        },
        old_logs: { removed_files: 1, freed_bytes: 10240 },
        updates: { removed_files: 0, freed_bytes: 0 },
        rotate_log: { removed_files: 0, freed_bytes: 0, note: '已换成新的一份，历史份按保留份数留存' },
      }
      return json({ ok: true, target, ...(results[target] || {}) })
    }
    if (url.pathname === '/api/storage/messages') {
      const body = request.postDataJSON() || {}
      const answers = {
        prune: { removed_rows: 4, rows: 28, days: body.days },
        clear: { removed_rows: 32, rows: 0 },
        vacuum: { bytes_before: 2691072, bytes_after: 12288, freed_bytes: 2678784 },
      }
      return json({ ok: true, action: body.action, ...(answers[body.action] || {}) })
    }
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
    if (url.pathname === '/api/debug/actions') return json({
      categories: ['消息', '信息'],
      actions: [
        {
          name: 'send_msg', aliases: [], category: '消息', summary: '发送一条消息',
          readOnly: false, implemented: true, wip: '',
          params: [{ name: 'message_type', type: 'string', required: true, default: '', desc: 'group 或 private' }],
        },
        {
          name: 'set_group_kick', aliases: [], category: '信息', summary: '踢出群成员',
          readOnly: false, implemented: false, wip: '微信 PC 客户端不提供对应接口', params: [],
        },
      ],
    })
    if (url.pathname === '/api/debug/invoke') return json({
      ok: true, elapsed_ms: 12,
      response: { status: 'ok', retcode: 0, data: { message_id: 41 }, echo: 'webui' },
    })
    if (url.pathname === '/api/debug/events') return json({
      events: [
        {
          message_id: 41, post_type: 'message', message_type: 'group', group_id: 1001,
          user_id: 2002, nickname: '林同学', raw_message: '今晚八点开始',
        },
      ],
    })
    if (url.pathname === '/api/contacts') return json({ contacts: [{ id: 'filehelper', name: '文件传输助手', kind: 'private' }, { id: 'example@chatroom', name: '测试群聊', kind: 'group' }] })
    if (url.pathname === '/api/send') return json({ ok: true })
    if (url.pathname === '/api/control' || url.pathname === '/api/mode' || url.pathname === '/api/windows/select') return json({ ok: true })
    if (url.pathname === '/api/windows') return json({ windows: [] })
    return json({ ok: true })
  })
  await page.route('**/api/desktop/screenshot**', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: '测试环境未提供画面' }) }))
  return requests
}
