import { test, expect } from '@playwright/test'
import fs from 'node:fs'

// 預設對 make serve 的 :8097；控制端可用 ASK_BASE_URL 覆寫到非正式埠（如 :8098）實跑
const BASE =
  process.env.ASK_BASE_URL ??
  process.env.SEARCH_BASE_URL ??
  process.env.MONITOR_BASE_URL ??
  'http://localhost:8097'

function creds() {
  const env = fs.readFileSync(new URL('../../.env', import.meta.url), 'utf8')
  const get = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1]?.trim()
  return { u: get('REPORT_MARK_ACCESS_USERNAME'), p: get('REPORT_MARK_ACCESS_PASSWORD') }
}

async function login(page) {
  const { u, p } = creds()
  await page.goto(`${BASE}/app/ask`)
  if (page.url().includes('/login')) {
    await page.fill('input[name="username"]', u)
    await page.fill('input[name="password"]', p)
    await page.click('button[type="submit"]')
    await page.waitForURL(/\/app\/ask/, { timeout: 10_000 })
  }
}

test('ask：提問串流 + 來源 + 多輪', async ({ page }) => {
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  // ── Step 1: 認證 ──────────────────────────────────────────────────────────
  await login(page)
  await expect(page).toHaveURL(/\/app\/ask/, { timeout: 10_000 })

  // ── Step 2: 頁面基本元件渲染 ─────────────────────────────────────────────
  // 輸入框與新對話鈕應存在
  await expect(page.getByTestId('ask-input')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByTestId('ask-new')).toBeVisible()

  // ── Step 3: 提問並等待串流回答 ───────────────────────────────────────────
  await page.getByTestId('ask-input').fill('台積電最新展望如何？')
  const respPromise = page.waitForResponse(
    (r) => r.url().includes('/api/ask'),
    { timeout: 15_000 },
  )
  await page.getByTestId('ask-send').click()
  await respPromise

  // 串流答案（ask-answer data-testid）出現且有內容（最多等 90s 含思考時間）
  await expect(page.getByTestId('ask-answer').first()).not.toBeEmpty({ timeout: 90_000 })

  // ── Step 4: 來源（條件式）────────────────────────────────────────────────
  // 等動作列出現（代表串流完成）
  const actionsBar = page.getByTestId('ask-sources-toggle')
  const actionsVisible = await actionsBar.isVisible({ timeout: 5_000 }).catch(() => false)
  if (actionsVisible) {
    // 展開來源清單確認至少一筆來源
    await actionsBar.click()
    await expect(page.getByTestId('ask-src').first()).toBeVisible({ timeout: 5_000 })
  }

  // ── Step 5: 多輪：第二輪追問 ─────────────────────────────────────────────
  await page.getByTestId('ask-input').fill('請進一步說明其中的風險因素')
  const resp2 = page.waitForResponse(
    (r) => r.url().includes('/api/ask'),
    { timeout: 15_000 },
  )
  await page.getByTestId('ask-send').click()
  await resp2
  // 現在應有兩個 ask-answer（第一輪 + 第二輪）
  await expect(page.getByTestId('ask-answer').nth(1)).not.toBeEmpty({ timeout: 90_000 })

  // ── Step 6: 歷史側欄 ─────────────────────────────────────────────────────
  // 完成後側欄應出現至少一筆歷史項目（對話在後端 done 後 refresh 寫入）
  // 使用 poll 兜住 POST→refresh 異步延遲（≤10s）
  await expect
    .poll(
      async () => page.getByTestId('ask-hist-item').count(),
      { timeout: 10_000, message: '歷史側欄應出現至少一筆對話' },
    )
    .toBeGreaterThanOrEqual(1)

  // ── Step 7: 新對話鈕清空 thread ──────────────────────────────────────────
  await page.getByTestId('ask-new').click()
  await expect(page.getByTestId('ask-empty')).toBeVisible({ timeout: 3_000 })
  await expect(page.getByTestId('ask-input')).toBeVisible()

  // ── Step 8: 0 console error ──────────────────────────────────────────────
  expect(errors).toEqual([])
})
