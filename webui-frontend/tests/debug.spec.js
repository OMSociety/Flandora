import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures'

test('debug page invokes an action and shows the response', async ({ page }) => {
  const requests = await mockApi(page)
  await page.goto('/#/dev')

  await expect(page.getByRole('button', { name: '执行' })).toBeVisible()
  await page.getByLabel('动作', { exact: true }).selectOption('send_msg')
  await expect(page.getByText('发送一条消息')).toBeVisible()

  await page.getByRole('button', { name: '执行' }).click()
  await expect(page.getByText('成功 · 12 ms')).toBeVisible()
  await expect(page.locator('pre.code-block').first()).toContainText('"message_id": 41')

  const invoke = requests.find((r) => r.path === '/api/debug/invoke')
  expect(invoke.method).toBe('POST')
  expect(invoke.body.action).toBe('send_msg')
})

test('debug page shows unimplemented actions with their reason', async ({ page }) => {
  await mockApi(page)
  await page.goto('/#/dev')

  await page.getByLabel('动作', { exact: true }).selectOption('set_group_kick')
  await expect(page.getByText('微信 PC 客户端不提供对应接口')).toBeVisible()
})

test('message builder renders CQ preview', async ({ page }) => {
  await mockApi(page)
  await page.goto('/#/dev')

  await page.locator('.builder-row input').first().fill('你好，[世界]')
  await expect(page.getByLabel('CQ 码预览')).toHaveValue('你好，&#91;世界&#93;')
})

test('debug panel switches tabs and keeps the tab in the address', async ({ page }) => {
  await mockApi(page)
  await page.goto('/#/dev')

  await expect(page.getByRole('tab', { name: '动作调试' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('button', { name: '执行' })).toBeVisible()
  await expect(page.getByRole('button', { name: '发送测试消息', exact: true })).toHaveCount(0)

  await page.getByRole('tab', { name: '发送测试' }).click()
  await expect(page).toHaveURL(/#\/dev\?tab=send$/)
  await expect(page.getByRole('button', { name: '发送测试消息', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('tab', { name: '发送测试' })).toHaveAttribute('aria-selected', 'true')
})
