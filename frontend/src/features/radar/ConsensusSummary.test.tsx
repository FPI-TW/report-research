import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BrokerSummary, Coverage } from '../../lib/radarSchemas'
import { ConsensusSummary } from './ConsensusSummary'
import { CoverageStrip } from './CoverageStrip'
import {
  axisPosition, buildEpsSeries, buildTargetSeries, epsBases, niceScale, pickTargetCurrency,
  reportFreshness,
} from './consensusSeries'

/**
 * 這一檔同時放純函式與元件測試，是刻意的：本 repo 有過「只多一個測試檔就把
 * App.test.tsx 推過 5s 門檻」的實測紀錄（PR #173），所以不為了整齊多開檔案。
 */

const TODAY = new Date(2026, 7, 6) // 2026-08-06（當地日曆日，避免 CI 走 UTC 時翻紅）

/**
 * 元件內部的新鮮度是**相對於今天**算的（`reportFreshness` 預設 `new Date()`），
 * 所以 fixture 的日期一律用相對值。寫死 `2026-06-01` ＋ 斷言「90 天內」的版本，
 * 會在 2026-08-31 那天無預警轉紅，而且症狀看起來像功能壞掉——
 * `BrokerTimeline.test.tsx` 的 `daysAgo()` 就是為同一件事存在的。
 */
function daysAgo(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() - days)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}
const shown = (iso: string) => iso.replaceAll('-', '/')

const KGI_DATE = daysAgo(66) // 90 天內
const FRESH_DATE = daysAgo(17)
const OLD_DATE = daysAgo(400) // 超過 90 天

function broker(partial: Partial<BrokerSummary> = {}): BrokerSummary {
  return {
    broker: 'kgi', broker_display: '凱基',
    latest_rating: 'buy', latest_rating_raw: 'Buy',
    latest_target_price: 478, latest_target_currency: 'TWD',
    latest_eps_value: 8.75, latest_eps_fy: 2026, latest_eps_period: 'FY',
    latest_eps_currency: 'TWD', latest_eps_unit: 'per_share',
    latest_report_date: KGI_DATE,
    report_link: {
      report_id: 'r-kgi', file_name: 'kgi.pdf',
      report_date: KGI_DATE, broker: 'kgi', broker_display: '凱基',
    },
    recent_change_label: null,
    recent_change_direction: 'none',
    stale: false, has_history: true,
    ...partial,
  }
}

/** 附圖那一組資料：四家券商、目標價 205/210/280/478、三家 FY26E＋一家 FY25E。 */
function sampleBrokers(): BrokerSummary[] {
  return [
    broker(),
    broker({
      broker: 'psc', broker_display: '統一',
      latest_target_price: 205, latest_eps_value: 5.96,
      latest_report_date: FRESH_DATE,
      report_link: { report_id: 'r-psc', broker: 'psc' },
    }),
    broker({
      broker: 'sinopac', broker_display: '永豐',
      latest_target_price: 280, latest_eps_value: 7,
      latest_report_date: FRESH_DATE,
      report_link: { report_id: 'r-sinopac', broker: 'sinopac' },
    }),
    broker({
      broker: 'yuanta', broker_display: '元大',
      latest_target_price: 210, latest_eps_value: 6.4, latest_eps_fy: 2025,
      latest_report_date: FRESH_DATE,
      report_link: { report_id: 'r-yuanta', broker: 'yuanta' },
    }),
  ]
}

function mount(brokers: BrokerSummary[], onViewBroker = vi.fn()) {
  render(
    <ConsensusSummary
      rating={{
        distribution: [
          { rating: 'buy', count: 4 },
          { rating: 'overweight', count: 0 },
          { rating: 'neutral', count: 0 },
          { rating: 'underweight', count: 0 },
          { rating: 'sell', count: 0 },
        ],
        bullish: 4, neutral: 0, bearish: 0, unknown: 0,
        total_rated: 4, median_rating: 'buy',
        upgrades: 0, downgrades: 0, unchanged: 4,
      }}
      targetCurrencyHint="TWD"
      brokers={brokers}
      window="90"
      onViewBroker={onViewBroker}
    />,
  )
  return { onViewBroker }
}

describe('niceScale', () => {
  it.each([
    ['附圖的目標價', [205, 210, 280, 478], 200, 500, [200, 300, 400, 500]],
    ['附圖的 EPS', [5.96, 7, 8.75], 5, 9, [5, 6, 7, 8, 9]],
  ] as const)('%s 產生整數刻度', (_label, values, min, max, ticks) => {
    const scale = niceScale([...values])
    expect(scale).toEqual({ min, max, ticks: [...ticks] })
  })

  it('單一資料點給對稱假範圍，讓點落在正中央而不是 0/0', () => {
    const scale = niceScale([478])!
    expect(scale.min).toBeLessThan(478)
    expect(scale.max).toBeGreaterThan(478)
    expect(axisPosition(478, scale)).toBeGreaterThan(0)
    expect(axisPosition(478, scale)).toBeLessThan(100)
  })

  it('空陣列回 null（沒有資料就不該畫出一條憑空的軸）', () => {
    expect(niceScale([])).toBeNull()
  })

  it('刻度不帶浮點誤差', () => {
    const scale = niceScale([0.1, 0.5])!
    for (const tick of scale.ticks) expect(String(tick)).not.toMatch(/\d{8}/)
  })
})

describe('reportFreshness', () => {
  it.each([
    ['2026-07-20', 'recent'],
    ['2026-01-01', 'stale'],
    ['', 'unknown'],
    [null, 'unknown'],
  ] as const)('%s → %s', (iso, expected) => {
    expect(reportFreshness(iso, TODAY)).toBe(expected)
  })

  it('日期不明**不算**新鮮：無法佐證它落在 90 天內', () => {
    // isReportStale 對解析不出來的日期回 false（「不知道日期不等於零天」），
    // 那個回退對「要不要顯示過期提示」是安全的，拿來當「90 天內」的證據就是憑空斷言。
    expect(reportFreshness('', TODAY)).not.toBe('recent')
  })
})

describe('buildTargetSeries', () => {
  it('每家券商一個點，依數值排序，不做任何聚合', () => {
    const series = buildTargetSeries(sampleBrokers(), 'TWD', TODAY)
    expect(series.points.map(p => p.value)).toEqual([205, 210, 280, 478])
    expect(series.points.map(p => p.broker)).toEqual(['統一', '元大', '永豐', '凱基'])
  })

  it('幣別不同者退成「未納入目前口徑」而不是被換算或丟棄', () => {
    const series = buildTargetSeries([
      broker(),
      broker({ broker: 'gs', broker_display: '高盛', latest_target_currency: 'USD', latest_target_price: 15 }),
    ], 'TWD', TODAY)

    expect(series.points).toHaveLength(1)
    expect(series.excluded).toEqual([{ key: 'gs', broker: '高盛', note: 'USD，未納入目前口徑' }])
  })

  it('有價無幣別時不猜成主要幣別', () => {
    // 擷取層允許「有價無幣別」（signal_extract 只加一條 note），猜一個等於捏造。
    const series = buildTargetSeries(
      [broker({ latest_target_currency: null })], 'TWD', TODAY,
    )
    expect(series.points).toHaveLength(0)
    expect(series.excluded[0].note).toBe('未標示幣別，未納入目前口徑')
  })

  it('沒有目標價的券商只計入 missing，不佔一列', () => {
    const series = buildTargetSeries(
      [broker(), broker({ broker: 'x', broker_display: 'X', latest_target_price: null })],
      'TWD', TODAY,
    )
    expect(series.points).toHaveLength(1)
    expect(series.excluded).toHaveLength(0)
    expect(series.missing).toBe(1)
  })
})

describe('pickTargetCurrency', () => {
  it('後端主要幣別在券商之間存在時優先採用', () => {
    expect(pickTargetCurrency(sampleBrokers(), 'TWD')).toBe('TWD')
  })

  it('後端主要幣別在這批券商裡不存在時退回眾數，不硬套', () => {
    const brokers = [
      broker({ latest_target_currency: 'USD' }),
      broker({ broker: 'b', latest_target_currency: 'USD' }),
      broker({ broker: 'c', latest_target_currency: 'HKD' }),
    ]
    expect(pickTargetCurrency(brokers, 'TWD')).toBe('USD')
  })
})

describe('epsBases / buildEpsSeries', () => {
  it('口徑依涵蓋券商數排序，預設落在最多人給的那一個', () => {
    const bases = epsBases(sampleBrokers())
    expect(bases.map(b => [b.label, b.count])).toEqual([['FY26E', 3], ['FY25E', 1]])
  })

  it('同名口徑（同 FY 不同幣別）才補幣別去歧義', () => {
    const bases = epsBases([
      broker(),
      broker({ broker: 'gs', latest_eps_currency: 'USD' }),
    ])
    expect(bases.map(b => b.label).sort()).toEqual(['FY26E · TWD · per_share', 'FY26E · USD · per_share'])
  })

  it('不同財年不混畫，退成「未納入目前期間」', () => {
    const bases = epsBases(sampleBrokers())
    const series = buildEpsSeries(sampleBrokers(), bases[0], TODAY)

    expect(series.points.map(p => p.broker)).toEqual(['統一', '永豐', '凱基'])
    expect(series.excluded).toEqual([{ key: 'yuanta', broker: '元大', note: 'FY25E，未納入目前期間' }])
  })

  it('財年相同但幣別不同時說的是「口徑」不是「期間」', () => {
    const bases = epsBases([
      broker(),
      broker({ broker: 'gs', broker_display: '高盛', latest_eps_currency: 'USD' }),
    ])
    const series = buildEpsSeries(
      [broker(), broker({ broker: 'gs', broker_display: '高盛', latest_eps_currency: 'USD' })],
      bases.find(b => b.currency === 'TWD')!,
      TODAY,
    )
    expect(series.excluded[0].note).toContain('未納入目前口徑')
  })
})

describe('ConsensusSummary', () => {
  it('兩張點圖各家一個點，且畫面上沒有中位數／平均／區間', () => {
    mount(sampleBrokers())

    expect(screen.getByTestId('target-dot-kgi')).toBeInTheDocument()
    expect(screen.getByTestId('eps-dot-kgi')).toBeInTheDocument()
    for (const banned of ['中位數', '平均', '區間']) {
      expect(screen.queryByText(new RegExp(banned))).not.toBeInTheDocument()
    }
  })

  it('點的位置由 niceScale 決定，同一張圖用同一個座標系', () => {
    mount(sampleBrokers())
    // 200–500 的軸：205 → 1.67%、478 → 92.67%。jsdom 沒有版面，所以驗的是
    // 被寫進 inline style 的那個百分比（既有圖表元件也都是這樣驗的）。
    expect(screen.getByTestId('target-pos-psc')).toHaveStyle({ left: `${(5 / 300) * 100}%` })
    expect(screen.getByTestId('target-pos-kgi')).toHaveStyle({ left: `${(278 / 300) * 100}%` })
  })

  it('點的 aria-label 同時講出券商、數值、日期與新鮮度（不只靠顏色）', () => {
    mount(sampleBrokers())
    const dot = screen.getByTestId('target-dot-kgi')
    const description = `凱基，NT$478，報告日期 ${shown(KGI_DATE)}，90 天內`

    expect(dot).toHaveAccessibleName(description)
    // title 必須掛在外層 span：掛在按鈕上會同時成為可及描述，讀屏把同一句唸兩次。
    expect(dot).not.toHaveAttribute('title')
    expect(dot).not.toHaveAccessibleDescription()
    expect(screen.getByTestId('target-pos-kgi')).toHaveAttribute('title', description)
  })

  it('點選同一家券商時兩張圖與詳情面板同步，再點一次取消', () => {
    mount(sampleBrokers())

    fireEvent.click(screen.getByTestId('target-dot-kgi'))
    expect(screen.getByTestId('target-dot-kgi')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('eps-dot-kgi')).toHaveAttribute('aria-pressed', 'true')

    const panel = screen.getByTestId('consensus-selected')
    expect(within(panel).getByText('凱基')).toBeInTheDocument()
    expect(within(panel).getByText('NT$478')).toBeInTheDocument()
    expect(within(panel).getByText('NT$8.75／股')).toBeInTheDocument()
    expect(within(panel).getByText(shown(KGI_DATE))).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('eps-dot-kgi'))
    expect(screen.queryByTestId('consensus-selected')).not.toBeInTheDocument()
  })

  it('選到的券商沒有目前口徑的 EPS 時顯示未提供，不拿別的年度充數', () => {
    mount(sampleBrokers())
    fireEvent.click(screen.getByTestId('target-dot-yuanta'))

    const panel = screen.getByTestId('consensus-selected')
    expect(within(panel).getByText('FY26E EPS')).toBeInTheDocument()
    expect(within(panel).getByText('未提供')).toBeInTheDocument()
    expect(within(panel).getByText('FY25E，未納入目前期間')).toBeInTheDocument()
    expect(within(panel).queryByText(/6\.4/)).not.toBeInTheDocument()
  })

  it('「查看券商觀點」把選到的券商 key 交出去', () => {
    const { onViewBroker } = mount(sampleBrokers())
    fireEvent.click(screen.getByTestId('target-dot-sinopac'))
    fireEvent.click(screen.getByRole('button', { name: '查看券商觀點' }))
    expect(onViewBroker).toHaveBeenCalledWith('sinopac')
  })

  it('「清除選取」清掉兩張圖與面板的選取態，並把焦點交還給那個點', () => {
    mount(sampleBrokers())
    fireEvent.click(screen.getByTestId('target-dot-kgi'))
    fireEvent.click(screen.getByRole('button', { name: '清除選取' }))

    expect(screen.queryByTestId('consensus-selected')).not.toBeInTheDocument()
    expect(screen.getByTestId('target-dot-kgi')).toHaveAttribute('aria-pressed', 'false')
    // 按鈕把自己從 DOM 移除，焦點若不接管就會掉到 <body>——鍵盤使用者被丟回文件開頭。
    expect(screen.getByTestId('target-dot-kgi')).toHaveFocus()
  })

  it('報告日期不明的點與「超過 90 天」分得開，且圖例只在真的出現時才多一項', () => {
    // 兩者共用空心時，一份沒有日期的研報會被畫成「確定超過 90 天」——那是資料裡沒有的事。
    mount(sampleBrokers())
    expect(screen.queryAllByText('報告日期不明')).toHaveLength(0)

    cleanup()
    mount([
      broker(),
      broker({
        broker: 'nodate', broker_display: '無日期', latest_target_price: 300,
        latest_report_date: '',
        report_link: { report_id: 'r-nd', broker: 'nodate' },
      }),
    ])

    const unknown = screen.getByTestId('target-dot-nodate')
    expect(unknown.className).toContain('unknown')
    expect(unknown.className).not.toContain('stale')
    expect(unknown.getAttribute('aria-label')).toContain('報告日期不明')
    expect(screen.getAllByText('報告日期不明').length).toBeGreaterThan(0)
  })

  it('表格的幣別排除註記講出是哪一種幣別，不只說「未納入」', () => {
    mount([
      broker(),
      broker({
        broker: 'gs', broker_display: '高盛', latest_target_currency: 'USD',
        latest_target_price: 15, latest_eps_value: null,
        report_link: { report_id: 'r-gs', broker: 'gs' },
      }),
    ])
    fireEvent.click(screen.getByRole('button', { name: '切換表格檢視' }))

    const table = screen.getByRole('table', { name: '各家目標價與 EPS' })
    expect(within(table).getByText('USD，未納入目前口徑')).toBeInTheDocument()
  })

  it('複製表格：只在表格檢視出現，複製的是 TSV 且帶口徑欄，成功要說出來', async () => {
    const clipboard = await import('../../lib/clipboard')
    const spy = vi.spyOn(clipboard, 'copyText').mockResolvedValue()
    mount([
      broker(),
      broker({
        broker: 'gs', broker_display: '高盛', latest_target_currency: 'USD',
        latest_target_price: 15, latest_eps_value: null,
        report_link: { report_id: 'r-gs', broker: 'gs' },
      }),
    ])
    // 點圖檢視時沒有「一張表」可言。
    expect(screen.queryByRole('button', { name: '複製表格' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '切換表格檢視' }))
    fireEvent.click(screen.getByRole('button', { name: '複製表格' }))

    const tsv = spy.mock.calls[0][0]
    const [header, ...rows] = tsv.split('\n').map(line => line.split('\t'))
    expect(header).toContain('納入目標價口徑')
    const gs = rows.find(r => r[0] === '高盛')!
    expect(gs[header.indexOf('目標價')]).toBe('15')
    expect(gs[header.indexOf('納入目標價口徑')]).toBe('否') // 與表格上那行小字同一個判準
    expect(await screen.findByText('已複製，可貼入 Excel')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('複製失敗要說出來（區網 HTTP 下剪貼簿可能被拒）', async () => {
    const clipboard = await import('../../lib/clipboard')
    const spy = vi.spyOn(clipboard, 'copyText').mockRejectedValue(new Error('denied'))
    mount([broker()])
    fireEvent.click(screen.getByRole('button', { name: '切換表格檢視' }))
    fireEvent.click(screen.getByRole('button', { name: '複製表格' }))
    expect(await screen.findByText('複製失敗，請再試一次')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('切到「90 天內」時濾掉過期券商，且被濾掉者的選取一併失效', () => {
    const brokers = [
      ...sampleBrokers(),
      broker({
        broker: 'old', broker_display: '老報告', latest_target_price: 320,
        latest_report_date: OLD_DATE,
        report_link: { report_id: 'r-old', broker: 'old' },
      }),
    ]
    mount(brokers)

    fireEvent.click(screen.getByTestId('target-dot-old'))
    expect(screen.getByTestId('consensus-selected')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: '90 天內' }))
    expect(screen.queryByTestId('target-dot-old')).not.toBeInTheDocument()
    expect(screen.queryByTestId('consensus-selected')).not.toBeInTheDocument()
  })

  it('EPS 口徑切換後換一組點，原本被排除的那家改為入圖', () => {
    mount(sampleBrokers())
    expect(screen.getByTestId('eps-excluded-yuanta')).toHaveTextContent('FY25E，未納入目前期間')

    fireEvent.click(screen.getByRole('button', { name: 'EPS 比較口徑：FY26E' }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /FY25E/ }))

    expect(screen.getByTestId('eps-dot-yuanta')).toBeInTheDocument()
    expect(screen.getByTestId('eps-excluded-kgi')).toHaveTextContent('FY26E，未納入目前期間')
  })

  it('表格檢視顯示同一份資料，並可從表格選取同一家券商', () => {
    mount(sampleBrokers())
    fireEvent.click(screen.getByRole('button', { name: '切換表格檢視' }))

    const table = screen.getByRole('table', { name: '各家目標價與 EPS' })
    expect(within(table).getByText('NT$478')).toBeInTheDocument()
    // 表格與點圖共用 epsExclusionNote：兩邊對同一家券商必須是逐字相同的說明。
    expect(within(table).getAllByText('FY25E，未納入目前期間')).toHaveLength(1)

    fireEvent.click(screen.getByTestId('table-pick-kgi'))
    expect(within(screen.getByTestId('consensus-selected')).getByText('凱基')).toBeInTheDocument()
  })

  it('資料檢視同時提供行動卡片，且卡片可選取同一家券商', () => {
    mount(sampleBrokers())
    fireEvent.click(screen.getByRole('button', { name: '切換表格檢視' }))

    // jsdom 會套用桌面 CSS，因此行動卡片在測試環境中是 display:none；顯示切換由下方
    // CSS 契約守門，這裡只驗證實際 DOM 內容與它和桌面版共用同一份選取狀態。
    const cards = screen.getByLabelText('各家目標價與 EPS 行動版')
    expect(within(cards).getByText('凱基')).toBeInTheDocument()
    expect(within(cards).getByText('NT$478')).toBeInTheDocument()
    expect(within(cards).getAllByText('FY26E EPS')).toHaveLength(4)
    expect(within(cards).getByText(shown(KGI_DATE))).toBeInTheDocument()

    fireEvent.click(within(cards).getByLabelText('選取凱基'))
    expect(within(screen.getByTestId('consensus-selected')).getByText('凱基')).toBeInTheDocument()
  })

  it('一家券商都沒有時說的是範圍太窄，不是「券商沒提供」', () => {
    // 兩者在畫面上長得一樣，但一個要去查原文、一個要把資料範圍放寬。
    mount([])
    expect(screen.getAllByText('此標的尚無任何已擷取的券商觀點。')).toHaveLength(2)
  })

  it('已經在「含過期資料」時，空狀態不會叫使用者去點「含過期資料」', () => {
    // 建議一個什麼都不會改變的動作，比不給建議更糟。
    mount([])
    expect(screen.queryByText(/改選「含過期資料」/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: '90 天內' }))
    expect(screen.getAllByText(/改選「含過期資料」可看到較舊的報告/).length).toBeGreaterThan(0)
  })

  it('有券商但都沒揭露該指標時，說的是「沒有券商提供」', () => {
    mount([
      broker({ latest_target_price: null, latest_eps_value: null }),
      broker({ broker: 'psc', broker_display: '統一', latest_target_price: null, latest_eps_value: null }),
    ])
    expect(screen.getByText('此範圍內沒有券商提供目標價。')).toBeInTheDocument()
    expect(screen.getByText('此範圍內沒有券商提供 EPS 預估。')).toBeInTheDocument()
  })

  it('全部券商都未標示幣別時仍可比較彼此，只是軸不冠幣別', () => {
    // 幣別是從現有券商推導的（取眾數），沒有人標示時「未標示」本身就是一致的口徑。
    // 這時退成不畫圖反而是把「這幾家講的是同一件事」這個事實丟掉。
    mount([
      broker({ broker: 'a', broker_display: '甲', latest_target_currency: null, latest_target_price: 100 }),
      broker({ broker: 'b', broker_display: '乙', latest_target_currency: null, latest_target_price: 120 }),
    ])
    expect(screen.getByTestId('target-dot-a')).toBeInTheDocument()
    expect(screen.getByTestId('target-dot-b')).toBeInTheDocument()
    expect(screen.getByText('目標價')).toBeInTheDocument()
  })
})

function coverage(partial: Partial<Coverage> = {}): Coverage {
  return {
    state: 'ok',
    brokers_total: 14,
    brokers_extracted: 6,
    brokers_in_consensus: 3,
    reports_available: 170,
    note: '',
    ...partial,
  }
}

describe('CoverageStrip', () => {
  it('三個數字在同一句話裡，分母說得出口', () => {
    render(<CoverageStrip coverage={coverage()} />)
    const strip = screen.getByRole('region', { name: '資料涵蓋' })
    expect(strip).toHaveTextContent('14 家追蹤・6 家已擷取・3 家納入共識')
  })

  it('說明預設收合，展開後才給比例條與註記', () => {
    render(<CoverageStrip coverage={coverage({ note: '歷史研報仍有部分欄位尚待整理。' })} />)

    const toggle = screen.getByRole('button', { name: /查看資料涵蓋說明/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('6 / 14 家')).not.toBeInTheDocument()

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('6 / 14 家')).toBeInTheDocument()
    expect(screen.getByText('170 份')).toBeInTheDocument()
    expect(screen.getByText('歷史研報仍有部分欄位尚待整理。')).toBeInTheDocument()
    // aria-controls 指到的 id 必須真的在 DOM 裡
    expect(document.getElementById(toggle.getAttribute('aria-controls')!)).not.toBeNull()
  })

  it('partial 即使已擷取比例 100%，仍明示非完整品質', () => {
    render(<CoverageStrip coverage={coverage({
      state: 'partial', brokers_total: 2, brokers_extracted: 2, brokers_in_consensus: 2,
    })} />)
    fireEvent.click(screen.getByRole('button', { name: /查看資料涵蓋說明/ }))

    expect(screen.getByText('部分資料 · 非完整品質')).toBeInTheDocument()
    expect(screen.getByText('2 / 2 家').previousElementSibling?.querySelector('i'))
      .toHaveStyle({ width: '100%' })
  })
})
