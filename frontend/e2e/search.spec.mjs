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

test('/app/search 平價：表格/列表/分組切換 + 高亮 + 市場索引 drill-in', async ({
  page,
}) => {
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  // ── 認證（重複登入仍需 cookie，走相同流程）──────────────────────────────
  const { u, p } = creds()
  await page.goto(`${BASE}/app/search`)
  await expect(page).toHaveURL(/\/login\?next=/)
  await page.fill('input[name="username"]', u)
  await page.fill('input[name="password"]', p)
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/app\/search$/, { timeout: 10_000 })

  // ── 預設月份分組已渲染 ────────────────────────────────────────────────
  await expect(page.getByTestId('grouped-list')).toBeVisible({ timeout: 15_000 })

  // ── 切換表格檢視 ─────────────────────────────────────────────────────
  await page.getByRole('radio', { name: '表格' }).click()
  // TableView 以「報告名稱」欄位標頭為辨識點
  await expect(page.getByText('報告名稱')).toBeVisible({ timeout: 5_000 })

  // ── 點擊欄位標頭（排序）────────────────────────────────────────────────
  const dateHeader = page.getByRole('columnheader', { name: /日期/ })
  await dateHeader.click()
  // aria-sort 應從 none 變為 ascending 或 descending
  await expect(dateHeader).toHaveAttribute('aria-sort', /ascending|descending/)

  // ── 切回列表（group）+ 分組依市場 ───────────────────────────────────
  await page.getByRole('radio', { name: '列表' }).click()
  // 分組選擇器應出現（view=group 時才顯示）
  const groupInput = page.getByLabel('分組依據', { exact: true })
  await expect(groupInput).toBeVisible({ timeout: 3_000 })

  // 選「依市場」
  await groupInput.click()
  await page.getByRole('option', { name: '依市場' }).click()

  // 市場索引應出現
  await expect(page.getByTestId('market-index')).toBeVisible({ timeout: 5_000 })

  // ── 點一個市場進入 drill 檢視 ─────────────────────────────────────────
  const firstMarket = page.getByTestId('market-index-item').first()
  await firstMarket.click()
  await expect(page.getByTestId('drill-view')).toBeVisible({ timeout: 5_000 })
  // drill 標頭應有市場名稱
  const drillHeader = page.getByTestId('drill-header')
  await expect(drillHeader).toBeVisible()

  // ── 搜尋關鍵字並確認高亮（<mark>）存在 ──────────────────────────────
  await page.goto(`${BASE}/app/search`)
  await expect(page.getByTestId('grouped-list')).toBeVisible({ timeout: 15_000 })
  const searchInput = page.getByLabel('搜尋研報')
  await searchInput.fill('台積電')
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/search'), { timeout: 20_000 }),
    searchInput.press('Enter'),
  ])
  await expect(page.getByTestId('result-card').first()).toBeVisible({ timeout: 10_000 })
  // 若有命中片段，應存在 <mark> 高亮元素
  const markLocator = page.locator('mark')
  const markCount = await markLocator.count()
  // 高亮存在（> 0）或無片段（= 0，視語料而定），皆合法；有 mark 時確認非空
  if (markCount > 0) {
    await expect(markLocator.first()).not.toBeEmpty()
  }

  // ── 0 console error ───────────────────────────────────────────────────
  expect(errors).toEqual([])
})
