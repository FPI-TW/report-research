import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL(u => u.pathname === '/')   // 尚未 cutover
  await page.goto('/app/ask')
}

test('問答：送出 → 串流答案 + 資料來源；多輪追問', async ({ page }) => {
  await login(page)
  const box = page.getByPlaceholder('輸入你的問題…')
  await expect(box).toBeVisible()

  await box.fill('AI 伺服器供應鏈的受惠標的有哪些？')
  await box.press('Enter')
  // 使用者泡泡即時出現
  await expect(page.getByText('AI 伺服器供應鏈的受惠標的有哪些？')).toBeVisible()
  // 首次冷啟動 BGE-M3 + LLM：放寬逾時，等「資料來源 N」或「已思考」出現
  await expect(page.getByRole('button', { name: /資料來源 \d+/ })).toBeVisible({ timeout: 180_000 })

  // 開來源抽屜
  await page.getByRole('button', { name: /資料來源 \d+/ }).first().click()
  await expect(page.getByText('引用來源')).toBeVisible()
  await page.getByRole('button', { name: '關閉' }).click()

  // 多輪追問（等輸入框可用＝串流結束）
  await expect(box).toBeEnabled({ timeout: 180_000 })
  await box.fill('那散熱類股呢？')
  await box.press('Enter')
  await expect(page.getByText('那散熱類股呢？')).toBeVisible()
  await expect(page.getByRole('button', { name: /資料來源 \d+/ }).nth(1)).toBeVisible({ timeout: 180_000 })
})

test('離題 → 警示卡（Callout warning）', async ({ page }) => {
  await login(page)
  const box = page.getByPlaceholder('輸入你的問題…')
  await box.fill('今天台北天氣如何？')
  await box.press('Enter')
  // 離題回覆或（保守）任一助理輸出；以警示卡為主
  await expect(page.getByRole('alert')).toBeVisible({ timeout: 180_000 })
})
