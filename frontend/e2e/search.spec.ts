import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL(u => u.pathname === '/')      // Phase 1 尚未 cutover
  await page.goto('/app/search')
}

test('browse → 搜尋 → 表格檢視 → 開詳情 modal', async ({ page }) => {
  await login(page)

  // browse 載入（快，無嵌入）：搜尋框可見 + 結果 meta 出現
  await expect(page.getByLabel('搜尋研報')).toBeVisible()
  await expect(page.getByText(/共 .* 篇/)).toBeVisible({ timeout: 15_000 })

  // 搜尋（首次可能冷啟動 BGE-M3，放寬逾時）
  await page.getByLabel('搜尋研報').fill('台積電')
  await page.getByLabel('搜尋研報').press('Enter')
  await expect(page.getByText(/找到 .* 篇研報/)).toBeVisible({ timeout: 90_000 })

  // 切表格檢視 → 出現表頭「報告名稱」
  await page.getByRole('button', { name: '表格檢視' }).click()
  await expect(page.getByRole('columnheader', { name: /報告名稱/ })).toBeVisible()

  // 點第一列 → 詳情 modal 開啟
  await page.locator('tbody tr').first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15_000 })
})
