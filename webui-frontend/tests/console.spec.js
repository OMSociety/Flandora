import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures.js'

// 页面登记表与图标白名单都从页面侧的 Vite 模块图读取：Playwright 运行器不编译 .vue，
// 而登记表静态引入页面组件。读到的仍是控制台真正使用的那份模块，字段与源文件一致。
async function readRegistry(page) {
  return page.evaluate(async () => {
    const [{ PAGES, NAV_GROUPS }, { ICON_NAMES }] = await Promise.all([
      import('/src/pages/index.js'),
      import('/src/components/AppIcon.vue'),
    ])
    return {
      PAGES: PAGES.map(({ id, group, label, icon, description, menu }) => ({ id, group, label, icon, description, menu })),
      groups: NAV_GROUPS.map((group) => ({ label: group.label, ids: group.items.map((item) => item.id) })),
      icons: [...ICON_NAMES],
    }
  })
}

async function openPage(page, path = 'dashboard', theme = 'light') {
  await mockApi(page)
  await page.addInitScript((selected) => {
    if (!localStorage.getItem('flandora-theme')) localStorage.setItem('flandora-theme', selected)
  }, theme)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    // 截图接口在本套件里固定返回 404 模拟「还没有画面」，这是被断言过的预期错误。
    if (message.type() === 'error' && !message.text().includes('404')) errors.push(message.text())
  })
  await page.goto(`/#/${path}`)
  await expect(page.locator('.page-heading h1')).toBeVisible()
  return errors
}

test('desktop navigation, theme persistence and dashboard are usable', async ({ page }) => {
  const errors = await openPage(page)
  await expect(page.locator('.page-heading h1')).toHaveText('仪表盘')
  await expect(page.locator('.version-label')).toHaveCount(0)
  await expect(page.locator('.status-grid article')).toHaveCount(4)
  await expect(page.locator('.metric-grid strong').first()).toHaveText('128')
  await expect(page.locator('.bar-in').first()).toHaveAttribute('height', /.+/)
  await page.screenshot({ path: '../.impeccable/review/desktop.png', fullPage: true })

  await page.getByRole('button', { name: '切换为深色主题', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.screenshot({ path: '../.impeccable/review/desktop-dark.png', fullPage: true })
  await page.getByRole('button', { name: '切换为浅色主题', exact: true }).click()
  await page.getByRole('button', { name: '收起侧栏', exact: true }).click()
  await expect(page.locator('.desktop-sidebar')).toHaveClass(/collapsed/)
  await expect(page.locator('.desktop-sidebar')).toHaveCSS('width', '64px')
  // 折叠栏宽 64px：图标必须实际落在栏内，否则会被 aside 的 overflow:hidden 裁掉。
  const rail = await page.locator('.desktop-sidebar').boundingBox()
  const icon = await page.locator('.nav-item .nav-icon').first().boundingBox()
  expect(rail.width).toBeLessThan(80)
  expect(icon.x).toBeGreaterThanOrEqual(rail.x)
  expect(icon.x + icon.width).toBeLessThanOrEqual(rail.x + rail.width)
  await page.getByRole('button', { name: '消息记录', exact: false }).click()
  await expect(page).toHaveURL(/#\/messages$/)
  const { PAGES } = await readRegistry(page)
  const navLabels = await page.locator('.nav-copy strong').allTextContents()
  expect(navLabels).toEqual(PAGES.filter((item) => item.menu !== false).map((item) => item.label))
  expect(errors).toEqual([])
})

test('all registered routes render and mobile layout has no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const errors = await openPage(page)
  const { PAGES } = await readRegistry(page)
  await expect(page.locator('.desktop-sidebar')).toBeHidden()
  await page.screenshot({ path: '../.impeccable/review/mobile.png', fullPage: true })
  await page.getByRole('button', { name: '打开导航', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '控制台导航' })).toBeVisible()
  await page.screenshot({ path: '../.impeccable/review/mobile-navigation.png', fullPage: true })
  await page.getByRole('button', { name: '消息记录', exact: false }).click()
  await expect(page.getByRole('dialog', { name: '控制台导航' })).toHaveCount(0)

  for (const path of PAGES.filter((item) => item.menu !== false).map((item) => item.id)) {
    await page.goto(`/#/${path}`)
    await expect(page.locator('#main-content > *').first()).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    expect(overflow, `${path} has horizontal overflow`).toBe(false)
    await page.screenshot({ path: `../.impeccable/review/mobile-${path}.png`, fullPage: true })
  }
  expect(errors).toEqual([])
})

test('every registered page icon exists in the AppIcon whitelist', async ({ page }) => {
  await openPage(page)
  const { PAGES, icons } = await readRegistry(page)
  const known = new Set(icons)
  expect(PAGES.filter((item) => !known.has(item.icon)).map((item) => `${item.id}: ${item.icon}`)).toEqual([])
})

test('sidebar groups flatten to the registered page order', async ({ page }) => {
  await openPage(page)
  const { PAGES, groups } = await readRegistry(page)
  expect(groups.flatMap((group) => group.ids)).toEqual(PAGES.filter((item) => item.menu !== false).map((item) => item.id))
})

test('config page opens on the first section and validates raw JSON', async ({ page }) => {
  const errors = await openPage(page, 'config')
  // 第一个分区默认打开，导航四个分区
  await expect(page.getByRole('heading', { name: '面板访问' })).toBeVisible()
  expect(await page.locator('.settings-nav button strong').allTextContents()).toEqual([
    '安全与服务',
    '存储',
    '高级',
    '关于',
  ])
  await page.getByRole('button', { name: '高级', exact: false }).click()
  await expect(page.getByRole('heading', { name: '高级' })).toBeVisible()
  await page.getByLabel('原始 JSON 配置').fill('{invalid')
  await expect(page.getByRole('button', { name: '应用并保存', exact: true })).toBeDisabled()
  expect(errors).toEqual([])
})

test('access section turns autostart on with one click', async ({ page }) => {
  const requests = await mockApi(page)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/#/config')
  await expect(page.getByRole('heading', { name: '开机启动' })).toBeVisible()
  await expect(page.locator('.autostart-command')).toContainText('Flandora.exe')

  const toggle = page.getByRole('checkbox', { name: '登录后自动启动 Flandora' })
  await expect(toggle).not.toBeChecked()
  await toggle.check()
  await expect(page.locator('.toast-item.success')).toContainText('已设置开机自启')
  await expect(toggle).toBeChecked()

  const posted = requests.filter((item) => item.path === '/api/autostart' && item.method === 'POST')
  expect(posted[0]?.body).toEqual({ enabled: true })
  expect(errors).toEqual([])
})

test('advanced config page pairs the editor with per-key notes', async ({ page }) => {
  const errors = await openPage(page, 'config')
  await page.getByRole('button', { name: '高级', exact: false }).click()
  const notes = page.getByRole('complementary', { name: '配置项说明' })
  await expect(notes).toContainText('web_port')
  await expect(notes).toContainText('ob_server_port')
  await expect(notes.getByText('改后需重启').first()).toBeVisible()
  // 编辑区就是 /api/config 的返回值：说明项不进配置，也不出现在 JSON 文本里。
  const editor = page.getByLabel('原始 JSON 配置')
  expect(await editor.inputValue()).not.toContain('_comment')
  expect(errors).toEqual([])
})

test('node page adds, toggles and removes OneBot endpoints', async ({ page }) => {
  // 这里要读回请求体，所以自己挂 mock：openPage 内部会再挂一层，路由被后挂的那层截走。
  const requests = await mockApi(page)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/#/nodes')
  await expect(page.locator('.page-heading h1')).toHaveText('节点配置')
  await expect(page.locator('.account-name strong')).toHaveText('测试账号')
  await expect(page.locator('.account-name small')).toHaveText('wxid_demouser10001')
  // 附件目录搬到了本机账号卡里
  await expect(page.getByLabel('客户端附件目录')).toHaveValue('D:/wechat/attachments')

  // 通用设置是默认标签页；切到 WS 服务端能看到正在监听的那一条
  await expect(page.getByRole('tab', { name: '通用设置' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: /WS 服务端/ }).click()
  await expect(page.locator('.node-row strong')).toHaveText('default')
  await expect(page.locator('.node-state')).toHaveText('监听中')
  await expect(page.locator('.node-conn')).toHaveText('1 个连接')

  // 逐条开关，整页一次提交
  await page.getByLabel('客户端附件目录').fill('D:/wechat/files')
  await page.getByRole('switch', { name: '启用 default' }).click()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => requests.filter((request) => request.path === '/api/config' && request.method === 'PUT').length).toBe(1)
  const first = requests.find((request) => request.path === '/api/config' && request.method === 'PUT')
  expect(first.body.ob_networks[0].enabled).toBe(false)
  expect(first.body.ob_server_port).toBe(7999)
  expect(first.body.client_attachments).toBe('D:/wechat/files')

  // 新建走弹窗，端口先按后端规则自查
  await page.getByRole('tab', { name: /HTTP 服务端/ }).click()
  await expect(page.locator('.node-empty')).toBeVisible()
  await page.getByRole('button', { name: '新建HTTP 服务端' }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 HTTP 服务' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('名称').fill('astrbot')
  await dialog.getByLabel('端口').fill('0')
  await expect(dialog.getByRole('button', { name: '保存修改' })).toBeDisabled()
  await dialog.getByLabel('端口').fill('5701')
  await dialog.getByRole('button', { name: '保存修改' }).click()
  await expect(page.locator('.node-row strong')).toHaveText('astrbot')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => requests.filter((request) => request.path === '/api/config' && request.method === 'PUT').length).toBe(2)
  const second = requests.filter((request) => request.path === '/api/config' && request.method === 'PUT')[1]
  expect(second.body.ob_networks).toHaveLength(2)
  expect(second.body.ob_networks[1]).toMatchObject({ type: 'http_server', name: 'astrbot', port: 5701, enabled: true })

  // 删除走确认弹窗；删空之后 WS 标签页回到「通用设置兜底」的那一行
  await page.getByRole('tab', { name: /WS 服务端/ }).click()
  await page.getByRole('button', { name: '删除 default' }).click()
  await page.getByRole('dialog', { name: '删除这个入口' }).getByRole('button', { name: '删除', exact: true }).click()
  await expect(page.getByText('通用设置生成的入口')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  expect(errors).toEqual([])
})

test('leaving the node editor with unsaved changes asks first', async ({ page }) => {
  const errors = await openPage(page, 'nodes')
  await expect(page.locator('.page-heading h1')).toHaveText('节点配置')
  // 改一个字段但不保存：顶栏出现未保存标记，导航要拦下来
  await page.getByLabel('客户端附件目录').fill('D:/wechat/unsaved')
  await expect(page.locator('.dirty-flag')).toBeVisible()

  await page.getByRole('button', { name: '消息记录', exact: false }).click()
  const prompt = page.getByRole('dialog', { name: '还有没保存的改动' })
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '留在本页' }).click()
  await expect(prompt).toHaveCount(0)
  // 取消后地址栏与页面都不动
  await expect(page).toHaveURL(/#\/nodes$/)
  await expect(page.locator('.page-heading h1')).toHaveText('节点配置')

  // 确认离开之后才真的切页
  await page.getByRole('button', { name: '消息记录', exact: false }).click()
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '放弃改动并离开' }).click()
  await expect(page).toHaveURL(/#\/messages$/)
  await expect(page.locator('.page-heading h1')).toHaveText('消息记录')
  expect(errors).toEqual([])
})

test('closing the node dialog with edits asks before dropping them', async ({ page }) => {
  const errors = await openPage(page, 'nodes')
  await page.getByRole('tab', { name: /HTTP 服务端/ }).click()
  await page.getByRole('button', { name: '新建HTTP 服务端' }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 HTTP 服务' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('名称').fill('半成品')
  await page.keyboard.press('Escape')

  const prompt = page.getByRole('dialog', { name: '放弃未保存的改动' })
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '取消' }).click()
  // 取消之后表单还在，填过的内容没丢
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel('名称')).toHaveValue('半成品')

  await page.keyboard.press('Escape')
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '放弃改动' }).click()
  await expect(dialog).toHaveCount(0)
  expect(errors).toEqual([])
})

test('prune keeps the day count inside the range the server accepts', async ({ page }) => {
  const requests = await mockApi(page)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/#/config?section=storage')
  await expect(page.getByText('条记录', { exact: false })).toBeVisible()
  const days = page.getByLabel('只保留最近多少天')
  await days.fill('0')
  await page.getByRole('button', { name: '清理旧记录' }).click()
  await expect(page.locator('.toast-item.error')).toContainText('保留天数要在 1 到 3650 之间')
  await expect(page.getByRole('dialog', { name: '按天数清理消息存档' })).toHaveCount(0)

  await days.fill('30')
  await page.getByRole('button', { name: '清理旧记录' }).click()
  await expect(page.getByRole('dialog', { name: '按天数清理消息存档' })).toBeVisible()
  await page.getByRole('dialog', { name: '按天数清理消息存档' }).getByRole('button', { name: '取消' }).click()
  expect(requests.some((r) => r.path === '/api/storage')).toBe(true)
  expect(errors).toEqual([])
})

test('messages filter, clear confirmation and log controls work', async ({ page }) => {
  await openPage(page, 'messages')
  await page.getByRole('searchbox', { name: '搜索消息' }).fill('不存在的消息')
  await expect(page.getByText('没有匹配的消息', { exact: true })).toBeVisible()
  await page.getByRole('searchbox', { name: '搜索消息' }).fill('读书会')
  await expect(page.locator('.message-item')).toHaveCount(2)
  await page.getByRole('button', { name: '清空当前列表', exact: true }).click()
  await expect(page.getByRole('button', { name: '再点一次：只清页面，存档不动' })).toBeVisible()
  await page.getByRole('button', { name: '再点一次：只清页面，存档不动' }).click()
  await expect(page.getByText('还没有消息', { exact: true })).toBeVisible()

  await page.goto('/#/logs')
  await page.getByRole('searchbox', { name: '搜索日志' }).fill('WebSocket')
  await expect(page.locator('.log-line')).toHaveCount(1)
  await page.getByRole('searchbox', { name: '搜索日志' }).fill('')
  await page.getByRole('checkbox', { name: '错误' }).uncheck()
  await expect(page.locator('.log-line')).toHaveCount(3)
})

test('send validation, modal keyboard handling and test action are wired', async ({ page }) => {
  const errors = await openPage(page, 'dev?tab=send')
  const requests = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/send') requests.push({ body: request.postDataJSON() })
  })
  await page.getByLabel('联系人 / 群名').fill('文件传输助手')
  await page.getByLabel('消息内容', { exact: true }).fill('浏览器测试消息')
  await expect(page.getByRole('button', { name: '发送测试消息', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '发送测试消息', exact: true }).click()
  await expect(page.locator('.toast-item.success')).toContainText('测试消息已发送')
  expect(requests[0]?.body).toEqual({ contact: '文件传输助手', text: '浏览器测试消息' })
  await page.goto('/#/desktop')
  await expect(page.getByText('测试环境未提供画面', { exact: true })).toBeVisible()
  await expect(page.locator('.shot')).toHaveCount(0)

  await page.goto('/#/dashboard')
  await page.getByRole('button', { name: '识别与绑定微信窗口', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '识别微信窗口' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '识别微信窗口' })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('wizard keeps the disclaimer tick across status polling', async ({ page }) => {
  await openPage(page, 'wizard')
  // 覆盖成「尚未确认」的初始状态，才能验证勾选不会被轮询清掉。
  await page.route('**/api/wizard/status', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      ok: true, current: 'disclaimer', disclaimer_accepted: false,
      db_key: { present: false }, image_key: { present: false }, manual_rva: 0,
      keytool_running: false, imagekey_running: false,
      steps: { disclaimer: { state: 'idle', message: '', log: [] }, dbkey: { state: 'idle', message: '', log: [] }, imagekey: { state: 'idle', message: '', log: [] }, verify: { state: 'idle', message: '', log: [] } },
    }),
  }))
  await page.reload()
  const checkbox = page.getByRole('checkbox', { name: /我已阅读并同意/ })
  await checkbox.check()
  // 轮询间隔 2 秒：等过两轮后勾选仍在，确认按钮仍可点。
  await page.waitForTimeout(2600)
  await expect(checkbox).toBeChecked()
  await expect(page.getByRole('button', { name: '确认并继续', exact: true })).toBeEnabled()
})

test('skip link moves focus without leaving the current page', async ({ page }) => {
  await openPage(page, 'logs')
  await page.locator('.skip-link').focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.page-heading h1')).toHaveText('运行日志')
  await expect(page.locator('#main-content')).toBeFocused()
})

test('config load failure blocks editing instead of showing empty defaults', async ({ page }) => {
  await mockApi(page)
  // 后注册的 route 先匹配，必须放在 mockApi 之后才能覆盖它的配置接口。
  await page.route('**/api/config', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ ok: false, error: '测试注入的读取失败' }) }))
  await page.goto('/#/config')
  await expect(page.getByRole('heading', { name: '无法读取配置' })).toBeVisible()
  await expect(page.getByText('测试注入的读取失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存', exact: true })).toHaveCount(0)
})

test('collapsed sidebar keeps accessible names on nav items', async ({ page }) => {
  await openPage(page)
  await page.getByRole('button', { name: '收起侧栏', exact: true }).click()
  await expect(page.locator('.desktop-sidebar')).toHaveClass(/collapsed/)
  await expect(page.getByRole('button', { name: '消息记录', exact: true })).toHaveCount(1)
})

test('restart asks for confirmation in an in-app dialog', async ({ page }) => {  const errors = await openPage(page)
  const controlCalls = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/control') controlCalls.push(request.postDataJSON())
  })

  await page.getByRole('button', { name: '重启桥接', exact: true }).click()
  const restart = page.getByRole('dialog', { name: '重启桥接服务' })
  await expect(restart).toBeVisible()
  await restart.getByRole('button', { name: '取消', exact: true }).click()
  await expect(restart).toHaveCount(0)
  expect(controlCalls).toEqual([])

  await page.getByRole('button', { name: '重启桥接', exact: true }).click()
  await page.getByRole('dialog', { name: '重启桥接服务' }).getByRole('button', { name: '重启', exact: true }).click()
  await expect.poll(() => controlCalls.length).toBe(1)
  expect(controlCalls[0]).toEqual({ action: 'restart' })
  await expect(page.getByRole('dialog', { name: '重启桥接服务' })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('missing WeChat install is called out instead of offering a dead start button', async ({ page }) => {
  await mockApi(page, { desktopStatus: { exe_found: false } })
  await page.addInitScript(() => localStorage.setItem('flandora-theme', 'light'))
  await page.goto('/#/desktop')
  await expect(page.locator('.page-heading h1')).toBeVisible()
  await expect(page.getByText('这台电脑上没找到微信', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '启动微信', exact: true })).toBeDisabled()
  await expect(page.getByText('没有可启动的微信', { exact: true })).toBeVisible()
})

test('password gate blocks the console until login succeeds', async ({ page }) => {
  await mockApi(page, { auth: { password: 'secret123' } })
  await page.goto('/#/dashboard')

  await expect(page.locator('.login-card h1')).toHaveText('进入控制台')
  await expect(page.locator('.page-heading h1')).toHaveCount(0)

  await page.getByLabel('密码').fill('wrong-pass')
  await page.getByRole('button', { name: '进入控制台' }).click()
  await expect(page.locator('.login-error')).toHaveText('密码不对')

  await page.getByLabel('密码').fill('secret123')
  await page.getByRole('button', { name: '进入控制台' }).click()
  await expect(page.locator('.page-heading h1')).toBeVisible()
  await expect(page.locator('.status-grid article')).toHaveCount(4)
})

test('settings about section hosts release checks and dashboard stays clean', async ({ page }) => {
  const errors = await openPage(page)
  await expect(page.getByRole('heading', { name: '版本与更新' })).toHaveCount(0)
  // 侧栏底部版本徽标直达「关于」分区
  await expect(page.getByText('Flandora · v1.0.0')).toBeVisible()
  await page.getByRole('button', { name: '查看版本与更新' }).click()
  await expect(page.getByRole('heading', { name: '版本与更新' })).toBeVisible()
  await expect(page.getByText('当前 1.0.0 · 源码运行')).toBeVisible()
  await expect(page.getByRole('link', { name: '仓库' })).toHaveAttribute('href', 'https://github.com/OMSociety/Flandora')
  await expect(page.getByRole('link', { name: '发行版' })).toBeVisible()
  await expect(page.getByRole('link', { name: '反馈问题' })).toBeVisible()
  await page.getByRole('button', { name: '检查更新', exact: true }).click()
  await expect(page.locator('.update-msg')).toHaveText('已经是最新版本')
  expect(errors).toEqual([])
})

test('access section guards the bind address and the restart prompt', async ({ page }) => {
  const requests = await mockApi(page)
  await page.goto('/#/config')
  await page.getByRole('button', { name: '安全与服务 面板访问、监听与备份' }).click()
  // 没设面板密码时，不允许把面板开到所有网卡上
  await expect(page.getByRole('radio', { name: /所有网卡/ })).toBeDisabled()
  await page.getByLabel('Web 面板端口').fill('4950')
  await page.getByRole('button', { name: '保存监听', exact: true }).click()
  await expect.poll(() => requests.filter((request) => request.path === '/api/config' && request.method === 'PUT').length).toBe(1)
  const saved = requests.find((request) => request.path === '/api/config' && request.method === 'PUT')
  expect(saved.body).toEqual({ web_port: 4950, web_bind_host: '127.0.0.1' })
  await page.getByRole('button', { name: '重启桥接服务' }).click()
  const dialog = page.getByRole('dialog', { name: '重启桥接服务' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(requests.filter((r) => r.path === '/api/control')).toEqual([])
  await page.getByRole('button', { name: '重启桥接服务' }).click()
  await page.getByRole('dialog', { name: '重启桥接服务' }).getByRole('button', { name: '重启', exact: true }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/control').length).toBe(1)
  expect(requests.find((r) => r.path === '/api/control').body).toEqual({ action: 'restart' })
})

test('access section installs a certificate and moves backups around', async ({ page }) => {
  const requests = await mockApi(page)
  await page.goto('/#/config?section=access')
  await expect(page.getByText('还没有证书')).toBeVisible()
  // 盘上没有证书、输入框也空着时，HTTPS 开关点不动
  await expect(page.getByRole('checkbox', { name: '用 HTTPS 提供面板' })).toBeDisabled()

  await page.getByLabel('证书 PEM').fill('-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----')
  await page.getByLabel('私钥 PEM').fill('-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----')
  await page.getByRole('button', { name: '保存证书', exact: true }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/config' && r.method === 'PUT').length).toBe(1)
  const certSave = requests.find((r) => r.path === '/api/config' && r.method === 'PUT')
  expect(certSave.body.webui_tls).toBe(false)
  expect(certSave.body.webui_tls_key).toContain('BEGIN PRIVATE KEY')
  await expect(page.getByText('证书已写入，重启桥接服务后生效。')).toBeVisible()

  await page.getByRole('button', { name: '导出备份' }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/config/backup').length).toBe(1)
  await expect(page.getByText('已导出 2 项配置', { exact: false })).toBeVisible()

  await page.setInputFiles('input.file-input', {
    name: 'flandora-config.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ version: '1.0.0', config: { web_port: 9000 } })),
  })
  const restoreDialog = page.getByRole('dialog', { name: '用这份备份覆盖配置' })
  await expect(restoreDialog).toBeVisible()
  await restoreDialog.getByRole('button', { name: '恢复', exact: true }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/config/restore').length).toBe(1)
  expect(requests.find((r) => r.path === '/api/config/restore').body).toEqual({
    config: { web_port: 9000 }, include_sensitive: 0,
  })
  await expect(page.getByText('已恢复 1 项配置', { exact: false })).toBeVisible()
})

test('storage section reports usage and runs the cleanup actions', async ({ page }) => {
  const requests = await mockApi(page)
  await page.goto('/#/config?section=storage')

  // 占用一览读的是服务端数出来的实况
  await expect(page.getByText('44 个文件', { exact: false })).toBeVisible()
  await expect(page.getByText('32 条', { exact: true })).toBeVisible()
  // 更新暂存是空的，按钮点不动
  await expect(page.getByRole('button', { name: '清空更新暂存' })).toBeDisabled()

  await page.getByRole('button', { name: '清理图片缓存' }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/storage/clean').length).toBe(1)
  expect(requests.find((r) => r.path === '/api/storage/clean').body).toEqual({ target: 'images' })
  await expect(page.getByText('已删除 20 个文件', { exact: false })).toBeVisible()

  // 清理解密缓存会先问一句，因为它会让收件卡一下
  await page.getByRole('button', { name: '清理解密缓存' }).click()
  const cacheDialog = page.getByRole('dialog', { name: '清理解密的微信数据库' })
  await expect(cacheDialog).toBeVisible()
  await cacheDialog.getByRole('button', { name: '清理', exact: true }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/storage/clean').length).toBe(2)
  expect(requests.filter((r) => r.path === '/api/storage/clean')[1].body).toEqual({ target: 'wx_cache' })

  // 日志策略改完立刻生效，不必重启；越界的值只提示，不发请求
  await page.getByLabel('单份上限（MB）').fill('0')
  await page.getByRole('button', { name: '保存日志策略' }).click()
  await expect(page.getByText('日志单份上限取 1–1024 MB')).toBeVisible()
  expect(requests.filter((r) => r.path === '/api/config' && r.method === 'PUT')).toEqual([])

  await page.getByLabel('单份上限（MB）').fill('4')
  await page.getByLabel('保留份数').fill('3')
  await page.getByRole('button', { name: '保存日志策略' }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/config' && r.method === 'PUT').length).toBe(1)
  expect(requests.find((r) => r.path === '/api/config' && r.method === 'PUT').body)
    .toEqual({ log_max_mb: 4, log_backup_count: 3 })

  // 消息存档：按天数清理先确认，压缩数据库直接跑
  await page.getByLabel('只保留最近多少天').fill('7')
  await page.getByRole('button', { name: '清理旧记录' }).click()
  const pruneDialog = page.getByRole('dialog', { name: '按天数清理消息存档' })
  await expect(pruneDialog).toBeVisible()
  await pruneDialog.getByRole('button', { name: '清理', exact: true }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/storage/messages').length).toBe(1)
  expect(requests.find((r) => r.path === '/api/storage/messages').body).toEqual({ action: 'prune', days: 7 })
  await expect(page.getByText('已删掉 4 条 7 天前的记录', { exact: false })).toBeVisible()

  await page.getByRole('button', { name: '压缩数据库' }).click()
  await expect.poll(() => requests.filter((r) => r.path === '/api/storage/messages').length).toBe(2)
  await expect(page.getByText('数据库已重写，释放 2.6 MB', { exact: false })).toBeVisible()
  expect(requests.filter((r) => r.path === '/api/storage/messages')[1].body).toEqual({ action: 'vacuum' })
})
