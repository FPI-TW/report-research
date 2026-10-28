import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { AssistantMessage } from './AssistantMessage'
import type { Turn } from '../../lib/askReducer'
import { TIME_SENSITIVE_WEB_HINTS } from '../../lib/useWebSearch'
import { setFeatures } from '../../lib/useFeatures'

function makeTurn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '答案 [1] 內容', thinkingMs: 3000, startedAt: 0,
    sources: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }],
    extSources: [], qaId: 'qa1', isOfftopic: false, noticeText: null, noticeKind: null,
    feedback: null,
    errorText: null, followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1, queuePosition: null,
    ...over,
  }
}
const noop = {
  onCite: vi.fn(),
  onOpenSources: vi.fn(),
  onFeedback: vi.fn(),
  onNoticeRetry: vi.fn(),
  onErrorRetry: vi.fn(),
  onRegenerate: vi.fn(),
  onFollowup: vi.fn(),
  onSetVersion: vi.fn(),
}

test('done：答案＋引用膠囊＋來源卡片＋「全部 N 則來源」＋讚/倒讚', () => {
  const onFeedback = vi.fn(); const onOpenSources = vi.fn(); const onCite = vi.fn()
  render(<AssistantMessage turn={makeTurn({})} {...noop} onCite={onCite} onOpenSources={onOpenSources} onFeedback={onFeedback} />)
  fireEvent.click(screen.getByRole('button', { name: '1' })); expect(onCite).toHaveBeenCalledWith(1, expect.objectContaining({ sources: expect.any(Array) }))
  fireEvent.click(screen.getByRole('button', { name: '全部 1 則來源' })); expect(onOpenSources).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '讚' })); expect(onFeedback).toHaveBeenCalledWith('like')
  expect(screen.queryByRole('button', { name: /外部參考/ })).toBeNull() // 無獨立外部參考鈕
})

test('來源卡片只列前 3 則（研報與網路補充同一份清單），點卡片＝點那個引用編號', () => {
  const onCite = vi.fn()
  const src = (n: number) => ({ n, report_id: `r${n}`, file_name: `第${n}篇.pdf`, market: 'TW', report_date: '2026-06-20', is_latest: false })
  const turn = makeTurn({ sources: [src(1), src(2), src(3)], extSources: [{ title: '外部新聞', url: 'https://a.example.com/x' }] })
  render(<AssistantMessage turn={turn} {...noop} onCite={onCite} />)
  expect(screen.getByRole('button', { name: /第3篇\.pdf/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /外部新聞/ })).toBeNull()
  expect(screen.getByRole('button', { name: '全部 4 則來源' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /第2篇\.pdf/ }))
  expect(onCite).toHaveBeenCalledWith(2, expect.objectContaining({ qaId: 'qa1' }))
})

test('沒有任何來源時不出現來源列', () => {
  render(<AssistantMessage turn={makeTurn({ sources: [], answer: '沒有引用的回答' })} {...noop} />)
  expect(screen.queryByRole('button', { name: /則來源/ })).toBeNull()
})

test('讚/倒讚是切換鈕：再點已亮起的那顆傳 null（取消），點另一顆仍傳自己的值', () => {
  // 沒有取消，誤按就只剩「按另一顆」這條假出口，錯的評價會留在 qa_log 裡。
  const onFeedback = vi.fn()
  render(<AssistantMessage turn={makeTurn({ feedback: 'like' })} {...noop} onFeedback={onFeedback} />)
  const like = screen.getByRole('button', { name: '讚' })
  expect(like).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: '倒讚' })).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(like)
  expect(onFeedback).toHaveBeenLastCalledWith(null)
  fireEvent.click(screen.getByRole('button', { name: '倒讚' }))
  expect(onFeedback).toHaveBeenLastCalledWith('dislike')
})

test('動作鈕是定位元素：popLayout 退場圖示才不會飛出畫面', () => {
  // 複製鈕換圖時，退場那顆會被 motion 套上 position:absolute（依 offsetParent 量到的座標）。
  // 按鈕若退回 static，offsetParent 會變成 AskPage 的 .column，而捲動的是它裡面的 .flow
  // ——捲過的對話按下複製，圖示就飛走。壞掉時完全沒有錯誤訊息，故在此釘死。
  render(<AssistantMessage turn={makeTurn({})} {...noop} />)
  expect(getComputedStyle(screen.getByRole('button', { name: '複製回答' })).position).toBe('relative')
})

test('時效婉拒不給「換個說法重新提問」：缺的是資料不是措辭', () => {
  // 換說法不會讓系統生出它沒有的即時資料，只會讓使用者反覆改寫同一題，
  // 每一次再吃一輪完整檢索。離題題才是換個說法就有救（見下一題）。
  render(<AssistantMessage turn={makeTurn({ phase: 'notice', isOfftopic: true, noticeKind: 'time_sensitive', noticeText: '需要即時行情或最新公告資料', qaId: null })} {...noop} />)
  expect(screen.getByText('需要即時行情或最新公告資料')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '換個說法重新提問' })).toBeNull()
})

test('網搜暫停中：時效婉拒不叫使用者去開一顆畫面上沒有的網搜開關', () => {
  const base = '需要即時行情或最新公告資料。'
  render(<AssistantMessage turn={makeTurn({ phase: 'notice', isOfftopic: true, noticeKind: 'time_sensitive', noticeText: base + TIME_SENSITIVE_WEB_HINTS[0], qaId: null })} {...noop} />)
  // 還沒抓到 /api/features（store 是空的）＝暫停：提示被剝掉
  expect(screen.queryByText(/網路搜尋/)).toBeNull()
  expect(screen.getByText(new RegExp(base))).toBeTruthy()
})

test('網搜開著（旗標 ask.web_search 為 true）：時效婉拒原樣顯示提示', () => {
  setFeatures({ 'ask.web_search': true })
  try {
    const base = '需要即時行情或最新公告資料。'
    render(<AssistantMessage turn={makeTurn({ phase: 'notice', isOfftopic: true, noticeKind: 'time_sensitive', noticeText: base + TIME_SENSITIVE_WEB_HINTS[0], qaId: null })} {...noop} />)
    expect(screen.getByText(/網路搜尋/)).toBeTruthy()
  } finally {
    setFeatures(null)
  }
})

test('notice：Callout warning + 換個說法重新提問', () => {
  const onNoticeRetry = vi.fn()
  render(<AssistantMessage turn={makeTurn({ phase: 'notice', isOfftopic: true, noticeText: '無法回答此問題', qaId: null })} {...noop} onNoticeRetry={onNoticeRetry} />)
  expect(screen.getByRole('alert')).toHaveTextContent('無法回答此問題')
  fireEvent.click(screen.getByRole('button', { name: '換個說法重新提問' })); expect(onNoticeRetry).toHaveBeenCalled()
})

test('error：失敗列（回答沒有完成＋後端給的原因）＋重試；思考區寫「思考已中斷」', () => {
  const onErrorRetry = vi.fn()
  render(<AssistantMessage turn={makeTurn({ phase: 'error', answer: '', stages: ['understanding', 'retrieved'], errorText: '此題觸發模型供應商的內容審查，可換個問法' })} {...noop} onErrorRetry={onErrorRetry} />)
  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('回答沒有完成')
  expect(alert).toHaveTextContent('此題觸發模型供應商的內容審查，可換個問法')
  expect(screen.getByText('思考已中斷')).toBeInTheDocument()
  // 失敗不給讚、複製、來源卡片：沒有完成的回答不該被評價或引用
  expect(screen.queryByRole('button', { name: '讚' })).toBeNull()
  expect(screen.queryByRole('button', { name: /則來源/ })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onErrorRetry).toHaveBeenCalled()
})

test('error 但另一輪還在跑：重試鈕停用（同時只能有一條串流）', () => {
  render(<AssistantMessage turn={makeTurn({ phase: 'error', answer: '', errorText: 'x' })} {...noop} disabled />)
  expect(screen.getByRole('button', { name: '重試' })).toBeDisabled()
})

test('error 但有部分答案：保留答案本文＋錯誤提示＋重試（不抹除已串出的內容）', () => {
  const onErrorRetry = vi.fn(); const onCite = vi.fn()
  const { container } = render(<AssistantMessage turn={makeTurn({ phase: 'error', answer: '已串出的半句回答 [1]', qaId: null, errorText: '查詢逾時或失敗' })} {...noop} onErrorRetry={onErrorRetry} onCite={onCite} />)
  expect(screen.getByText(/已串出的半句回答/)).toBeInTheDocument() // 部分答案本文保留顯示
  expect(container.querySelector('[data-streaming]')).toBeNull()   // 已中斷、非串流，不顯示游標
  fireEvent.click(screen.getByRole('button', { name: '1' }))
  expect(onCite).toHaveBeenCalledWith(1, expect.objectContaining({ qaId: null }))
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onErrorRetry).toHaveBeenCalled() // 錯誤提示仍在
})

test('串流中：本文帶 data-streaming（行內游標鉤），文字切成逐段淡入的 span', () => {
  const { container } = render(
    <AssistantMessage turn={makeTurn({ phase: 'streaming', answer: '生成中的內容', qaId: null })} {...noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeTruthy()
  expect(container.querySelectorAll('[data-streaming] p span[class*="tok"]')).toHaveLength(6)
})

test('串流收尾：文字段不重新掛載也不再切段（DOM 回到純文字）', () => {
  const t = makeTurn({ phase: 'streaming', answer: '生成中', qaId: null })
  const { container, rerender } = render(<AssistantMessage turn={t} {...noop} />)
  const first = container.querySelector('p span')
  rerender(<AssistantMessage turn={{ ...t, answer: '生成中的內容' }} {...noop} />)
  // 已出現的段落是同一個節點：淡入只在新段落掛載時播一次
  expect(container.querySelector('p span')).toBe(first)
  rerender(<AssistantMessage turn={{ ...t, phase: 'done', answer: '生成中的內容' }} {...noop} />)
  expect(container.querySelector('p span[class*="tok"]')).toBeNull()
  expect(container.querySelector('p')!.textContent).toBe('生成中的內容')
})

test('done：本文不帶 data-streaming', () => {
  const { container } = render(
    <AssistantMessage turn={makeTurn({})} {...noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeNull()
})

test('stopped phase shows partial answer, 已停止 marker and regenerate', async () => {
  const onRegenerate = vi.fn()
  const turn = makeTurn({ phase: 'stopped', answer: '部分答案', qaId: 'q1' })
  render(<AssistantMessage turn={turn} {...noop} onRegenerate={onRegenerate} />)
  expect(screen.getByText(/部分答案/)).toBeInTheDocument()
  expect(screen.getByText(/已停止/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /重新生成/ }))
  expect(onRegenerate).toHaveBeenCalledOnce()
})

test('延伸提問：最後一輪才列，點一列就送出那一題', async () => {
  const onFollowup = vi.fn()
  const turn = makeTurn({ phase: 'done', qaId: 'q1', followups: ['追問一'] })
  const { rerender } = render(<AssistantMessage turn={turn} {...noop} onFollowup={onFollowup} />)
  expect(screen.getByText('延伸提問')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '追問一' }))
  expect(onFollowup).toHaveBeenCalledWith('追問一')
  rerender(<AssistantMessage turn={turn} {...noop} onFollowup={onFollowup} isLast={false} />)
  expect(screen.queryByRole('button', { name: '追問一' })).toBeNull()
})

test('version pager shows when versionCount > 1', () => {
  const turn = makeTurn({ phase: 'done', qaId: 'q2', versionCount: 2, versionIndex: 1 })
  render(<AssistantMessage turn={turn} {...noop} />)
  expect(screen.getByText('2 / 2')).toBeInTheDocument()
})

test('重載最新版（priorVersions 尚未載入）：pager 標籤與內容不錯位，回饋鈕顯示', () => {
  const onFeedback = vi.fn()
  const turn = makeTurn({ phase: 'done', qaId: 'q2', priorVersions: [], versionIndex: 1, versionCount: 2, answer: '最新' })
  render(<AssistantMessage turn={turn} {...noop} onFeedback={onFeedback} />)
  expect(screen.getByText('2 / 2')).toBeInTheDocument()
  expect(screen.getByText('最新')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '讚' }))
  expect(onFeedback).toHaveBeenCalledWith('like')
})

test('檢視舊版快照（非 live）：回饋鈕隱藏，追問 chips 隱藏', () => {
  const turn = makeTurn({
    phase: 'done', qaId: 'q2', versionCount: 2, versionIndex: 0, followups: ['追問一'],
    priorVersions: [{ answer: '舊答案', sources: [], extSources: [], qaId: 'q1', thinkingMs: null, stages: [], feedback: null, followups: [], stopped: false }],
  })
  render(<AssistantMessage turn={turn} {...noop} />)
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
  expect(screen.getByText(/舊答案/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '讚' })).toBeNull()
  expect(screen.queryByRole('button', { name: '倒讚' })).toBeNull()
  expect(screen.queryByRole('button', { name: '追問一' })).toBeNull()
})

test('檢視舊版快照時，資料來源操作帶出該版來源', () => {
  const onOpenSources = vi.fn()
  const oldSource = { n: 1, report_id: 'old-r1', file_name: '舊版.pdf', market: 'TW', report_date: '2025-01-01', is_latest: false }
  const turn = makeTurn({
    phase: 'done', qaId: 'q2', versionCount: 2, versionIndex: 0,
    priorVersions: [{ answer: '舊答案', sources: [oldSource], extSources: [], qaId: 'q1', thinkingMs: null, stages: [], feedback: null, followups: [], stopped: false }],
  })
  render(<AssistantMessage turn={turn} {...noop} onOpenSources={onOpenSources} />)

  fireEvent.click(screen.getByRole('button', { name: '全部 1 則來源' }))
  expect(onOpenSources).toHaveBeenCalledWith(expect.objectContaining({ sources: [oldSource] }))
})

test('「已停止生成」自成一行：行內元素會黏到前一個行內元素旁邊漂著', () => {
  // 2026-08-03 實際回報的版面缺陷：.stopped 原是 inline-block，與思考區同行並排、
  // 「已停止」浮在右側。現在是區塊層級的 flex 膠囊（寬度貼內容），釘死 display，回歸即紅。
  render(<AssistantMessage turn={makeTurn({ phase: 'stopped', answer: '' })} {...noop} />)
  const marker = screen.getByText('已停止生成')
  expect(getComputedStyle(marker).display).toBe('flex')
})

test('停止在一個字都還沒寫出來時，仍可重新生成（只是沒有東西可複製）', () => {
  render(<AssistantMessage turn={makeTurn({ phase: 'stopped', answer: '' })} {...noop} />)
  expect(screen.getByRole('button', { name: '重新生成' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '複製回答' })).toBeNull()
})

test('複製回答：只複製本文，不附來源清單', async () => {
  const clipboard = await import('../../lib/clipboard')
  const spy = vi.spyOn(clipboard, 'copyText').mockResolvedValue()
  render(<AssistantMessage turn={makeTurn({ answer: '展望正向 [1]。' })} {...noop} />)
  fireEvent.click(screen.getByRole('button', { name: '複製回答' }))
  expect(spy).toHaveBeenCalledWith('展望正向 [1]。')
  spy.mockRestore()
})
