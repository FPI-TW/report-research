import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { EvalSource } from '../../lib/progressSchema'
import { ReviewQueuePanel } from './ReviewQueuePanel'
import { reasonText } from './reviewReasons'

afterEach(() => vi.unstubAllGlobals())

type Handler = (url: URL, init?: RequestInit) => { status?: number; body: unknown }

function mount(handler: Handler, scale: EvalSource | null = null, canReadContent = false) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const { status = 200, body } = handler(new URL(input, 'http://x'), init)
    return new Response(JSON.stringify(body), { status })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ReviewQueuePanel scale={scale} canReadContent={canReadContent} /></MemoryRouter>
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
  qa_id: `qa${i}`,
  created_at: '2026-09-20T03:00:00+00:00', faithfulness_score: 0.208, ...over,
})

test('預設是忠實度低分：列出問答代號、分數與日期；不顯示原文、不連到對話串', async () => {
  // 舊後端（或被竄改的回應）就算帶了原文，畫面也不印：zod 會把未宣告的鍵丟掉。
  mount(() => ({ body: page('faithfulness', [qa(1, { question: '機密提問', asked_by: 'alice', conversation_id: 'conv1' })]) }))
  await screen.findByText('問答 qa1')
  expect(screen.queryByText(/機密提問/)).not.toBeInTheDocument()
  expect(screen.queryByText(/alice/)).not.toBeInTheDocument()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
  expect(screen.getByText('0.208')).toBeInTheDocument()
  expect(screen.getByText('2026-09-20')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '忠實度低分' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByText(/門檻 0\.9/)).toBeInTheDocument()
})

test('忠實度低分列出該筆的判定尺（舊後端沒有這欄時不印）', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1, { judge_model: 'claude-haiku-4-5' }), qa(2)]) }))
  await screen.findByText('問答 qa1')
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
  expect(await screen.findByText('問答 qa3')).toBeInTheDocument()
  expect(screen.queryByText('0.208')).not.toBeInTheDocument()
})

test('還有下一頁時「載入更多」以 next_offset 接續並累加', async () => {
  const fetchMock = mount(url => (
    url.searchParams.get('offset') === '0'
      ? { body: page('faithfulness', [qa(1)], { total: 2, has_more: true, next_offset: 1 }) }
      : { body: page('faithfulness', [qa(2)], { total: 2, offset: 1 }) }
  ))
  fireEvent.click(await screen.findByRole('button', { name: '載入更多（共 2 筆）' }))
  expect(await screen.findByText('問答 qa2')).toBeInTheDocument()
  expect(screen.getByText('問答 qa1')).toBeInTheDocument()
  expect(new URL(fetchMock.mock.calls.at(-1)![0] as string, 'http://x').searchParams.get('offset')).toBe('1')
  await waitFor(() => expect(screen.queryByRole('button', { name: /載入更多/ })).not.toBeInTheDocument())
})

test('載入失敗：說出來並給重試，不讓整張卡消失', async () => {
  let fail = true
  mount(() => (fail ? { status: 500, body: { detail: 'x' } } : { body: page('faithfulness', [qa(1)]) }))
  expect(await screen.findByText(/佇列載入失敗/)).toBeInTheDocument()
  fail = false
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(await screen.findByText('問答 qa1')).toBeInTheDocument()
})

test('記錄人工驗證、已處理後從待處理消失，並可重新打開', async () => {
  let current = { status: 'open', note: '', verification: 'untested', updated_at: '2026-09-20T03:00:00Z' }
  const fetchMock = mount((url, init) => {
    if (init?.method === 'PUT') {
      current = { ...current, ...JSON.parse(init.body as string), updated_at: '2026-09-21T03:00:00Z' }
      return { body: { kind: 'feedback', subject_id: 'qa3', ...current } }
    }
    if (url.searchParams.get('kind') !== 'feedback') return { body: page('faithfulness', []) }
    const status = url.searchParams.get('status')
    return { body: page('feedback', status === current.status || status === 'all'
      ? [qa(3, { review_status: current.status, review_note: current.note,
        verification: current.verification, reviewed_at: current.updated_at })] : []) }
  })
  fireEvent.click(await screen.findByRole('tab', { name: '倒讚' }))
  await screen.findByText('問答 qa3')
  fireEvent.change(screen.getByLabelText('處理狀態'), { target: { value: 'resolved' } })
  fireEvent.change(screen.getByLabelText('人工驗證'), { target: { value: 'passed' } })
  fireEvent.change(screen.getByLabelText('處理註記'), { target: { value: '已核對原文' } })
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.queryByText('問答 qa3')).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: '已處理' }))
  await screen.findByText('問答 qa3')
  expect(screen.getByLabelText('處理註記')).toHaveValue('已核對原文')
  expect(screen.getByLabelText('人工驗證')).toHaveValue('passed')
  fireEvent.change(screen.getByLabelText('處理狀態'), { target: { value: 'open' } })
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.queryByText('問答 qa3')).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: '待處理' }))
  await screen.findByText('問答 qa3')
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')
  expect(writes).toHaveLength(2)
  expect(JSON.parse(writes[0][1]!.body as string)).toEqual({
    status: 'resolved', note: '已核對原文', verification: 'passed',
  })
  expect(JSON.parse(writes[1][1]!.body as string).status).toBe('open')
})

test('展開五頁後儲存只刷新首頁，後續分頁不漏掉因結案而前移的項目', async () => {
  let items = Array.from({ length: 41 }, (_, i) => qa(i + 1))
  const fetchMock = mount((url, init) => {
    if (init?.method === 'PUT') {
      const id = url.pathname.split('/').at(-1)
      items = items.filter(item => item.qa_id !== id)
      return { body: { kind: 'faithfulness', subject_id: id, ...JSON.parse(init.body as string),
        updated_at: '2026-09-21T03:00:00Z' } }
    }
    const offset = Number(url.searchParams.get('offset'))
    const next = offset + 10
    return { body: page('faithfulness', items.slice(offset, next), {
      total: items.length, offset, has_more: next < items.length,
      next_offset: next < items.length ? next : null,
    }) }
  })
  await screen.findByText('問答 qa1')
  for (let i = 1; i <= 4; i++) {
    fireEvent.click(screen.getByRole('button', { name: '載入更多（共 41 筆）' }))
    await screen.findByText(`問答 qa${i * 10 + 1}`)
  }
  await waitFor(() => expect(screen.queryByRole('button', { name: /載入更多/ })).not.toBeInTheDocument())
  fetchMock.mockClear()
  fireEvent.change(screen.getAllByLabelText('處理狀態')[10], { target: { value: 'resolved' } })
  fireEvent.click(screen.getAllByRole('button', { name: '儲存' })[10])
  const more = await screen.findByRole('button', { name: '載入更多（共 40 筆）' })
  await waitFor(() => expect(screen.getAllByRole('button', { name: '儲存' })[0]).toBeEnabled())
  const reads = fetchMock.mock.calls.filter(([, init]) => init?.method !== 'PUT')
  expect(reads.map(([url]) => new URL(url, 'http://x').searchParams.get('offset'))).toEqual(['0'])
  expect(screen.getAllByText(/^問答 qa/)).toHaveLength(10)
  fireEvent.click(more)
  await screen.findByText('問答 qa12')
  expect(await screen.findByText('問答 qa21')).toBeInTheDocument()
  expect(screen.queryByText('問答 qa11')).not.toBeInTheDocument()
})

test('儲存失敗保留已展開的頁面，不重新查詢清單', async () => {
  const fetchMock = mount((url, init) => {
    if (init?.method === 'PUT') return { status: 500, body: {} }
    const offset = Number(url.searchParams.get('offset'))
    return { body: page('faithfulness', [qa(offset + 1)], {
      total: 2, offset, has_more: offset === 0, next_offset: offset === 0 ? 1 : null,
    }) }
  })
  fireEvent.click(await screen.findByRole('button', { name: '載入更多（共 2 筆）' }))
  await screen.findByText('問答 qa2')
  fetchMock.mockClear()
  fireEvent.click(screen.getAllByRole('button', { name: '儲存' })[0])
  expect(await screen.findByRole('alert')).toHaveTextContent('儲存失敗，請重試')
  expect(screen.getByText('問答 qa1')).toBeInTheDocument()
  expect(screen.getByText('問答 qa2')).toBeInTheDocument()
  expect(fetchMock.mock.calls).toHaveLength(1)
  expect(fetchMock.mock.calls[0][1]?.method).toBe('PUT')
})

test('原因帶著量到的值：分數不低的研報看得出為什麼在這裡', () => {
  const item = { quality_score: 0.93, quality_flags: { layout_coverage: 0.12, garbled_ratio: 0.034 }, pages_failed: null }
  expect(reasonText(item, 'low_coverage')).toBe('版面覆蓋率 12%')
  expect(reasonText(item, 'garbled')).toBe('亂碼率 3.4%')
  // 旗標缺值時不編數字；後端新增的未知原因原樣顯示，不讓整列消失。
  expect(reasonText({ quality_flags: null }, 'low_coverage')).toBe('版面覆蓋率過低')
  expect(reasonText({}, 'something_new')).toBe('something_new')
})

test('問答列顯示提問者代號與處理人；共用帳號時期的舊列標「共用帳號」、沒處理過不印處理人', async () => {
  mount(() => ({ body: page('faithfulness', [
    qa(1, { asker_code: '3fa9c2d1', reviewer: 'root', reviewed_at: '2026-10-04T08:00:00Z', review_status: 'resolved' }),
    qa(2, { asker_code: null, reviewer: null }),
  ]) }))
  await screen.findByText('問答 qa1')
  expect(screen.getByText('提問者 #3fa9c2d1')).toBeInTheDocument()
  expect(screen.getByText('處理人 root（2026-10-04）')).toBeInTheDocument()
  expect(screen.getByText('提問者 共用帳號')).toBeInTheDocument()
  expect(screen.getAllByText(/^處理人 /)).toHaveLength(1)
})

test('研報列也顯示處理人', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'extraction'
      ? page('extraction', [{ report_id: 'r9', file_hash: 'a'.repeat(64), file_name: 'z.pdf', review_reasons: [],
        reviewer: 'bob', reviewed_at: '2026-10-03T01:00:00Z' }])
      : page('faithfulness', []),
  }))
  fireEvent.click(await screen.findByRole('tab', { name: '抽取品質' }))
  expect(await screen.findByText('處理人 bob（2026-10-03）')).toBeInTheDocument()
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
  await screen.findByText('問答 qa1')
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

test('沒有 qa_content.read：問答列沒有「查看內容」，也不會打讀取端點', async () => {
  const fetchMock = mount(() => ({ body: page('faithfulness', [qa(1)]) }))
  await screen.findByText('問答 qa1')
  expect(screen.queryByRole('button', { name: '查看內容' })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/access'))).toBe(false)
})

test('有 qa_content.read：按下才 POST 取這一筆，收起即丟掉、再看要再讀一次', async () => {
  const fetchMock = mount((url, init) => {
    if (url.pathname === '/api/review/qa/qa1/access') {
      expect(init?.method).toBe('POST')
      return { body: { qa_id: 'qa1', kinds: ['faithfulness'], created_at: null, question: '台積電目標價？', answer: '1,500 元 [1]' } }
    }
    return { body: page('faithfulness', [qa(1)]) }
  }, null, true)
  await screen.findByText('問答 qa1')
  // 載入佇列時不預先讀內容
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/access'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '查看內容' }))
  expect(await screen.findByText('台積電目標價？')).toBeInTheDocument()
  expect(screen.getByText('1,500 元 [1]')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '收起' }))
  expect(screen.queryByText('台積電目標價？')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '查看內容' }))
  await screen.findByText('台積電目標價？')
  expect(fetchMock.mock.calls.filter(([u]) => String(u).includes('/access'))).toHaveLength(2)
})

test('讀取端點回 404（已不在佇列）時說出來，不顯示任何內容', async () => {
  mount(url => (
    url.pathname.endsWith('/access')
      ? { status: 404, body: { detail: '待複核項目不存在', code: 'not_found' } }
      : { body: page('feedback', [qa(3, { feedback: 'dislike' })]) }
  ), null, true)
  fireEvent.click(await screen.findByRole('tab', { name: '倒讚' }))
  fireEvent.click(await screen.findByRole('button', { name: '查看內容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('已不在待複核佇列')
})

test('抽取品質列不會有「查看內容」', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'extraction'
      ? page('extraction', [{ report_id: 'r9', file_hash: 'a'.repeat(64), file_name: 'z.pdf', review_reasons: [] }])
      : page('faithfulness', []),
  }), null, true)
  fireEvent.click(await screen.findByRole('tab', { name: '抽取品質' }))
  await screen.findByRole('link', { name: 'z.pdf' })
  expect(screen.queryByRole('button', { name: '查看內容' })).not.toBeInTheDocument()
})
