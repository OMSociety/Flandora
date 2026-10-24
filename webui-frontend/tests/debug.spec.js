import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures'

test('debug page invokes an action and shows the response', async ({ page }) => {
  const requests = await mockApi(page)
  await page.goto('/#/debug')

  await expect(page.getByRole('button', { name: '执行' })).toBeVisible()
  await page.getByLabel('动作').selectOption('send_msg')
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
  await page.goto('/#/debug')

  await page.getByLabel('动作').selectOption('set_group_kick')
  await expect(page.getByText('微信 PC 客户端不提供对应接口')).toBeVisible()
})

test('message builder renders CQ preview', async ({ page }) => {
  await mockApi(page)
  await page.goto('/#/debug')

  await page.locator('.builder-row input').first().fill('你好，[世界]')
  await expect(page.getByLabel('CQ 码预览')).toHaveValue('你好，&#91;世界&#93;')
})
