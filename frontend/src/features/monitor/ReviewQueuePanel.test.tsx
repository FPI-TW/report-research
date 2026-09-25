import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { EvalSource } from './progressSchema'
import { ReviewQueuePanel } from './ReviewQueuePanel'
import { reasonText } from './reviewReasons'

afterEach(() => vi.unstubAllGlobals())

type Handler = (url: URL) => { status?: number; body: unknown }

function mount(handler: Handler, scale: EvalSource | null = null) {
  const fetchMock = vi.fn(async (input: string) => {
    const { status = 200, body } = handler(new URL(input, 'http://x'))
    return new Response(JSON.stringify(body), { status })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ReviewQueuePanel scale={scale} /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

function page(kind: string, items: unknown[], over: Record<string, unknown> = {}) {
  return {
    kind, total: items.length, limit: 10, offset: 0, has_more: false, next_offset: null,
    min_score: kind === 'faithfulness' ? 0.9 : null, items, ...over,
  }
}

const qa = (i: number, over: Record<string, unknown> = {}) => ({
  qa_id: `qa${i}`, conversation_id: `conv${i}`, question: `提問 ${i}`,
  created_at: '2026-09-20T03:00:00+00:00', faithfulness_score: 0.208, ...over,
})

test('預設是忠實度低分：列出提問、分數與日期，並連回那一串對話', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1)]) }))
  const link = await screen.findByRole('link', { name: '提問 1' })
  expect(link).toHaveAttribute('href', '/ask?c=conv1')
  expect(screen.getByText('0.208')).toBeInTheDocument()
  expect(screen.getByText('2026-09-20')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '忠實度低分' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByText(/門檻 0\.9/)).toBeInTheDocument()
})

test('忠實度低分列出該筆的判定尺（舊後端沒有這欄時不印）', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1, { judge_model: 'claude-haiku-4-5' }), qa(2)]) }))
  await screen.findByRole('link', { name: '提問 1' })
  expect(screen.getAllByText('claude-haiku-4-5')).toHaveLength(1)
})

test('切到抽取品質：以 kind=extraction 重新取數，研報連到閱讀頁、標題缺值回退檔名', async () => {
  const fetchMock = mount(url => (
    url.searchParams.get('kind') === 'extraction'
      ? { body: page('extraction', [{
          report_id: 'r1', file_hash: 'h'.repeat(64), file_name: 'a.pdf', title: null,
          source: '凱基', quality_score: 0.41, pages_failed: [2, 7],
          review_reasons: ['pages_failed', 'low_score'],
        }]) }
      : { body: page('faithfulness', []) }
  ))
  await screen.findByText('沒有待複核的項目')
  fireEvent.click(screen.getByRole('tab', { name: '抽取品質' }))
  const link = await screen.findByRole('link', { name: 'a.pdf' })
  expect(link).toHaveAttribute('href', `/report/${'h'.repeat(64)}`)
  expect(screen.getByText('品質分數 0.41')).toBeInTheDocument()
  expect(screen.getByText('2 頁抽取失敗')).toBeInTheDocument()
  expect(new URL(fetchMock.mock.calls.at(-1)![0] as string, 'http://x').searchParams.get('kind')).toBe('extraction')
})

test('倒讚分頁不顯示分數（那一欄對它沒有意義）', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'feedback'
      ? page('feedback', [qa(3, { faithfulness_score: null, feedback: 'dislike' })])
      : page('faithfulness', []),
  }))
  fireEvent.click(await screen.findByRole('tab', { name: '倒讚' }))
  expect(await screen.findByRole('link', { name: '提問 3' })).toBeInTheDocument()
  expect(screen.queryByText('0.208')).not.toBeInTheDocument()
})

test('還有下一頁時「載入更多」以 next_offset 接續並累加', async () => {
  const fetchMock = mount(url => (
    url.searchParams.get('offset') === '0'
      ? { body: page('faithfulness', [qa(1)], { total: 2, has_more: true, next_offset: 1 }) }
      : { body: page('faithfulness', [qa(2)], { total: 2, offset: 1 }) }
  ))
  fireEvent.click(await screen.findByRole('button', { name: '載入更多（共 2 筆）' }))
  expect(await screen.findByRole('link', { name: '提問 2' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '提問 1' })).toBeInTheDocument()
  expect(new URL(fetchMock.mock.calls.at(-1)![0] as string, 'http://x').searchParams.get('offset')).toBe('1')
  await waitFor(() => expect(screen.queryByRole('button', { name: /載入更多/ })).not.toBeInTheDocument())
})

test('載入失敗：說出來並給重試，不讓整張卡消失', async () => {
  let fail = true
  mount(() => (fail ? { status: 500, body: { detail: 'x' } } : { body: page('faithfulness', [qa(1)]) }))
  expect(await screen.findByText(/佇列載入失敗/)).toBeInTheDocument()
  fail = false
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(await screen.findByRole('link', { name: '提問 1' })).toBeInTheDocument()
})

test('原因帶著量到的值：分數不低的研報看得出為什麼在這裡', () => {
  const item = { quality_score: 0.93, quality_flags: { layout_coverage: 0.12, garbled_ratio: 0.034 }, pages_failed: null }
  expect(reasonText(item, 'low_coverage')).toBe('版面覆蓋率 12%')
  expect(reasonText(item, 'garbled')).toBe('亂碼率 3.4%')
  // 旗標缺值時不編數字；後端新增的未知原因原樣顯示，不讓整列消失。
  expect(reasonText({ quality_flags: null }, 'low_coverage')).toBe('版面覆蓋率過低')
  expect(reasonText({}, 'something_new')).toBe('something_new')
})

test('以現行門檻已不需複核的研報要說出來，不留一格空白', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'extraction'
      ? page('extraction', [{ report_id: 'r9', file_hash: 'a'.repeat(64), file_name: 'z.pdf', review_reasons: [] }])
      : page('faithfulness', []),
  }))
  fireEvent.click(await screen.findByRole('tab', { name: '抽取品質' }))
  expect(await screen.findByText('現行門檻下已達標')).toBeInTheDocument()
})

const scaleBase: EvalSource = {
  total: 9, checked: 9, degraded: 0, below_min: 1, avg_score: 0.95, latest: '2026-09-26',
  judge_model: 'deepseek-flash', judge_since: '2026-09-25', other_judge_checked: 6, judge_checked: 3, avg_n: 3,
}

test('判定尺剛換成 DeepSeek、窗期內還有舊尺 → 忠實度分頁比照監控卡標新量尺', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1)]) }), scaleBase)
  await screen.findByRole('link', { name: '提問 1' })
  expect(screen.getByText(/判定尺 deepseek-flash 是新量尺（自 2026-09-25 起，DeepSeek）/)).toBeInTheDocument()
})

test('新尺尚無查核 → 新量尺但不編日期', async () => {
  mount(() => ({ body: page('faithfulness', []) }), { ...scaleBase, judge_since: null, judge_checked: 0 })
  await screen.findByText('沒有待複核的項目')
  expect(screen.getByText(/新量尺（尚無查核，DeepSeek）/)).toBeInTheDocument()
})

test('窗期內已全是新尺、或沒有量尺資料 → 不標新量尺', async () => {
  mount(() => ({ body: page('faithfulness', []) }), { ...scaleBase, other_judge_checked: 0 })
  await screen.findByText('沒有待複核的項目')
  expect(screen.queryByText(/新量尺/)).not.toBeInTheDocument()
})

test('新量尺標示只在忠實度分頁：切到倒讚就不印', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'feedback' ? page('feedback', []) : page('faithfulness', []),
  }), scaleBase)
  expect(await screen.findByText(/新量尺/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: '倒讚' }))
  await waitFor(() => expect(screen.queryByText(/新量尺/)).not.toBeInTheDocument())
})
