import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { ReactNode } from 'react'
import { BriefTile } from './BriefTile'
import { HeadlineTile } from './HeadlineTile'
import { RatingChangesTile } from './RatingChangesTile'
import { PreviewLink } from './PreviewLink'
import { headlineInstrument } from './useLibraryHome'
import type { BriefEnvelope } from '../../lib/briefSchemas'
import type { RatingChange } from '../../lib/librarySchemas'
import type { RadarOverview } from '../../lib/radarSchemas'
import type { ReadingDoc } from '../../lib/readingSchemas'
import type { ReportRow } from '../../lib/schemas'

const inRouter = (node: ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>)

const ROW: ReportRow = {
  report_id: 'r1', file_hash: 'a'.repeat(64), file_name: 'nanya.pdf', title: '南電：載板價格持續上漲',
  market: 'TW', source: '元大', summary: '載板供需吃緊。', report_date: '2026-10-27', report_type: '個股',
  instrument_types: null, relates_stock: true, relates_futures: null, stock_targets: ['8046'], futures_targets: null,
}

function doc(over: Partial<ReadingDoc> = {}): ReadingDoc {
  return {
    report_id: 'r1', file_hash: ROW.file_hash, file_name: 'nanya.pdf', instrument_types: [],
    stock_targets: ['8046'], futures_targets: [], has_file: true, is_pdf: true, text_state: 'ok',
    text_chars: 100, takeaways: [], signals_state: 'none', signals: [], ...over,
  } as ReadingDoc
}

afterEach(() => vi.useRealTimers())

describe('headlineInstrument', () => {
  it('優先用研報自己的訊號，沒有才退回第一個個股標的', () => {
    const withSignal = doc({
      signals: [{ instrument_code: '3037', market: 'TW', rating_normalized: 'buy', eps_estimates: [], thesis: [] }],
    } as Partial<ReadingDoc>)
    expect(headlineInstrument(withSignal, ROW)).toEqual({ market: 'TW', code: '3037' })
    expect(headlineInstrument(doc(), ROW)).toEqual({ market: 'TW', code: '8046' })
    expect(headlineInstrument(undefined, { market: 'XX', stock_targets: ['1'] })).toBeNull()
  })
})

describe('HeadlineTile', () => {
  it('整張連到閱讀頁，列出三條重點摘錄與券商共識列', () => {
    const takeaways = [1, 2, 3, 4].map(n => ({ ordinal: n, claim: `主張${n}`, quote: `引文${n}` }))
    const consensus = {
      coverage: { brokers_in_consensus: 9 },
      rating: { total_rated: 9, median_rating: 'buy', upgrades: 2, downgrades: 0 },
    } as unknown as RadarOverview
    inRouter(<HeadlineTile row={ROW} isLatest doc={doc({ takeaways })} consensus={consensus} />)

    const link = screen.getByRole('link', { name: ROW.title! })
    expect(link).toHaveAttribute('href', `/report/${ROW.file_hash}`)
    expect(screen.getByText('最新')).toBeInTheDocument()
    expect(screen.getByText('主張3')).toBeInTheDocument()
    expect(screen.queryByText('主張4')).toBeNull()
    expect(screen.getByText('「引文1」')).toBeInTheDocument()
    expect(screen.getByText('買進')).toBeInTheDocument()
    expect(screen.getByText(/家券商/).textContent).toBe('9 家券商')
    expect(screen.getByText(/調升/).textContent).toBe('調升 2')
    expect(screen.queryByText(/調降/)).toBeNull()
    expect(screen.getByText('查看全文')).toBeInTheDocument()
  })

  it('沒有摘錄與共識時不畫那兩塊', () => {
    inRouter(<HeadlineTile row={ROW} isLatest={false} doc={doc()} />)
    expect(screen.queryByText('重點摘錄')).toBeNull()
    expect(screen.queryByText('券商共識')).toBeNull()
    expect(screen.queryByText('最新')).toBeNull()
  })
})

describe('RatingChangesTile', () => {
  const change: RatingChange = {
    market: 'TW', instrument_code: '8046', instrument_name: '南電', broker: 'fubon', broker_display: '富邦',
    report_date: '2026-10-05', rating_from: 'neutral', rating_to: 'buy', direction: 'up',
  }

  it('每列連到該標的的券商共識，箭頭依方向上色', () => {
    const down = { ...change, instrument_code: '1477', instrument_name: null, rating_from: 'buy', rating_to: 'overweight', direction: 'down' } as RatingChange
    inRouter(<RatingChangesTile items={[change, down]} failed={false} onRetry={() => {}} />)
    const up = screen.getByRole('link', { name: /南電 8046，富邦，中立調升為買進/ })
    expect(up).toHaveAttribute('href', '/consensus?market=TW&code=8046&window=90')
    // 沒有公司名時只顯示代號，不重複
    expect(screen.getByRole('link', { name: /1477 1477，富邦，買進調降為加碼/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '券商共識' })).toHaveAttribute('href', '/consensus')
  })

  it('沒有變動與載入失敗各有一句話', () => {
    const onRetry = vi.fn()
    const { rerender } = inRouter(<RatingChangesTile items={[]} failed={false} onRetry={onRetry} />)
    expect(screen.getByText('目前沒有評等變動')).toBeInTheDocument()
    rerender(<MemoryRouter><RatingChangesTile failed onRetry={onRetry} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(onRetry).toHaveBeenCalled()
  })
})

describe('BriefTile', () => {
  function envelope(brief_date: string, reports: { market: string }[], report_count: number): BriefEnvelope {
    return {
      status: 'ready',
      available_dates: [],
      brief: {
        brief_date, window_start: '', window_end: '', created_at: '', signal_count: 0, report_count,
        markdown: '## 今日重點\n- 一（kgi）\n- 二\n- 三\n- 四\n- 五',
        reports: reports.map((r, i) => ({ report_id: `r${i}`, file_hash: `h${i}`, file_name: 'x', market: r.market })),
      },
    }
  }

  it('今天的簡報叫「今日簡報」，列四條重點、篇數與市場數', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 28, 9))
    inRouter(<BriefTile envelope={envelope('2026-10-28', [{ market: 'TW' }, { market: 'US' }, { market: 'TW' }], 3)} failed={false} onRetry={() => {}} />)
    expect(screen.getByRole('heading', { name: '今日簡報' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').map(li => li.textContent)).toEqual(['一', '二', '三', '四'])
    expect(screen.getByText('篇新進研報').previousSibling?.textContent).toBe('3')
    expect(screen.getByText('個市場').previousSibling?.textContent).toBe('2')
    expect(screen.getByRole('link', { name: '閱讀全文' })).toHaveAttribute('href', '/brief')
  })

  it('不是今天的簡報用頁名；來源清單被截斷時不顯示市場數', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 28, 9))
    inRouter(<BriefTile envelope={envelope('2026-10-27', [{ market: 'TW' }], 50)} failed={false} onRetry={() => {}} />)
    expect(screen.getByRole('heading', { name: '每日簡報' })).toBeInTheDocument()
    expect(screen.queryByText('個市場')).toBeNull()
  })

  it('還沒有簡報', () => {
    inRouter(<BriefTile envelope={{ status: 'pending', available_dates: [] }} failed={false} onRetry={() => {}} />)
    expect(screen.getByText('今天的簡報尚未產生')).toBeInTheDocument()
  })
})

describe('PreviewLink', () => {
  it('一般點擊開預覽；帶修飾鍵時交給瀏覽器開閱讀頁', () => {
    const onPreview = vi.fn()
    inRouter(<PreviewLink row={ROW} onPreview={onPreview}>南電</PreviewLink>)
    const link = screen.getByRole('link', { name: '南電' })
    expect(link).toHaveAttribute('href', `/report/${ROW.file_hash}`)
    expect(link).toHaveAttribute('aria-haspopup', 'dialog')
    fireEvent.click(link, { metaKey: true })
    expect(onPreview).not.toHaveBeenCalled()
    fireEvent.click(link)
    expect(onPreview).toHaveBeenCalledWith(ROW)
  })
})
