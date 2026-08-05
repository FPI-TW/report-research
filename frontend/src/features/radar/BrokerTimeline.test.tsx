import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrokerHistory, BrokerSnapshot, ChangeItem } from '../../lib/radarSchemas'
import { BrokerTimeline } from './BrokerTimeline'
import { useBrokerHistory } from './useRadar'

vi.mock('./useRadar', () => ({ useBrokerHistory: vi.fn() }))

function snapshot(
  reportId: string,
  reportDate: string,
  thesis: BrokerSnapshot['thesis'] = [],
): BrokerSnapshot {
  return {
    report_id: reportId, report_date: reportDate, in_window: true,
    rating: 'buy', rating_raw: 'Buy', target_price: 120, target_currency: 'TWD',
    eps: [{
      fiscal_year: 2025, period: 'FY', currency: 'TWD', unit: 'per_share',
      median: 50, count: 1, revision_pct: null, revision_direction: 'none',
    }],
    primary_eps: {
      fiscal_year: 2027, period: 'FY', currency: 'TWD', unit: 'per_share',
      median: 66.4, count: 1, revision_pct: null, revision_direction: 'none',
    },
    thesis, extraction_status: 'valid', report_link: { report_id: reportId },
  }
}

/**
 * 後端固定回四格（缺的維度三個欄位全 null）。
 * 這裡刻意讓陣列順序是「催化劑 → 展望 → 估值 → 風險」——前端必須自己排成閱讀順序，
 * 照收到的順序畫等於讓版面隨後端實作漂移。
 */
function thesisCells(
  filled: Partial<Record<'outlook' | 'catalyst' | 'risk' | 'valuation', [string | null, string]>>,
): BrokerSnapshot['thesis'] {
  const display = {
    catalyst: '催化劑', outlook: '展望', valuation: '估值', risk: '風險',
  } as const
  return (Object.keys(display) as (keyof typeof display)[]).map(dimension => {
    const cell = filled[dimension]
    return {
      dimension,
      dimension_display: display[dimension],
      stance: cell ? 'positive' : null,
      summary: cell ? cell[0] : null,
      evidence: cell ? cell[1] : null,
    }
  })
}

/** 真實形狀：prev/curr 是 scale.eps_group_label 的原始輸出，含 TWD 與 per_share。 */
const EPS_GROUP_MISMATCH: ChangeItem = {
  field: 'eps', dimension: 'FY2027 · FY · TWD · per_share', label: 'EPS 群組變更',
  direction: 'incomparable', prev_value: 'FY2026 · 1H · TWD · per_share',
  curr_value: 'FY2027 · FY · TWD · per_share', pct_change: null, comparable: false,
  reason_code: 'eps_group_mismatch',
  incomparable_reason: 'EPS 群組不同：FY／期間／幣別／單位無法直接比較',
}

const NO_PRIOR = {
  from_report_id: null, from_report_date: null, to_report_date: '2026-07-11',
  changes: [], has_prior_report: false, has_prior_comparable: false,
  note: '沒有更早研報',
}

function history(partial: Partial<BrokerHistory> = {}): BrokerHistory {
  return {
    market: 'TW', instrument_code: '8046', broker: 'daiwa', broker_display: '大和',
    window: '90', as_of: '2026-07-11', current_rating: 'buy', report_count: 3,
    snapshots: [
      snapshot('r3', '2026-07-11', thesisCells({
        outlook: ['需求轉強', '展望原句'],
        catalyst: ['新產品放量', '催化劑原句'],
      })),
      snapshot('r2', '2026-06-01'),
      snapshot('r1', '2026-05-01'),
    ],
    diffs: [
      {
        from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
        changes: [EPS_GROUP_MISMATCH],
        has_prior_report: true, has_prior_comparable: true, note: null,
      },
      {
        from_report_id: 'r1', from_report_date: '2026-05-01', to_report_date: '2026-06-01',
        changes: [], has_prior_report: true, has_prior_comparable: true, note: null,
      },
      { ...NO_PRIOR, to_report_date: '2026-05-01' },
    ],
    coverage_state: 'ok',
    ...partial,
  }
}

function useHistory(partial: Partial<BrokerHistory> = {}) {
  vi.mocked(useBrokerHistory).mockReturnValue({
    data: history(partial), isLoading: false, isError: false, refetch: vi.fn(),
  } as never)
}

/** 距今 n 天的 ISO 日期。過期提示由「今天」現算，寫死日期會在 90 天後自己翻紅。 */
function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

beforeEach(() => {
  vi.resetAllMocks()
  useHistory()
})

function mount(onOpenReport = vi.fn()) {
  const { container } = render(
    <BrokerTimeline
      code="8046" market="TW" broker="daiwa" window="90" expanded
      onOpenReport={onOpenReport}
    />,
  )
  // 第一個 <dl> 就是最新研報的三個摘要；歷史研報展開後才會有第二個。
  return { onOpenReport, facts: () => container.querySelector('dl')! }
}

describe('BrokerTimeline', () => {
  it('最新研報預設展開、較舊研報預設收合且整段不在 DOM', () => {
    mount()

    expect(screen.getByRole('heading', { name: '大和最新觀點' })).toBeInTheDocument()
    expect(screen.getByText(/需求轉強/)).toBeInTheDocument()

    // 兩份舊研報各一顆收合中的控制項（Pressable 的連結鈕沒有 aria-expanded，不會誤中）
    const older = screen.getAllByRole('button', { expanded: false })
    expect(older).toHaveLength(2)
    // 收合＝條件渲染而非 CSS 隱藏：後兩者在 jsdom 量不出差別，會讓這條退化成空守門
    expect(screen.getAllByText('查看原始研報')).toHaveLength(1)

    fireEvent.click(older[0])

    expect(older[0]).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByText('查看原始研報')).toHaveLength(2)
  })

  it('展開後由上而下：券商與日期 → 三個摘要 → 一句話結論 → 核心論點 → 口徑提示 → 歷史報告', () => {
    mount()
    const body = document.body.textContent ?? ''
    const order = [
      '大和最新觀點', '2026/07/11',
      '評等', '目標價', 'FY27E EPS',
      '一句話結論', '核心論點', 'EPS 因期間或資料口徑不同，暫不比較。', '歷史報告',
    ]
    const positions = order.map(token => body.indexOf(token))

    expect(positions.filter(position => position < 0)).toEqual([])
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  it('一句話結論取四維第一句並標明出處，該維不再重複出現在核心論點', () => {
    mount()

    // fixture 的陣列順序是催化劑在前，展望仍要排到最前面
    expect(screen.getByText(/需求轉強/)).toHaveTextContent('需求轉強（展望）')
    expect(screen.getByText('核心論點（1）')).toBeInTheDocument()
    expect(screen.getByText('新產品放量')).toBeInTheDocument()
    expect(screen.getAllByText(/需求轉強/)).toHaveLength(1)
  })

  it('四維都沒有 summary 時不拿 evidence（研報原句）充當結論', () => {
    useHistory({
      snapshots: [snapshot('r3', '2026-07-11', thesisCells({ risk: [null, '風險原句'] }))],
      diffs: [NO_PRIOR],
    })
    mount()

    expect(screen.queryByText('一句話結論')).not.toBeInTheDocument()
    expect(screen.getByText('核心論點（1）')).toBeInTheDocument()
    expect(screen.getByText('風險原句')).toBeInTheDocument()
  })

  it('不可比較收斂成一句話，不外洩 per_share 與幣別代碼', () => {
    mount()

    expect(screen.getByText('EPS 因期間或資料口徑不同，暫不比較。')).toBeInTheDocument()
    // 舊版把後端的 eps_group_label 原樣印出來（含 TWD 與 per_share），
    // 再加上 DirectionTag 的「不可比較」與 incomparable_reason，同一件事講三次。
    expect(document.body.textContent).not.toContain('per_share')
    expect(document.body.textContent).not.toContain('TWD')
    expect(screen.queryByText('不可比較')).not.toBeInTheDocument()
    expect(screen.queryByText(/無法直接比較/)).not.toBeInTheDocument()
  })

  it('顯示中的 EPS 群組本身比得出來時，不得同時說「暫不比較」', () => {
    // 後端對 curr 的每一筆 EPS estimate 各產一筆 change：同鍵者可比較、新群組不可比較。
    // 券商每滾動一次預估年度就同時產出兩種，只看「有沒有不可比較」會讓畫面變成
    // 上面掛「上修 12.5%」、下面緊接著「暫不比較」。生產資料實測 112 筆含不可比較
    // EPS 的 diff 有 66 筆（59%）長這樣。
    useHistory({
      diffs: [{
        from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
        changes: [
          {
            field: 'eps', dimension: '2027 FY EPS', label: '2027 FY EPS', direction: 'up',
            prev_value: '59', curr_value: '66.4', pct_change: 12.5, comparable: true,
            reason_code: null, incomparable_reason: null,
          },
          EPS_GROUP_MISMATCH,
        ],
        has_prior_report: true, has_prior_comparable: true, note: null,
      }],
    })
    const { facts } = mount()

    expect(within(facts()).getByText('12.5%')).toBeInTheDocument()
    expect(screen.queryByText(/暫不比較/)).not.toBeInTheDocument()
  })

  it('其他會計年度的 EPS 修正仍看得到，不因為摘要只放得下一格而消失', () => {
    useHistory({
      diffs: [{
        from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
        changes: [
          {
            field: 'eps', dimension: '2027 FY EPS', label: '2027 FY EPS', direction: 'up',
            prev_value: '59', curr_value: '66.4', pct_change: 12.5, comparable: true,
            reason_code: null, incomparable_reason: null,
          },
          {
            field: 'eps', dimension: '2028 FY EPS', label: '2028 FY EPS', direction: 'down',
            prev_value: '80', curr_value: '76', pct_change: -5, comparable: true,
            reason_code: null, incomparable_reason: null,
          },
        ],
        has_prior_report: true, has_prior_comparable: true, note: null,
      }],
    })
    const { facts } = mount()

    // 摘要那格是 primary（FY27E），別的年度不得掛到它身上
    expect(within(facts()).getByText('12.5%')).toBeInTheDocument()
    expect(within(facts()).queryByText('5.0%')).not.toBeInTheDocument()
    // 但也不得整筆消失
    expect(screen.getByText('其他年度')).toBeInTheDocument()
    expect(screen.getByText('2028 FY EPS')).toBeInTheDocument()
    expect(screen.getByText('5.0%')).toBeInTheDocument()
  })

  it('目標價幣別不同也收斂成一句話（與 EPS 各有各的說法）', () => {
    useHistory({
      diffs: [{
        from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
        changes: [{
          field: 'target_price', dimension: null, label: '目標價',
          direction: 'incomparable', prev_value: 'TWD 500', curr_value: 'USD 16',
          pct_change: null, comparable: false, reason_code: null,
          incomparable_reason: '幣別不同',
        }],
        has_prior_report: true, has_prior_comparable: true, note: null,
      }],
    })
    const { facts } = mount()

    expect(screen.getByText('目標價幣別不同，暫不比較。')).toBeInTheDocument()
    // 不可比較的變動不得被 movement() 當成方向變動畫成徽章
    expect(within(facts()).queryByText('不可比較')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('USD 16')
  })

  it('同一種不可比較只講一次', () => {
    useHistory({
      diffs: [{
        from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
        changes: [
          EPS_GROUP_MISMATCH,
          { ...EPS_GROUP_MISMATCH, dimension: 'FY2028 · FY · USD · per_share' },
        ],
        has_prior_report: true, has_prior_comparable: true, note: null,
      }],
    })
    mount()

    expect(screen.getAllByText('EPS 因期間或資料口徑不同，暫不比較。')).toHaveLength(1)
  })

  it('評等以「前 → 後」呈現；EPS 修正只認同一個比較群組的那一筆', () => {
    useHistory({
      diffs: [{
        from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
        changes: [
          {
            field: 'rating', dimension: null, label: '中立 → Buy', direction: 'up',
            prev_value: '中立', curr_value: 'Buy', pct_change: null, comparable: true,
            reason_code: null, incomparable_reason: null,
          },
          {
            field: 'target_price', dimension: null, label: '目標價', direction: 'up',
            prev_value: 'TWD 100', curr_value: 'TWD 120', pct_change: 20, comparable: true,
            reason_code: null, incomparable_reason: null,
          },
          // 這一筆是 FY2025，畫面上的 primary_eps 是 FY2027——掛錯不會有任何錯誤訊息
          {
            field: 'eps', dimension: '2025 FY EPS', label: '2025 FY EPS', direction: 'down',
            prev_value: '55', curr_value: '50', pct_change: -9.1, comparable: true,
            reason_code: null, incomparable_reason: null,
          },
        ],
        has_prior_report: true, has_prior_comparable: true, note: null,
      }],
    })
    const { facts } = mount()

    expect(facts()).toHaveTextContent('中立 → Buy')
    expect(within(facts()).getByText('20.0%')).toBeInTheDocument()
    expect(within(facts()).queryByText('9.1%')).not.toBeInTheDocument()
  })

  it('EPS 金額帶幣別前綴與「／股」，標題帶會計年度', () => {
    const { facts } = mount()

    // 金額先前只有標題被驗到：把 epsAmount 改成 String(median) 掉幣別與後綴，全綠。
    expect(within(facts()).getByText('FY27E EPS')).toBeInTheDocument()
    expect(within(facts()).getByText('NT$66.40／股')).toBeInTheDocument()
  })

  it('coverage_state=partial 時說明只顯示已擷取內容', () => {
    useHistory({ coverage_state: 'partial' })
    mount()

    expect(screen.getByText('部分研報仍在整理，以下只顯示已擷取內容。')).toBeInTheDocument()
  })

  it('rating_raw 缺席時回退到正規化評等的中文，且帶多空語意色', () => {
    // 後端 rating_raw 是 nullish，unknown 評等尤其常見；少了後備整格會是空白。
    useHistory({
      snapshots: [{ ...snapshot('r3', '2026-07-11'), rating: 'sell', rating_raw: null }],
      diffs: [NO_PRIOR],
    })
    const { facts } = mount()

    const tinted = within(facts()).getByText('賣出')
    expect(tinted.className).toMatch(/bear/)
  })

  it('缺值顯示「未提供」，EPS 標題在沒有會計年度時退成單獨的「EPS」', () => {
    useHistory({
      snapshots: [{
        ...snapshot('r3', ''),
        target_price: null, target_currency: null, eps: [], primary_eps: null,
      }],
      diffs: [NO_PRIOR],
    })
    const { facts } = mount()

    expect(within(facts()).getAllByText('未提供')).toHaveLength(2)   // 目標價 ＋ EPS
    expect(within(facts()).getByText('EPS')).toBeInTheDocument()
    // 報告日期為空字串（後端 report_date 為 NULL 時給的就是空字串）
    expect(screen.getAllByText('未提供').length).toBeGreaterThanOrEqual(3)
  })

  it('報告日期超過 90 天才顯示過期提示，由日期現算', () => {
    useHistory({ snapshots: [snapshot('r3', daysAgo(120))], diffs: [NO_PRIOR] })
    mount()

    expect(screen.getByText('最新報告已超過 90 天')).toBeInTheDocument()
  })

  it('90 天內不顯示過期提示', () => {
    useHistory({ snapshots: [snapshot('r3', daysAgo(30))], diffs: [NO_PRIOR] })
    mount()

    expect(screen.queryByText('最新報告已超過 90 天')).not.toBeInTheDocument()
  })

  it('pending_extraction 顯示整理中，而非一般載入錯誤', () => {
    useHistory({ report_count: 0, snapshots: [], diffs: [], coverage_state: 'pending_extraction' })
    mount()

    expect(screen.getByText('此券商研報尚待觀點資料整理')).toBeInTheDocument()
    expect(screen.queryByText('載入券商歷程失敗。')).not.toBeInTheDocument()
  })

  it('窗期內沒有研報時給得出說明，而不是一塊空面板', () => {
    // window_empty 在此之前沒有分支，會渲染成只有標題、底下什麼都沒有的面板。
    useHistory({ report_count: 0, snapshots: [], diffs: [], coverage_state: 'window_empty' })
    mount()

    expect(screen.getByText('近 90 天內沒有這家券商的研報。')).toBeInTheDocument()
  })

  it('沒有可比較的前次研報時說明原因', () => {
    // 「這次沒有變動」與「根本無從比較」在畫面上長得一模一樣；note 是後端唯一的理由出口。
    useHistory({ snapshots: [snapshot('r3', '2026-07-11')], diffs: [NO_PRIOR] })
    mount()

    expect(screen.getByText('沒有更早研報')).toBeInTheDocument()
    expect(screen.queryByText('歷史報告')).not.toBeInTheDocument()
  })

  it('同時掛兩份時 id 不得撞號（BrokerList 就是這樣掛的）', () => {
    // BrokerList 把這個面板渲染**兩次**：桌機表格一份、手機卡片一份，另一份只是
    // display: none，兩份都在 DOM 裡。歷史研報的 body id 若由 report_id 組成就會撞，
    // 而重複 id 的失效方式是靜默的——aria-controls 一律解析到文件順序在前的那一份，
    // 也就是永遠指向桌機那份，手機使用者按下的鈕指著一個他看不見的區塊。
    render(
      <>
        <BrokerTimeline
          code="8046" market="TW" broker="daiwa" window="90" expanded onOpenReport={vi.fn()}
        />
        <BrokerTimeline
          code="8046" market="TW" broker="daiwa" window="90" expanded onOpenReport={vi.fn()}
        />
      </>,
    )
    // 兩份都把歷史研報展開，body 的 id 才會真的進 DOM
    screen.getAllByRole('button', { expanded: false }).forEach(el => fireEvent.click(el))

    const controls = screen.getAllByRole('button', { expanded: true })
      .map(el => el.getAttribute('aria-controls'))
    expect(controls.length).toBe(4)                       // 兩份 × 兩筆歷史研報
    expect(new Set(controls).size).toBe(controls.length)

    const ids = Array.from(document.querySelectorAll('[id]')).map(el => el.id)
    expect(ids.length).toBeGreaterThan(0)
    expect(ids.filter((id, index) => ids.indexOf(id) !== index)).toEqual([])
  })

  it('歷史研報配到的是自己那一筆 diff（older 少了第一筆，索引要位移 1）', () => {
    // 位置式對齊，錯了不報錯：改成 diffs[index] 之後每份歷史研報都會掛上「後一份」的
    // 變動，數字全部合法、畫面看起來完全正常。先前四個測試檔 45 條沒有一條會紅。
    useHistory({
      diffs: [
        {
          from_report_id: 'r2', from_report_date: '2026-06-01', to_report_date: '2026-07-11',
          changes: [{
            field: 'target_price', dimension: null, label: '目標價', direction: 'up',
            prev_value: 'TWD 100', curr_value: 'TWD 120', pct_change: 20, comparable: true,
            reason_code: null, incomparable_reason: null,
          }],
          has_prior_report: true, has_prior_comparable: true, note: null,
        },
        {
          from_report_id: 'r1', from_report_date: '2026-05-01', to_report_date: '2026-06-01',
          changes: [{
            field: 'target_price', dimension: null, label: '目標價', direction: 'down',
            prev_value: 'TWD 108', curr_value: 'TWD 100', pct_change: -7.4, comparable: true,
            reason_code: null, incomparable_reason: null,
          }],
          has_prior_report: true, has_prior_comparable: true, note: null,
        },
        { ...NO_PRIOR, to_report_date: '2026-05-01' },
      ],
    })
    mount()

    const older = screen.getAllByRole('button', { expanded: false })
    fireEvent.click(older[0])   // 2026/06/01 → 應該拿到第 2 筆 diff（-7.4%）

    const body = document.getElementById(older[0].getAttribute('aria-controls')!)!
    expect(within(body).getByText('7.4%')).toBeInTheDocument()
    expect(within(body).queryByText('20.0%')).not.toBeInTheDocument()
  })

  it('最新與展開後的歷史研報都給得到原始研報連結', () => {
    const { onOpenReport } = mount()

    fireEvent.click(screen.getAllByText('查看原始研報')[0])
    expect(onOpenReport).toHaveBeenCalledWith('r3', expect.anything())

    fireEvent.click(screen.getAllByRole('button', { expanded: false })[0])
    fireEvent.click(screen.getAllByText('查看原始研報')[1])
    expect(onOpenReport).toHaveBeenCalledWith('r2', expect.anything())
  })
})
