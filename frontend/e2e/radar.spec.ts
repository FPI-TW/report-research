import { expect, test, type Locator, type Page } from '@playwright/test'

const RADAR_URL = '/app/radar?market=TW&code=8046&window=90'

const stats = {
  total_reports: 8,
  total_chunks: 24,
  markets: [],
  instrument_types: [],
  report_types: [],
  username: 'analyst',
}

const dimensions = [
  ['outlook', '展望'],
  ['catalyst', '催化劑'],
  ['risk', '風險'],
  ['valuation', '估值'],
] as const

function change(index: number) {
  return {
    field: 'target_price',
    dimension: null,
    label: `目標價上修 ${index + 1}`,
    direction: 'up',
    prev_value: `US$${100_000 + index}`,
    curr_value: `US$${110_000 + index}`,
    pct_change: 10,
    comparable: true,
    reason_code: null,
    incomparable_reason: null,
  }
}

function radarEvent(index: number) {
  const number = String(index + 1).padStart(2, '0')
  return {
    broker: 'daiwa',
    broker_display: '大和',
    report_date: `2026-07-${String(13 - index).padStart(2, '0')}`,
    headline: `事件 ${number}`,
    changes: [change(index)],
    evidence: [`事件 ${number} 的可追溯引文`],
    report_link: {
      report_id: `event-report-${number}`,
      file_name: `event-${number}.pdf`,
      report_date: `2026-07-${String(13 - index).padStart(2, '0')}`,
      broker: 'daiwa',
      broker_display: '大和',
    },
  }
}

const thesis = dimensions.map(([dimension, dimensionDisplay], index) => ({
  dimension,
  dimension_display: dimensionDisplay,
  label: index === 0 ? 'strengthen' : 'stable',
  label_display: index === 0 ? '轉強' : '穩定',
  brokers_strengthen: index === 0 ? 1 : 0,
  brokers_weaken: 0,
  brokers_comparable: 1,
  coverage_note: null,
  sample_summary: null,
}))

// 模擬舊版 overview：只有 preview 與 total，刻意沒有
// recent_events_has_more / recent_events_next_offset。
const legacyOverview = {
  market: 'TW',
  market_display: '台股',
  instrument_code: '8046',
  instrument_name: '南電',
  window: '90',
  as_of: '2026-07-11',
  coverage: {
    state: 'ok',
    brokers_total: 2,
    brokers_extracted: 2,
    brokers_in_consensus: 2,
    reports_available: 8,
    note: '',
  },
  rating: {
    distribution: [
      { rating: 'buy', count: 1 },
      { rating: 'overweight', count: 1 },
      { rating: 'neutral', count: 0 },
      { rating: 'underweight', count: 0 },
      { rating: 'sell', count: 0 },
    ],
    bullish: 2,
    neutral: 0,
    bearish: 0,
    unknown: 0,
    total_rated: 2,
    median_rating: 'overweight',
    upgrades: 1,
    downgrades: 0,
    unchanged: 1,
  },
  target_price: {
    primary_currency: 'USD',
    groups: [{
      currency: 'USD',
      median: 123_456,
      q1: 120_000,
      q3: 125_000,
      low: 110_000,
      high: 130_000,
      count: 2,
      revision_pct: 10,
      revision_direction: 'up',
    }],
    note: null,
  },
  eps: {
    primary: {
      fiscal_year: 2027,
      period: 'FY',
      currency: 'TWD',
      unit: 'per_share',
      median: 19.69,
      count: 2,
      revision_pct: 4.2,
      revision_direction: 'up',
    },
    groups: [],
  },
  thesis,
  recent_events: [radarEvent(0), radarEvent(1), radarEvent(2)],
  recent_events_total: 13,
  brokers: [{
    broker: 'daiwa',
    broker_display: '大和',
    latest_rating: 'buy',
    latest_rating_raw: 'Buy',
    latest_target_price: 120_000,
    latest_target_currency: 'USD',
    latest_eps_value: 19.69,
    latest_eps_fy: 2027,
    latest_eps_period: 'FY',
    latest_eps_currency: 'TWD',
    latest_eps_unit: 'per_share',
    latest_report_date: '2026-07-11',
    report_link: {
      report_id: 'report-new',
      file_name: 'daiwa-new.pdf',
      report_date: '2026-07-11',
      broker: 'daiwa',
      broker_display: '大和',
    },
    recent_change_label: '目標價上修',
    recent_change_direction: 'up',
    stale: false,
    has_history: true,
  }],
  notes: [],
}

function snapshotThesis(suffix: string) {
  return dimensions.map(([dimension, dimensionDisplay]) => ({
    dimension,
    dimension_display: dimensionDisplay,
    stance: 'positive',
    summary: `${dimensionDisplay}${suffix}`,
    evidence: dimension === 'outlook' ? `AI 伺服器需求${suffix}` : null,
  }))
}

const brokerHistory = {
  market: 'TW',
  instrument_code: '8046',
  broker: 'daiwa',
  broker_display: '大和',
  window: '90',
  as_of: '2026-07-11',
  current_rating: 'buy',
  report_count: 2,
  snapshots: [
    {
      report_id: 'report-new',
      report_date: '2026-07-11',
      in_window: true,
      rating: 'buy',
      rating_raw: 'Buy',
      target_price: 120_000,
      target_currency: 'USD',
      eps: [],
      primary_eps: {
        fiscal_year: 2027,
        period: 'FY',
        currency: 'TWD',
        unit: 'per_share',
        median: 19.69,
        count: 1,
        revision_pct: null,
        revision_direction: 'none',
      },
      thesis: snapshotThesis('續強'),
      extraction_status: 'valid',
      report_link: {
        report_id: 'report-new',
        file_name: 'daiwa-new.pdf',
        report_date: '2026-07-11',
        broker: 'daiwa',
        broker_display: '大和',
      },
    },
    {
      report_id: 'report-old',
      report_date: '2026-06-01',
      in_window: true,
      rating: 'overweight',
      rating_raw: 'Outperform',
      target_price: 110_000,
      target_currency: 'USD',
      eps: [],
      primary_eps: null,
      thesis: snapshotThesis('穩健'),
      extraction_status: 'valid',
      report_link: {
        report_id: 'report-old',
        file_name: 'daiwa-old.pdf',
        report_date: '2026-06-01',
        broker: 'daiwa',
        broker_display: '大和',
      },
    },
  ],
  diffs: [
    {
      from_report_id: 'report-old',
      from_report_date: '2026-06-01',
      to_report_date: '2026-07-11',
      changes: [change(0)],
      has_prior_report: true,
      has_prior_comparable: true,
      note: null,
    },
    {
      from_report_id: null,
      from_report_date: null,
      to_report_date: '2026-06-01',
      changes: [],
      has_prior_report: false,
      has_prior_comparable: false,
      note: '沒有前次研報',
    },
  ],
  coverage_state: 'ok',
}

async function login(page: Page) {
  await page.goto('/login')
  if (new URL(page.url()).pathname !== '/login') return

  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await Promise.all([
    page.waitForURL(url => url.pathname === '/' || url.pathname.startsWith('/app/')),
    page.getByRole('button', { name: '登入' }).click(),
  ])
}

async function mockRadarShell(page: Page) {
  await page.route(url => url.pathname === '/api/stats', route => route.fulfill({ json: stats }))
  await page.route(
    url => url.pathname === '/api/instrument/8046/radar',
    route => route.fulfill({ json: legacyOverview }),
  )
}

async function openRadar(page: Page) {
  await login(page)
  await mockRadarShell(page)
  await page.goto(RADAR_URL)
  await expect(page.getByRole('heading', { name: '南電' })).toBeVisible()
}

async function boxes(locator: Locator) {
  return locator.evaluateAll(nodes => nodes.map(node => {
    const rect = node.getBoundingClientRect()
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
  }))
}

async function expectTouchTarget(locator: Locator, name: string) {
  await expect(locator, `${name} 應可見`).toBeVisible()
  const box = await locator.boundingBox()
  expect(box, `${name} 應有可量測的 hit area`).not.toBeNull()
  expect(box!.width, `${name} 寬度`).toBeGreaterThanOrEqual(44)
  expect(box!.height, `${name} 高度`).toBeGreaterThanOrEqual(44)
}

test.describe('Radar contract', () => {
  test('390x844 維持 KPI 2+1、四向觀點 2x2、長合法值與 44px 操作契約', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await openRadar(page)

    const keyFigures = page.getByLabel('關鍵數字')
    const keyFigureCards = keyFigures.locator(':scope > div')
    await expect(keyFigureCards).toHaveCount(3)
    const keyFigureBoxes = await boxes(keyFigureCards)
    expect(Math.abs(keyFigureBoxes[0].y - keyFigureBoxes[1].y)).toBeLessThanOrEqual(1)
    expect(keyFigureBoxes[1].x).toBeGreaterThan(keyFigureBoxes[0].x)
    expect(keyFigureBoxes[2].y).toBeGreaterThan(keyFigureBoxes[0].y + keyFigureBoxes[0].height - 1)
    expect(Math.abs(keyFigureBoxes[2].x - keyFigureBoxes[0].x)).toBeLessThanOrEqual(1)
    expect(Math.abs(
      (keyFigureBoxes[2].x + keyFigureBoxes[2].width)
        - (keyFigureBoxes[1].x + keyFigureBoxes[1].width),
    )).toBeLessThanOrEqual(1)

    const longValue = keyFigures.getByText('US$123,456', { exact: true })
    await expect(longValue).toBeVisible()
    const longValueMetrics = await longValue.evaluate(node => {
      const rect = node.getBoundingClientRect()
      const card = node.parentElement!.getBoundingClientRect()
      return {
        clientWidth: node.clientWidth,
        scrollWidth: node.scrollWidth,
        left: rect.left,
        right: rect.right,
        cardLeft: card.left,
        cardRight: card.right,
      }
    })
    expect(longValueMetrics.scrollWidth).toBeLessThanOrEqual(longValueMetrics.clientWidth + 1)
    expect(longValueMetrics.left).toBeGreaterThanOrEqual(longValueMetrics.cardLeft - 1)
    expect(longValueMetrics.right).toBeLessThanOrEqual(longValueMetrics.cardRight + 1)

    const medianNeedle = page.getByText('中位', { exact: true }).locator('..')
    const medianPosition = await medianNeedle.evaluate(node => {
      const needle = node.getBoundingClientRect()
      const track = node.parentElement!.querySelector('[class*="dist"]')!.getBoundingClientRect()
      return {
        needleCenter: needle.left + needle.width / 2,
        expected: track.left + track.width * 0.3,
      }
    })
    expect(Math.abs(medianPosition.needleCenter - medianPosition.expected)).toBeLessThanOrEqual(1)

    const thesisCells = page.getByLabel('四向觀點').locator(':scope > article')
    await expect(thesisCells).toHaveCount(4)
    const thesisBoxes = await boxes(thesisCells)
    expect(Math.abs(thesisBoxes[0].y - thesisBoxes[1].y)).toBeLessThanOrEqual(1)
    expect(Math.abs(thesisBoxes[2].y - thesisBoxes[3].y)).toBeLessThanOrEqual(1)
    expect(thesisBoxes[1].x).toBeGreaterThan(thesisBoxes[0].x)
    expect(thesisBoxes[2].y).toBeGreaterThan(thesisBoxes[0].y + thesisBoxes[0].height - 1)
    expect(Math.abs(thesisBoxes[2].x - thesisBoxes[0].x)).toBeLessThanOrEqual(1)
    expect(Math.abs(thesisBoxes[3].x - thesisBoxes[1].x)).toBeLessThanOrEqual(1)

    const overflow = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }))
    expect(overflow.document).toBeLessThanOrEqual(overflow.viewport)
    expect(overflow.body).toBeLessThanOrEqual(overflow.viewport)

    const windowControls = page.getByRole('radio')
    await expect(windowControls).toHaveCount(4)
    for (let index = 0; index < 4; index += 1) {
      await expectTouchTarget(windowControls.nth(index), `窗期控制 ${index + 1}`)
    }
    await expectTouchTarget(page.getByRole('button', { name: '標的' }), '麵包屑返回')
    await expectTouchTarget(page.getByRole('button', { name: '查看全部 13 項' }), '查看全部事件')
    await expectTouchTarget(page.getByRole('button', { name: /原始研報/ }).first(), '事件原始研報')
    await expectTouchTarget(page.getByRole('button', { name: /^大和 · Buy/ }), '券商歷程展開')
    await expectTouchTarget(page.getByRole('link', { name: '觀點' }), '行動版觀點導覽')
  })

  test('舊 overview 缺少 has_more metadata 時仍依 total 載入完整 13 筆事件', async ({ page }) => {
    const requestedQueries: Array<Record<string, string | null>> = []
    await page.route(
      url => url.pathname === '/api/instrument/8046/radar/events',
      async route => {
        const url = new URL(route.request().url())
        requestedQueries.push({
          market: url.searchParams.get('market'),
          window: url.searchParams.get('window'),
          limit: url.searchParams.get('limit'),
          offset: url.searchParams.get('offset'),
        })
        const offset = Number(url.searchParams.get('offset') || '0')
        const items = offset === 0
          ? Array.from({ length: 12 }, (_, index) => radarEvent(index))
          : [radarEvent(12)]
        await route.fulfill({
          json: {
            market: 'TW',
            instrument_code: '8046',
            window: '90',
            as_of: '2026-07-11',
            total: 13,
            limit: 12,
            offset,
            has_more: offset === 0,
            next_offset: offset === 0 ? 12 : null,
            items,
          },
        })
      },
    )
    await openRadar(page)

    const expand = page.getByRole('button', { name: '查看全部 13 項' })
    await expect(expand).toHaveAttribute('aria-expanded', 'false')
    await expand.click()

    const eventFeed = page.locator('#radar-recent-events')
    await expect(eventFeed.locator(':scope > article')).toHaveCount(12)
    expect(requestedQueries).toEqual([
      { market: 'TW', window: '90', limit: '12', offset: '0' },
    ])
    await page.getByRole('button', { name: '載入更多（尚有 1 項）' }).click()

    await expect(eventFeed.locator(':scope > article')).toHaveCount(13)
    await expect(page.getByText('事件 13', { exact: true })).toBeVisible()
    expect(requestedQueries).toEqual([
      { market: 'TW', window: '90', limit: '12', offset: '0' },
      { market: 'TW', window: '90', limit: '12', offset: '12' },
    ])
    await expect(page.getByRole('button', { name: /載入更多/ })).toHaveCount(0)
  })

  test('券商歷程可展開，且日期、證據與原始研報入口可追溯', async ({ page }) => {
    const brokerRequests: string[] = []
    await page.route(
      url => url.pathname === '/api/instrument/8046/radar/brokers/daiwa',
      async route => {
        brokerRequests.push(route.request().url())
        await route.fulfill({ json: brokerHistory })
      },
    )
    await openRadar(page)

    const expand = page.getByTestId('broker-row-daiwa')
    await expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(brokerRequests).toHaveLength(0)
    await expand.click()

    await expect(expand).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('heading', { name: '大和觀點歷程' }).first()).toBeVisible()
    expect(brokerRequests).toHaveLength(1)
    const historyUrl = new URL(brokerRequests[0])
    expect(historyUrl.pathname).toBe('/api/instrument/8046/radar/brokers/daiwa')
    expect(historyUrl.searchParams.get('market')).toBe('TW')
    expect(historyUrl.searchParams.get('window')).toBe('90')

    await expect(page.getByText('最新研報（2026/07/11）').first()).toBeVisible()
    await expect(page.getByText('前次可比較研報（2026/06/01）').first()).toBeVisible()
    await expect(page.getByText(/AI 伺服器需求續強/).first()).toBeVisible()
    await expect(page.getByRole('button', { name: '查看原始研報' }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: '收合' }).first()).toBeVisible()
  })
})
