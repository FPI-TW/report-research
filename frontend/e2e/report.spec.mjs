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

/** 是否在 timeout 內出現（真的等待，而非 isVisible() 的立即快照）。 */
async function appearsWithin(locator, timeout) {
  try {
    await locator.waitFor({ state: 'visible', timeout })
    return true
  } catch {
    return false
  }
}

test('report：問答觸發深度研報 offer → 生成 → 下載 → 全文預覽', async ({ page }) => {
  // 深度研報生成（檢索+撰寫+PDF 排版）比單純問答慢得多，且 offer 是否出現由後端
  // report_gate 決定（非確定性）。無 playwright.config，全域 test timeout 需在此明確放寬，
  // 覆蓋「等答案完成 + offer 判定 + 生成 + 下載/全文」的總時長。
  // 控制端實測完整研報端到端 ~298s（claude CLI 思考時間致首個 token 遲至 184s 才出現），
  // 故報告完成等待與整體測試逾時都需放寬（540s / 900s）。
  test.setTimeout(900_000)
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  // ── Step 1: 認證 ──────────────────────────────────────────────────────────
  await login(page)
  await expect(page).toHaveURL(/\/app\/ask/, { timeout: 10_000 })

  // ── Step 2: 頁面基本元件渲染 ─────────────────────────────────────────────
  await expect(page.getByTestId('ask-input')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByTestId('ask-send')).toBeVisible()

  // ── Step 3: 提問（易觸發研報 offer 的問題）並等待串流回答完成 ─────────────
  await page.getByTestId('ask-input').fill('台積電最新展望如何？')
  const respPromise = page.waitForResponse(
    (r) => r.url().includes('/api/ask'),
    { timeout: 15_000 },
  )
  await page.getByTestId('ask-send').click()
  await respPromise

  // 串流答案出現且有內容（Haiku 串流實測 ~30-100s，留 150s 餘裕）
  await expect(page.getByTestId('ask-answer').first()).not.toBeEmpty({ timeout: 150_000 })

  // 輸入框重新可編輯 = streaming=false，代表本輪（含 offer 判定）已完全結束
  await expect(page.getByTestId('ask-input')).toBeEnabled({ timeout: 150_000 })

  // ── Step 4: 條件式處理研報 offer（非確定性，由 report_gate 決定）─────────
  const offerCard = page.getByTestId('report-offer')
  const offered = await appearsWithin(offerCard, 15_000)

  if (!offered) {
    console.log('[report e2e] no offer surfaced — skipping generation assertions')
    expect(errors).toEqual([])
    return
  }

  // ── Step 5: 接受 offer → 等待生成中 → 等待完成（放寬 timeout）────────────
  await page.getByTestId('report-offer-yes').click()
  await expect(page.getByTestId('report-generating')).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId('report-done')).toBeVisible({ timeout: 540_000 })

  // ── Step 6: 下載連結 ─────────────────────────────────────────────────────
  await expect(page.getByTestId('report-download')).toHaveAttribute(
    'href',
    /\/api\/report-doc\//,
  )

  // ── Step 7: 查看全文 ─────────────────────────────────────────────────────
  await page.getByTestId('report-viewfull').click()
  await expect(page.getByTestId('report-full-modal')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByTestId('report-full-body')).not.toBeEmpty({ timeout: 30_000 })

  // ── Step 8: 0 console error ──────────────────────────────────────────────
  expect(errors).toEqual([])
})
