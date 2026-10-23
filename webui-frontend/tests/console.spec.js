import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures.js'

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
  await expect(page.getByRole('heading', { name: '一眼看清桥接的每个环节。' })).toBeVisible()
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
  expect(errors).toEqual([])
})

test('all seven routes render and mobile layout has no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const errors = await openPage(page)
  await expect(page.locator('.desktop-sidebar')).toBeHidden()
  await page.screenshot({ path: '../.impeccable/review/mobile.png', fullPage: true })
  await page.getByRole('button', { name: '打开导航', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '控制台导航' })).toBeVisible()
  await page.screenshot({ path: '../.impeccable/review/mobile-navigation.png', fullPage: true })
  await page.getByRole('button', { name: '消息记录', exact: false }).click()
  await expect(page.getByRole('dialog', { name: '控制台导航' })).toHaveCount(0)

  for (const path of ['messages', 'logs', 'test', 'desktop', 'wizard', 'config']) {
    await page.goto(`/#/${path}`)
    await expect(page.locator('.page-intro h2')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    expect(overflow, `${path} has horizontal overflow`).toBe(false)
    await page.screenshot({ path: `../.impeccable/review/mobile-${path}.png`, fullPage: true })
  }
  expect(errors).toEqual([])
})

test('config saves edited values and exposes JSON validation', async ({ page }) => {
  const requests = await mockApi(page)
  await page.goto('/#/config')
  await page.getByLabel('自我称呼').fill('测试助手')
  await page.getByRole('button', { name: '保存配置', exact: true }).click()
  await expect.poll(() => requests.filter((request) => request.path === '/api/config' && request.method === 'PUT').length).toBe(1)
  const saved = requests.find((request) => request.path === '/api/config' && request.method === 'PUT')
  expect(saved.body.self_name).toBe('测试助手')
  expect(saved.body.bot_nicknames).toEqual(['小麦', '桥接助手'])
  await page.getByRole('button', { name: 'OneBot 连接', exact: false }).click()
  await expect(page.getByLabel('监听端口')).toHaveValue('7999')
  await page.getByRole('button', { name: '编辑原始 JSON', exact: true }).click()
  await page.getByLabel('原始 JSON 配置').fill('{invalid')
  await expect(page.getByRole('button', { name: '保存配置', exact: true })).toBeDisabled()
})

test('messages filter, clear confirmation and log controls work', async ({ page }) => {
  await openPage(page, 'messages')
  await page.getByRole('searchbox', { name: '搜索消息' }).fill('不存在的消息')
  await expect(page.getByText('没有匹配的消息', { exact: true })).toBeVisible()
  await page.getByRole('searchbox', { name: '搜索消息' }).fill('读书会')
  await expect(page.locator('.message-item')).toHaveCount(2)
  await page.getByRole('button', { name: '清空记录', exact: true }).click()
  await expect(page.getByRole('button', { name: '再次点击确认清空' })).toBeVisible()
  await page.getByRole('button', { name: '再次点击确认清空' }).click()
  await expect(page.getByText('还没有消息', { exact: true })).toBeVisible()

  await page.goto('/#/logs')
  await page.getByRole('searchbox', { name: '搜索日志' }).fill('WebSocket')
  await expect(page.locator('.log-line')).toHaveCount(1)
  await page.getByRole('searchbox', { name: '搜索日志' }).fill('')
  await page.getByRole('checkbox', { name: '错误' }).uncheck()
  await expect(page.locator('.log-line')).toHaveCount(3)
})

test('send validation, modal keyboard handling and test action are wired', async ({ page }) => {
  const errors = await openPage(page, 'test')
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
  await expect(page.getByRole('button', { name: '保存配置', exact: true })).toHaveCount(0)
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
