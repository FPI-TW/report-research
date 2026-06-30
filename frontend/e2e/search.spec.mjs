import { test, expect } from '@playwright/test'
import fs from 'node:fs'

// 預設對 make serve 的 :8097；控制端可用 SEARCH_BASE_URL 或 MONITOR_BASE_URL 覆寫到非正式埠實跑
const BASE =
  process.env.SEARCH_BASE_URL ?? process.env.MONITOR_BASE_URL ?? 'http://localhost:8097'

function creds() {
  const env = fs.readFileSync(new URL('../../.env', import.meta.url), 'utf8')
  const get = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1]?.trim()
  return { u: get('REPORT_MARK_ACCESS_USERNAME'), p: get('REPORT_MARK_ACCESS_PASSWORD') }
}

test('/app/search 平價：登入 → 月份分組渲染 → 搜尋 → 載入更多 → 0 console error', async ({
  page,
}) => {
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  // ── Step 1: 認證 ──────────────────────────────────────────────────────────
  const { u, p } = creds()
  await page.goto(`${BASE}/app/search`)
  // 未認證 → 導向 /login?next=/app/search
  await expect(page).toHaveURL(/\/login\?next=/)
  await page.fill('input[name="username"]', u)
  await page.fill('input[name="password"]', p)
  await page.click('button[type="submit"]')
  // 登入後返回 /app/search
  await expect(page).toHaveURL(/\/app\/search$/, { timeout: 10_000 })

  // ── Step 2: 預設月份分組渲染 ────────────────────────────────────────────
  // GroupedList（data-testid="grouped-list"）及至少一張 ResultCard（data-testid="result-card"）
  await expect(page.getByTestId('grouped-list')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('result-card').first()).toBeVisible({ timeout: 5_000 })

  // ── Step 3: 搜尋關鍵字 ──────────────────────────────────────────────────
  const searchInput = page.getByLabel('搜尋研報')
  await searchInput.fill('台積電')
  // 等後端回應（/api/search）再斷言，避免 flaky；同時兜住 debounce 和 Enter 觸發兩路
  const [_response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/search'), { timeout: 20_000 }),
    searchInput.press('Enter'),
  ])
  // 搜尋結果中至少有一張卡片
  await expect(page.getByTestId('result-card').first()).toBeVisible({ timeout: 10_000 })

  // ── Step 4: 載入更多（條件式）──────────────────────────────────────────
  const loadMoreBtn = page.getByTestId('load-more-btn')
  if (await loadMoreBtn.isVisible()) {
    // 記下目前卡片數，點擊後用 expect.poll 確認增加
    const beforeCount = await page.getByTestId('result-card').count()
    await loadMoreBtn.click()
    await expect
      .poll(
        async () => page.getByTestId('result-card').count(),
        { timeout: 15_000, message: '載入更多後卡片數量應增加' },
      )
      .toBeGreaterThan(beforeCount)
  }
  // 若不可見（結果不足一頁），跳過此斷言

  // ── Step 5: URL 可分享 ──────────────────────────────────────────────────
  // 搜尋後 URL 應含 ?q= 參數
  await expect(page).toHaveURL(/[?&]q=/)
  // 重新載入頁面後，搜尋框值應還原為「台積電」且結果仍在
  await page.reload()
  await expect(page.getByLabel('搜尋研報')).toHaveValue('台積電', { timeout: 15_000 })
  await expect(page.getByTestId('result-card').first()).toBeVisible({ timeout: 10_000 })

  // ── Step 6: 0 console error ─────────────────────────────────────────────
  // 放在所有互動之後；任何 console.error 皆視為失敗
  expect(errors).toEqual([])
})
