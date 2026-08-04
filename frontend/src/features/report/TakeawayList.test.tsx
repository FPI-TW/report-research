import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Takeaway } from '../../lib/readingSchemas'
import { TakeawayList } from './TakeawayList'

/**
 * 這支測試檔存在的理由：`ReportPage.test.tsx` 目前混著他人未提交的 WIP，
 * 而摘錄條目的**可及性與狀態呈現**必須有守門——它是這一頁對輔助技術唯一可讀的
 * 研報內容（PDF 內文是 canvas/img，DOM 裡沒有文字節點）。
 */
const TK: Takeaway[] = [
  { ordinal: 1, claim: '重申買進評級', quote: '視 NYPCB 為基板族群首選', quote_start: 2, quote_end: 8, anchor_method: 'exact' },
  { ordinal: 2, claim: '亞洲兩家基板廠停止接 BT 訂單', quote: null, quote_start: null, quote_end: null, anchor_method: null },
]

describe('TakeawayList', () => {
  it('有引文且可跳 → 是按鈕，且**可及名稱包含條目內容**', () => {
    render(<TakeawayList takeaways={TK} canJump onJump={() => {}} />)
    // aria-label 會覆蓋後代內容算出的名稱，按鈕又是可及性樹的葉節點 ——
    // 掛上去等於把 claim 與逐字引文對螢幕閱讀器整個關掉。這條反向釘死那件事。
    const btn = screen.getByRole('button', { name: /重申買進評級/ })
    expect(btn).toBeInTheDocument()
    expect(btn).toHaveAccessibleName(/視 NYPCB 為基板族群首選/)
    expect(btn).toHaveAccessibleName(/在原文中尋找這段引文/)
  })

  it('沒有引文 → 非互動（claim 是 LLM 轉述，拿去搜 PDF 幾乎必然落空）', () => {
    render(<TakeawayList takeaways={TK} canJump onJump={() => {}} />)
    expect(screen.getByText('亞洲兩家基板廠停止接 BT 訂單')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /亞洲兩家/ })).toBeNull()
  })

  // 不可跳時整批塌成 div，而不是 disabled button：disabled 預設不可聚焦，
  // 螢幕閱讀器會整條跳過，讀者連這條摘錄存在都不知道。
  it('canJump 為否 → 全部非互動，但內容仍讀得到', () => {
    render(<TakeawayList takeaways={TK} canJump={false} onJump={() => {}} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('重申買進評級')).toBeInTheDocument()
    expect(screen.getByText('視 NYPCB 為基板族群首選')).toBeInTheDocument()
  })

  it('點擊把該條交給呼叫端', () => {
    const onJump = vi.fn()
    render(<TakeawayList takeaways={TK} canJump onJump={onJump} />)
    fireEvent.click(screen.getByRole('button', { name: /重申買進評級/ }))
    expect(onJump).toHaveBeenCalledWith(TK[0])
  })

  it('尋找中 → 該條顯示進行中，其他條不顯示', () => {
    render(<TakeawayList takeaways={TK} canJump pendingOrdinal={1} onJump={() => {}} />)
    expect(screen.getAllByText('尋找中…')).toHaveLength(1)
  })

  it('找到單一命中 → 畫面不標頁碼，但播報區要說', () => {
    render(
      <TakeawayList
        takeaways={TK}
        canJump
        result={{ nonce: 1, ok: true, page: 12, total: 1, ordinal: 1 }}
        onJump={() => {}}
      />,
    )
    expect(screen.queryByText(/共 /)).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('已在第 12 頁找到')
  })

  it('多重命中 → 標「共 N 處」（跳到的是第一處，未必是引用的那一處）', () => {
    render(
      <TakeawayList
        takeaways={TK}
        canJump
        result={{ nonce: 1, ok: true, page: 12, total: 2, ordinal: 1 }}
        onJump={() => {}}
      />,
    )
    expect(screen.getByText('共 2 處')).toBeInTheDocument()
  })

  // 找不到是可呈現的結果，不是故障 —— 沒有任何路徑通往「什麼都沒發生」
  it('找不到 → 條目上明說，播報區也說', () => {
    render(
      <TakeawayList
        takeaways={TK}
        canJump
        result={{ nonce: 1, ok: false, ordinal: 1 }}
        onJump={() => {}}
      />,
    )
    expect(screen.getAllByText('原文中找不到這段文字')).toHaveLength(2)
    expect(screen.getByRole('status').textContent).toBe('原文中找不到這段文字')
  })

  // 兩條摘錄命中同一頁時結果字串完全相同，live region 內容沒變就不會重播 ——
  // 第二次點擊對螢幕閱讀器等於零回饋。key 帶 nonce 讓節點重建才會再播。
  it('連續兩次結果字串相同時，播報節點會重建', () => {
    const { rerender } = render(
      <TakeawayList takeaways={TK} canJump result={{ nonce: 1, ok: true, page: 12, total: 1, ordinal: 1 }} onJump={() => {}} />,
    )
    const first = screen.getByRole('status')
    rerender(
      <TakeawayList takeaways={TK} canJump result={{ nonce: 2, ok: true, page: 12, total: 1, ordinal: 2 }} onJump={() => {}} />,
    )
    expect(screen.getByRole('status')).not.toBe(first)
  })
})
