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
  // 兩輪 Haiku 串流各約 30-100s（含思考、追問另含 condense），遠超 Playwright 預設 30s 全域 test timeout；
  // 無 playwright.config 故在此明確放寬，覆蓋「等第一輪結束 + 2× 150s 答案等待 + 多輪 + poll」的總時長。
  test.setTimeout(540_000)
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

  // 串流答案（ask-answer data-testid）出現且有內容（含思考時間；Haiku 串流實測 ~30-100s，留 150s 餘裕）
  await expect(page.getByTestId('ask-answer').first()).not.toBeEmpty({ timeout: 150_000 })

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
  // 追問前必須等第一輪串流「完全結束」：AskComposer 在 streaming 時 disabled，
  // 而 Step 3 僅等到首個 token 渲染（此時仍在串流）。輸入框重新可編輯 = streaming=false。
  await expect(page.getByTestId('ask-input')).toBeEnabled({ timeout: 150_000 })
  await page.getByTestId('ask-input').fill('請進一步說明其中的風險因素')
  const resp2 = page.waitForResponse(
    (r) => r.url().includes('/api/ask'),
    { timeout: 15_000 },
  )
  await page.getByTestId('ask-send').click()
  await resp2
  // 第二輪問題泡泡應出現（多輪：同一對話內第二輪獨立渲染）
  await expect(page.getByTestId('ask-q')).toHaveCount(2)
  // 第二輪「回應」可能是正常答案（第二個 ask-answer 非空）或離題/無語料卡（ask-notice）——
  // 追問經多輪 condense_and_classify 由後端判定，分類非確定性；前端只需正確渲染其中一種。
  // 故斷言：第二輪產生了非空回應（答案或 notice），以 toPass 輪詢兜住串流延遲。
  await expect(async () => {
    const ans2 = page.getByTestId('ask-answer').nth(1)
    const ans2Text = (await ans2.count()) > 0 ? (await ans2.textContent()) || '' : ''
    const noticeCount = await page.getByTestId('ask-notice').count()
    expect(ans2Text.length > 0 || noticeCount > 0).toBe(true)
  }).toPass({ timeout: 150_000 })

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
