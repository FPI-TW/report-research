import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL(u => u.pathname === '/')   // 尚未 cutover
  await page.goto('/app/monitor')
}

test('監控頁：標題 + KPI + LIVE + 處理管線', async ({ page }) => {
  await login(page)
  await expect(page.getByRole('heading', { name: '研報導入監控' })).toBeVisible({ timeout: 30_000 })
  // exact:true 避免子字串多重命中 strict-mode（如「已導入報告」vs 頁首「N 篇已導入」）
  await expect(page.getByText('已導入報告', { exact: true })).toBeVisible()
  await expect(page.getByText('處理管線', { exact: true })).toBeVisible()
  // LIVE 或（暫時斷線）重連中
  await expect(page.getByText(/LIVE|重連中/)).toBeVisible()
  // 至少一條管線列
  await expect(page.getByText('Web 服務', { exact: true })).toBeVisible()
})
