import { test, expect } from '@playwright/test'
import fs from 'node:fs'

// 預設對 make serve 的 :8097；控制端可用 MONITOR_BASE_URL 覆寫到非正式埠實跑
const BASE = process.env.MONITOR_BASE_URL ?? 'http://localhost:8097'

function creds() {
  const env = fs.readFileSync(new URL('../../.env', import.meta.url), 'utf8')
  const get = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1]?.trim()
  return { u: get('REPORT_MARK_ACCESS_USERNAME'), p: get('REPORT_MARK_ACCESS_PASSWORD') }
}

test('/app/monitor 平價：登入 → 輪詢 → 0 console error', async ({ page }) => {
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  const { u, p } = creds()
  await page.goto(`${BASE}/app/monitor`)
  // 未認證 → 導向 /login?next=/app/monitor
  await expect(page).toHaveURL(/\/login\?next=/)
  await page.fill('input[name="username"]', u)
  await page.fill('input[name="password"]', p)
  await page.click('button[type="submit"]')
  // 登入後返回 /app/monitor
  await expect(page).toHaveURL(/\/app\/monitor$/)
  await expect(page.getByText('研報導入監控')).toBeVisible()

  // 觀察 2 秒輪詢：~4.5 秒內至少 2 次 /api/progress
  let calls = 0
  page.on('requestfinished', (r) => r.url().includes('/api/progress') && calls++)
  await page.waitForTimeout(4500)
  expect(calls).toBeGreaterThanOrEqual(2)
  expect(errors).toEqual([])

  // 至少一個 tile 顯示具體數值（非「—」）；全「—」表示頁面炸掉
  const firstNumericTile = page.getByText(/^\d[\d,]*$/).first()
  await expect(firstNumericTile).toBeVisible({ timeout: 3000 })
  const tileVal = await firstNumericTile.textContent()
  expect(tileVal).toMatch(/[\d,]+/)
  expect(tileVal).not.toBe('—')
})
