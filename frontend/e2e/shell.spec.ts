import { test, expect } from '@playwright/test'

test('登入頁對齊新設計', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByText('廷豐智能研報')).toBeVisible()
  await expect(page.getByRole('button', { name: '登入' })).toBeVisible()
})

test('登入後 /app/search 殼與側欄導覽可見', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL('**/app/search')
  await expect(page.getByText('檢索頁（Phase 1 實作）')).toBeVisible()
  await expect(page.getByRole('link', { name: /問答/ })).toBeVisible()
})
