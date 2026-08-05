import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BrokerSummary } from '../../lib/radarSchemas'
import { BrokerList } from './BrokerList'

vi.mock('./BrokerTimeline', () => ({
  // 歷程面板自己會打 API（useBrokerHistory）；本檔驗的是清單的表格與開合，
  // 不是面板內容，故換成替身，免得每個展開測試都要架 query client。
  BrokerTimeline: () => <div data-testid="timeline-stub" />,
}))

function broker(partial: Partial<BrokerSummary> = {}): BrokerSummary {
  return {
    broker: 'daiwa', broker_display: '大和', latest_rating: 'buy',
    latest_rating_raw: 'Buy', latest_target_price: 120, latest_target_currency: 'TWD',
    latest_eps_value: 5, latest_eps_fy: 2027, latest_eps_period: 'FY',
    latest_eps_currency: 'USD', latest_eps_unit: 'per_share',
    latest_report_date: '2026-07-11',
    report_link: { report_id: 'r1' },
    recent_change_label: null, recent_change_direction: 'none',
    stale: false, has_history: true,
    ...partial,
  }
}

function mount(brokers: BrokerSummary[] = [broker()]) {
  return render(
    <BrokerList brokers={brokers} code="8046" market="TW" window="90" onOpenReport={vi.fn()} />,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('BrokerList', () => {
  it('桌機與手機都使用 EPS 自身 metadata，不借用目標價幣別', () => {
    render(
      <BrokerList
        brokers={[broker()]}
        code="8046"
        market="TW"
        window="90"
        onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByTestId('broker-eps-daiwa-desktop')).toHaveTextContent('US$5／股 · FY27E')
    expect(screen.getByTestId('broker-eps-daiwa-mobile')).toHaveTextContent('US$5／股 · FY27E')
    expect(screen.queryByText(/NT\$5/)).not.toBeInTheDocument()
    // per_share 是內部欄位（後端對缺值一律補這個值），不得以任何形式出現在畫面上
    expect(document.body.textContent).not.toContain('per_share')
    expect(document.body.textContent).not.toContain('每股')
    expect(screen.getByTestId('broker-row-daiwa')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByTestId('broker-row-daiwa').tagName).toBe('BUTTON')
  })

  it('stale 券商在桌機與手機兩份 DOM 都標示「窗外最新」', () => {
    // 兩份 DOM 是各自手寫的，桌機有、手機漏掉過一次。stale 代表這家券商在所選窗期內
    // 沒有訊號、顯示的是窗外最近一筆——窗期正是這頁的語意核心，少了它，同一家券商
    // 在寬窄兩種螢幕上講的是不同的話（≤900px 會把窗期外的舊評等讀成當期最新）。
    render(
      <BrokerList
        brokers={[broker({ stale: true })]}
        code="8046"
        market="TW"
        window="30"
        onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getAllByText('窗外最新')).toHaveLength(2)
  })

  it('手機卡的數值不得被包進展開鈕（包進去＝輔助技術一個數字都讀不到）', () => {
    // button 在無障礙樹裡是葉節點，子樹只會被壓成可及名稱；而 ≤900px 時桌機表格是
    // display: none、那份有語意的 <td> 整份離開無障礙樹。兩件事加起來，窄視窗使用者
    // 拿到的整份券商清單就只剩「大和 · 買進，按鈕，已收合」重複 N 次。
    // 需要 hidden: true —— .cards 的**基礎**樣式就是 display: none（手機版靠
    // @media (max-width: 900px) 才打開），而 jsdom 不求值 media query，所以手機那份 DOM
    // 在 role 查詢眼中恆為不可及。
    mount()

    const cardHead = screen.getAllByRole('button', { hidden: true })
      .find(el => el.className.includes('cardHead'))!
    expect(cardHead).toBeDefined()
    expect(cardHead).toHaveTextContent('大和 · Buy')
    for (const testid of ['target', 'eps', 'change', 'date']) {
      const field = screen.getByTestId(`broker-${testid}-daiwa-mobile`)
      expect(cardHead.contains(field)).toBe(false)
    }
  })

  it('防禦性過濾無 canonical broker 的舊後端資料', () => {
    render(
      <BrokerList
        brokers={[broker({ broker: '   ', broker_display: null })]}
        code="8046"
        market="TW"
        window="90"
        onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByText('尚無券商清單')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /歷程/ })).not.toBeInTheDocument()
  })

  // ── 展開／收合：每一列只有一個控制項 ────────────────────────────────

  it('展開與收合都走同一顆鈕，且 aria-expanded 跟著變', () => {
    const { container } = mount()
    const toggle = screen.getByTestId('broker-row-daiwa')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(container.querySelector('[class*="rowOpen"]')).not.toBeNull()
    expect(screen.getAllByTestId('timeline-stub').length).toBeGreaterThan(0)

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('資料列本身不是控制項——點格子不會展開', () => {
    // 曾經整列可點：命中區大，但 <tr> 不可聚焦、也沒有 aria-expanded，等於同一個狀態
    // 有兩個入口而只有一個講得出自己是什麼。滑鼠與鍵盤看到的不是同一個介面。
    mount()

    fireEvent.click(screen.getByText('大和'))

    expect(screen.getByTestId('broker-row-daiwa')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryAllByTestId('timeline-stub')).toHaveLength(0)
  })

  it('鈕以 aria-controls 指向它開合的面板', () => {
    mount()
    const toggle = screen.getByTestId('broker-row-daiwa')
    const panelId = toggle.getAttribute('aria-controls')
    expect(panelId).toBeTruthy()

    fireEvent.click(toggle)

    expect(document.getElementById(panelId!)).not.toBeNull()
  })

  it('桌機列與手機卡指向各自的面板，不是同一個 id', () => {
    // 本檔把 BrokerTimeline 換成替身，所以這裡只驗得到清單自己產生的兩個 id；
    // 面板**內部**的 id 撞號另有 BrokerTimeline.test.tsx 的「同時掛兩份」那條守門。
    mount([broker(), broker({ broker: 'nomura', broker_display: '野村' })])
    fireEvent.click(screen.getByTestId('broker-row-daiwa'))

    const ids = Array.from(document.querySelectorAll('[id]')).map(el => el.id)
    expect(ids.length).toBeGreaterThan(0)
    expect(ids.filter((id, index) => ids.indexOf(id) !== index)).toEqual([])
  })

  it('收合後面板真的離開 DOM（AnimatePresence 不得把它留在原地）', async () => {
    // 展開／收合用 AnimatePresence 做高度過渡後多出一種故障：退場動畫若沒有完成，
    // 面板會永遠留在 DOM 裡——畫面上看起來像「收不起來」，而 aria-expanded 是對的。
    mount()
    const toggle = screen.getByTestId('broker-row-daiwa')

    fireEvent.click(toggle)
    expect(screen.getAllByTestId('timeline-stub').length).toBeGreaterThan(0)

    fireEvent.click(toggle)
    await waitFor(() => expect(screen.queryAllByTestId('timeline-stub')).toHaveLength(0))
  })

  // ── 缺值與視覺層次 ────────────────────────────────────────────────

  it('缺值一律顯示「未提供」，不是破折號', () => {
    mount([broker({
      latest_target_price: null,
      latest_target_currency: null,
      latest_eps_value: null,
      latest_report_date: '',
      recent_change_label: null,
    })])

    // 精確數，不是下限：先前寫成 `>= 4` 而實際渲染 8 個，四個「這一格不走缺值路徑」
    // 的反轉實驗全部沒被抓到——下限式斷言容得下三格靜默消失。
    // 桌機四格（目標價／EPS／最近變化／報告日期）＋ 手機卡四格。
    expect(screen.getAllByText('未提供')).toHaveLength(8)
    expect(screen.queryByText('—')).not.toBeInTheDocument()
    for (const testid of ['target', 'eps', 'change', 'date']) {
      expect(screen.getByTestId(`broker-${testid}-daiwa-mobile`)).toHaveTextContent('未提供')
    }
  })

  it('評等依多空上色，且色掛在 span 上（掛 td 會被 .table td 的特異度蓋掉）', () => {
    const { container } = mount([
      broker({ latest_rating: 'sell', latest_rating_raw: 'Sell' }),
    ])

    const tinted = container.querySelector('[class*="bear"]')
    expect(tinted).not.toBeNull()
    expect(tinted!.tagName).toBe('SPAN')
    expect(tinted).toHaveTextContent('Sell')
  })

  it('EPS 的比較群組標籤降為次要小字，但整格文字仍是 fmtEps 的完整輸出', () => {
    const { container } = mount()
    const cell = screen.getByTestId('broker-eps-daiwa-desktop')

    expect(cell).toHaveTextContent('US$5／股 · FY27E')
    expect(cell.querySelector('[class*="epsMeta"]')).toHaveTextContent('· FY27E')
    // 主值不得被歸進 meta：那樣整欄又回到同一個字級
    expect(container.querySelector('[class*="epsMeta"]')).not.toHaveTextContent('US$5')
  })

  it('手機卡片把六欄改成標籤／值配對，不是一串裸值', () => {
    // 「NT$120 US$5／股 2026/07/11」三個數字並排時，讀者無從得知哪個是哪個。
    // **每一格都要走自己的 testid**：標籤文字與桌機的 <th> 逐字相同，而 jsdom 不求值
    // media query ⇒ 兩份 DOM 同時存在，`getAllByText('目標價')` 會被表頭滿足——
    // 把手機那一格的 <dt> 清空也不會紅（實測過的空守門）。
    mount([broker({ recent_change_label: '目標價上修 6.3%', recent_change_direction: 'up' })])

    const expected: [string, string, string][] = [
      ['change', '最近變化', '目標價上修 6.3%'],
      ['target', '目標價', 'NT$120'],
      ['eps', 'EPS', 'US$5／股 · FY27E'],
      ['date', '報告日期', '2026/07/11'],
    ]
    for (const [testid, label, value] of expected) {
      const field = screen.getByTestId(`broker-${testid}-daiwa-mobile`)
      expect(field.querySelector('dt')).toHaveTextContent(label)
      expect(field.querySelector('dd')).toHaveTextContent(value)
    }
  })
})
