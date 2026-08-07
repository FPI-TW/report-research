import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import type { RadarInstrumentItem } from '../../lib/radarSchemas'
import { InstrumentTable } from './InstrumentTable'
import tableSource from './InstrumentTable.tsx?raw'
import tableCss from './InstrumentTable.module.css?raw'

function item(partial?: Partial<RadarInstrumentItem>): RadarInstrumentItem {
  return {
    market: 'TW',
    market_display: '台股',
    instrument_code: '2330',
    instrument_name: '台積電',
    broker_count: 20,
    report_count: 1772,
    latest_report_date: '2026-08-07',
    coverage_state: 'ok',
    consensus: {
      window: '90',
      stance: {
        rating: 'buy',
        bullish: 12, neutral: 7, bearish: 1, total_rated: 20,
        // 後端恆回五級（zod 也要求 length(5)），零家數的級別同樣在陣列裡
        distribution: [
          { rating: 'buy', count: 9 },
          { rating: 'overweight', count: 3 },
          { rating: 'neutral', count: 7 },
          { rating: 'underweight', count: 0 },
          { rating: 'sell', count: 1 },
        ],
        upgrades: 2, downgrades: 0, net_rating: 2,
      },
      target: { revision_direction: 'none' },
    },
    ...partial,
  }
}

function renderTable(items: RadarInstrumentItem[]) {
  return render(
    <MemoryRouter>
      <InstrumentTable
        items={items}
        sort="latest"
        hrefFor={(m, c) => `/radar?market=${m}&code=${c}`}
      />
    </MemoryRouter>,
  )
}

function firstRow() {
  return screen.getAllByRole('row')[1]
}

describe('InstrumentTable', () => {
  it('七欄表頭齊全、順序固定，且皆為 column header', () => {
    renderTable([item()])
    // 用 textContent 開頭比對而不是可及名稱正則：幾個欄名帶 sr-only 的口徑說明，
    // 而那段說明裡也出現「標的」二字，用 /標的/ 會同時命中兩欄。
    const heads = screen.getAllByRole('columnheader').map(th => th.textContent ?? '')
    const labels = ['標的', '共識評等', '近期變化', '評等分布', '資料涵蓋', '最新研報', '查看']
    expect(heads).toHaveLength(labels.length)
    heads.forEach((text, i) => expect(text.startsWith(labels[i]), text).toBe(true))
  })

  it('標的欄是列標題，含名稱、代碼與市場', () => {
    renderTable([item()])
    const rowHeader = screen.getByRole('rowheader')
    expect(within(rowHeader).getByText('台積電')).toBeInTheDocument()
    expect(within(rowHeader).getByText('2330')).toBeInTheDocument()
    expect(within(rowHeader).getByText('台股')).toBeInTheDocument()
  })

  /*
   * 這一欄的值是**中位立場**（後端 stance.rating ＝ median_rating），不是最新一份研報
   * 的評等。欄名與 sr-only 前綴一起把差別說出來——差別看不出來，而它會被當成後者。
   */
  it('共識評等印中位立場並在可及名稱裡說明', () => {
    renderTable([item()])
    expect(within(firstRow()).getByText('買進')).toBeInTheDocument()
    expect(within(firstRow()).getByText('中位立場：')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /共識評等/ })).toBeInTheDocument()
  })

  it('近期變化以評等淨變動優先，並附家數', () => {
    renderTable([item()])
    expect(within(firstRow()).getByText('評等上調')).toBeInTheDocument()
    expect(within(firstRow()).getByText('2 家')).toBeInTheDocument()
  })

  it('評等分布給整條一個完整可及名稱，並含五級明細', () => {
    renderTable([item()])
    const bar = within(firstRow()).getByRole('img')
    const label = bar.getAttribute('aria-label') ?? ''
    expect(label).toContain('共 20 家已評等')
    expect(label).toContain('偏多 60%，12 家')
    expect(label).toContain('五級明細：買進 9 家、加碼 3 家、中立 7 家、賣出 1 家')
  })

  it('資料涵蓋印券商數與研報數', () => {
    renderTable([item()])
    // 整列比對會誤中日期（2026/08/07 也含「20」），所以只看那一格的完整文字。
    const cells = within(firstRow()).getAllByRole('cell')
    expect(cells.map(c => c.textContent?.replace(/\s+/g, ' ').trim()))
      .toContain('20 家券商· 1772 份研報')
  })

  it('最新研報以 YYYY/MM/DD 呈現', () => {
    renderTable([item()])
    expect(within(firstRow()).getByText('2026/08/07')).toBeInTheDocument()
  })

  /*
   * 硬性限制：清單頁不得出現目標價的中位數／平均／區間／百分比，唯一出口是連到總覽的
   * 各家目標價。元件層只驗得到「這份 fixture 沒印」，所以另有一條靜態比對（下一個 it）。
   */
  it('不出現任何目標價聚合，只給「查看各家目標價」連結', () => {
    renderTable([item()])
    const row = firstRow()
    for (const banned of ['中位數', '平均', '區間', 'NT\\$', 'US\\$']) {
      expect(within(row).queryByText(new RegExp(banned)), banned).toBeNull()
    }
    expect(
      within(row).getByRole('link', { name: /查看 台積電 2330 的各家目標價/ }),
    ).toBeInTheDocument()
  })

  it('原始碼不引用任何目標價聚合欄位', () => {
    // 元件測試只覆蓋餵進去的 fixture；靜態比對覆蓋所有分支，包含日後有人「順手」加回來。
    for (const banned of ['target.median', 'revision_pct', 'fmtPrice', 'fmtNum']) {
      expect(tableSource, banned).not.toContain(banned)
    }
  })

  it('未擷取共識的列走淡態', () => {
    renderTable([item({ consensus: null })])
    const row = firstRow()
    expect(within(row).getByText('資料擷取中')).toBeInTheDocument()
    expect(within(row).queryByRole('img')).toBeNull()
  })

  /*
   * 連結名稱是對目的地的承諾：一家都沒給目標價時仍寫「查看各家目標價」，
   * 使用者點過去只會看到空的一區，而那不是錯誤狀態、沒有任何提示。
   */
  it('沒有任何目標價時連結不承諾各家目標價', () => {
    renderTable([item({
      consensus: { ...item().consensus!, target: null },
    })])
    expect(within(firstRow()).getByRole('link', { name: /查看 台積電 2330 的券商觀點/ }))
      .toBeInTheDocument()
  })

  /*
   * 每列只留一個 Tab 停點。列本身可點是滑鼠的便利，給它 tabindex 會讓 52 檔變成
   * 104 個停點，而兩個停點做的是同一件事。
   */
  it('列本身不可聚焦，每列只有一個連結', () => {
    renderTable([item(), item({ instrument_code: '2317', instrument_name: '鴻海' })])
    for (const row of screen.getAllByRole('row').slice(1)) {
      expect(row).not.toHaveAttribute('tabindex')
      expect(within(row).getAllByRole('link')).toHaveLength(1)
    }
  })

  it('三態之間表頭不消失', () => {
    const { rerender } = renderTable([])
    expect(screen.getAllByRole('columnheader')).toHaveLength(7)
    rerender(
      <MemoryRouter>
        <InstrumentTable items={[]} sort="latest" hrefFor={() => '/radar'} loading />
      </MemoryRouter>,
    )
    expect(screen.getAllByRole('columnheader')).toHaveLength(7)
    expect(screen.getAllByTestId('picker-skeleton').length).toBeGreaterThan(0)
  })

  it('目前排序反映在對應欄的 aria-sort', () => {
    render(
      <MemoryRouter>
        <InstrumentTable items={[item()]} sort="reports" hrefFor={() => '/radar'} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('columnheader', { name: /資料涵蓋/ }))
      .toHaveAttribute('aria-sort', 'descending')
    expect(screen.getByRole('columnheader', { name: /最新研報/ }))
      .not.toHaveAttribute('aria-sort')
  })
})

/*
 * 契約解析刻意採 ReportCssContracts 那組（取「所有」匹配 ＋ only()），不用 RadarCssContracts
 * 的 blockAfter——後者只取第一個匹配，而 CSS 生效的是最後一次宣告：在檔尾追加一條
 * `.table { min-width: auto }` 可以讓斷言全綠而版面已毀。
 */
function blocksFor(css: string, selector: string): string[] {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const out: string[] = []
  for (const m of clean.matchAll(new RegExp(`(?:^|[}\\n])\\s*${esc}\\s*\\{`, 'g'))) {
    const open = clean.indexOf('{', m.index)
    let depth = 0
    for (let j = open; j < clean.length; j += 1) {
      if (clean[j] === '{') depth += 1
      else if (clean[j] === '}') {
        depth -= 1
        if (depth === 0) { out.push(clean.slice(open + 1, j)); break }
      }
    }
  }
  return out
}

function onlyBlock(css: string, selector: string): string {
  const blocks = blocksFor(css, selector)
  expect(
    blocks.length,
    `${selector} 應恰好出現一次，實際 ${blocks.length} 次；CSS 以最後一次宣告為準，`
    + '出現多次時本契約無法判定生效值。',
  ).toBe(1)
  return blocks[0]
}

describe('InstrumentTable 版面契約', () => {
  /*
   * 中文的 min-content 是**一個字**：只寫 overflow-x 而沒有 min-width，窄容器下表格
   * 不會橫捲，會把每一欄壓成逐字直排，而 CSS 不為此報任何錯。兩者必須成對存在。
   */
  it('橫捲與最小寬度成對存在', () => {
    expect(onlyBlock(tableCss, '.frame')).toMatch(/overflow-x:\s*auto/)
    expect(onlyBlock(tableCss, '.table')).toMatch(/min-width:\s*\d+px/)
  })

  it('表頭樣式只套 thead，不會蓋到列標題', () => {
    // `.table th` 會把表頭的淡底與 12px 小字一併套到每一列的第一格（它是 th scope=row）。
    expect(blocksFor(tableCss, '.table th')).toHaveLength(0)
    expect(blocksFor(tableCss, '.table thead th')).toHaveLength(1)
  })

  it('唯一的可點控制項套 44px 觸控目標', () => {
    expect(onlyBlock(tableCss, '.go')).toMatch(/min-height:\s*44px/)
  })
})
