import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, useLocation } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../components/console'
import type { JudgeScale } from '../../lib/reviewSchemas'
import { ReviewQueuePanel } from './ReviewQueuePanel'
import { reasonText } from './reviewReasons'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

type Handler = (url: URL, init?: RequestInit) => { status?: number; body: unknown }
type FetchMock = { mock: { calls: [string, RequestInit?][] } }

/** 把目前的 query string 印出來給斷言用。刻意不用 <output>：它自帶 role=status，會和 toast 撞名。 */
function Search() {
  return <span data-testid="search">{useLocation().search}</span>
}

function mount(handler: Handler, { scale = null, canReadContent = false, entry = '/admin/reviews' }: {
  scale?: JudgeScale | null; canReadContent?: boolean; entry?: string
} = {}) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const { status = 200, body } = handler(new URL(input, 'http://x'), init)
    return new Response(JSON.stringify(body), { status })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // 與 App.tsx 同一種 router（data router＋react-router/dom 的 RouterProvider）：導航的 flushSync 只在這種
  // router 生效，用 <MemoryRouter> 會讓「儲存後同步取消選取」在測試裡靜默變回 transition。
  const router = createMemoryRouter([{
    path: '*',
    element: <>
      <ReviewQueuePanel scale={scale} canReadContent={canReadContent} />
      <Toaster />
      <Search />
    </>,
  }], { initialEntries: [entry] })
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
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

const queue = () => within(screen.getByRole('list', { name: '待複核清單' }))
const detail = () => within(screen.getByRole('region', { name: '詳情' }))
/** 清單裡的一筆（標題那一格；點它等於點那一列的按鈕）。清單還沒出來就等。 */
const findRow = (subject: string) => waitFor(() => queue().getByText(subject))
const rowButton = (subject: string) => queue().getByText(subject).closest('button')!
const seg = (group: string, name: string) => within(screen.getByRole('group', { name: group })).getByRole('button', { name })
const search = () => screen.getByTestId('search').textContent
/** 中繼資料格：某個 dt 對應的 dd 文字；沒有這一格回 null。 */
const metaValue = (term: string) => detail().queryByText(term, { selector: 'dt' })?.nextElementSibling?.textContent ?? null

const parse = (u: string) => new URL(u, 'http://x')
/** 清單的取數（不含分頁籤上待處理數的 limit=1 請求，也不含寫入）。 */
const listReads = (f: FetchMock) => f.mock.calls
  .filter(([u, init]) => parse(u).pathname === '/api/review/queue' && parse(u).searchParams.get('limit') !== '1' && init?.method !== 'PUT')
  .map(([u]) => parse(u).searchParams)
const countReads = (f: FetchMock) => f.mock.calls.filter(([u]) => parse(u).searchParams.get('limit') === '1')
const accessCalls = (f: FetchMock) => f.mock.calls.filter(([u]) => String(u).includes('/access'))

const extractionItem = (over: Record<string, unknown> = {}) => ({
  report_id: 'r9', file_hash: 'a'.repeat(64), file_name: 'z.pdf', review_reasons: [], ...over,
})
const extractionOnly = (items: unknown[]): Handler => url => ({
  body: url.searchParams.get('kind') === 'extraction' ? page('extraction', items) : page('faithfulness', []),
})

test('預設是忠實度低分：列出問答代號、分數與日期，右邊是第一筆的詳情；不顯示原文、不連到對話串', async () => {
  // 舊後端（或被竄改的回應）就算帶了原文，畫面也不印：zod 會把未宣告的鍵丟掉。
  mount(() => ({ body: page('faithfulness', [qa(1, { question: '機密提問', asked_by: 'alice', conversation_id: 'conv1' })]) }))
  await findRow('問答 qa1')
  expect(screen.queryByText(/機密提問/)).not.toBeInTheDocument()
  expect(screen.queryByText(/alice/)).not.toBeInTheDocument()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
  expect(queue().getByText('0.208')).toBeInTheDocument()
  expect(queue().getByText('提問者 共用帳號・2026-09-20')).toBeInTheDocument()
  expect(queue().getByText('待處理')).toBeInTheDocument()
  expect(rowButton('問答 qa1')).toHaveAttribute('aria-current', 'true')
  expect(detail().getByRole('heading', { level: 2, name: '問答 qa1' })).toBeInTheDocument()
  expect(metaValue('忠實度')).toBe('0.208')
  expect(metaValue('日期')).toBe('2026-09-20')
  expect(screen.getByRole('tab', { name: /^忠實度低分/ })).toHaveAttribute('aria-selected', 'true')
  expect(seg('複核狀態', '待處理')).toHaveAttribute('aria-pressed', 'true')
  // 門檻取 API 回的 min_score，不寫死
  expect(screen.getByText(/最低分在前；只列現行判定尺量的分數（門檻 0\.90）。/)).toBeInTheDocument()
})

test('沒有 min_score 時說明不編門檻', async () => {
  mount(() => ({ body: page('faithfulness', [], { min_score: null }) }))
  expect(await screen.findByText('目前沒有項目')).toBeInTheDocument()
  expect(screen.getByText(/只列現行判定尺量的分數。$/)).toBeInTheDocument()
  expect(screen.getByText('從左邊選一筆')).toBeInTheDocument()
})

test('門檻設得比兩位小數細時原樣顯示，不四捨五入成另一個門檻', async () => {
  mount(() => ({ body: page('faithfulness', [], { min_score: 0.875 }) }))
  expect(await screen.findByText(/（門檻 0\.875）。$/)).toBeInTheDocument()
})

test('忠實度低分的詳情列出該筆的判定尺（舊後端沒有這欄時不印）', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1, { judge_model: 'claude-haiku-4-5' }), qa(2)]) }))
  await findRow('問答 qa1')
  expect(metaValue('判定尺')).toBe('claude-haiku-4-5')
  fireEvent.click(queue().getByText('問答 qa2'))
  expect(detail().getByRole('heading', { name: '問答 qa2' })).toBeInTheDocument()
  expect(metaValue('判定尺')).toBeNull()
})

test('切到抽取品質：以 kind=extraction 重新取數，詳情連到閱讀頁、標題缺值回退檔名', async () => {
  const fetchMock = mount(extractionOnly([{
    report_id: 'r1', file_hash: 'h'.repeat(64), file_name: 'a.pdf', title: null,
    source: '凱基', quality_score: 0.41, pages_failed: [2, 7],
    review_reasons: ['pages_failed', 'low_score'],
  }]))
  await screen.findByText('目前沒有項目')
  fireEvent.click(screen.getByRole('tab', { name: /^抽取品質/ }))
  await findRow('a.pdf')
  expect(queue().getByText('凱基・2 頁抽取失敗、品質分數 0.41')).toBeInTheDocument()
  expect(detail().getByRole('heading', { name: 'a.pdf' })).toBeInTheDocument()
  expect(detail().getByRole('link', { name: '開啟閱讀頁' })).toHaveAttribute('href', `/report/${'h'.repeat(64)}`)
  expect(detail().getByText('品質分數 0.41')).toBeInTheDocument()
  expect(detail().getByText('2 頁抽取失敗')).toBeInTheDocument()
  expect(metaValue('檔名')).toBe('a.pdf')
  expect(metaValue('券商')).toBe('凱基')
  expect(listReads(fetchMock).at(-1)!.get('kind')).toBe('extraction')
  expect(search()).toBe('?kind=extraction')
})

test('倒讚分頁不顯示分數（那一欄對它沒有意義）', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'feedback'
      ? page('feedback', [qa(3, { faithfulness_score: null, feedback: 'dislike' })])
      : page('faithfulness', []),
  }))
  fireEvent.click(await screen.findByRole('tab', { name: /^倒讚/ }))
  await findRow('問答 qa3')
  expect(screen.queryByText('0.208')).not.toBeInTheDocument()
  expect(metaValue('忠實度')).toBeNull()
  expect(screen.getByText('近 30 天內使用者按了倒讚的回答。')).toBeInTheDocument()
})

test('分頁籤上的數字是各自的待處理數（status=open），與目前的狀態篩選無關', async () => {
  const fetchMock = mount(url => {
    const kind = url.searchParams.get('kind')!
    const open = { faithfulness: 4, feedback: 2, extraction: 7 }[kind]
    return { body: page(kind, [], { total: url.searchParams.get('status') === 'open' ? open : 0 }) }
  }, { entry: '/admin/reviews?status=resolved' })
  expect(await screen.findByRole('tab', { name: /^忠實度低分\s*4$/ })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: /^倒讚\s*2$/ })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: /^抽取品質\s*7$/ })).toBeInTheDocument()
  expect(countReads(fetchMock).map(([u]) => parse(u).searchParams.get('status'))).toEqual(['open', 'open', 'open'])
})

test('還有下一頁時「載入更多」以 next_offset 接續並累加', async () => {
  const fetchMock = mount(url => (
    url.searchParams.get('offset') === '0'
      ? { body: page('faithfulness', [qa(1)], { total: 2, has_more: true, next_offset: 1 }) }
      : { body: page('faithfulness', [qa(2)], { total: 2, offset: 1 }) }
  ))
  fireEvent.click(await screen.findByRole('button', { name: '載入更多（共 2 筆）' }))
  await findRow('問答 qa2')
  expect(queue().getByText('問答 qa1')).toBeInTheDocument()
  expect(listReads(fetchMock).at(-1)!.get('offset')).toBe('1')
  await waitFor(() => expect(screen.queryByRole('button', { name: /載入更多/ })).not.toBeInTheDocument())
})

test('載入失敗：說出來並給重試，不讓整頁消失', async () => {
  let fail = true
  mount(() => (fail
    ? { status: 503, body: { detail: '資料庫暫時無法使用，請稍後再試', code: 'db_unavailable' } }
    : { body: page('faithfulness', [qa(1)]) }))
  // 和其他頁同一種寫法：「○○載入失敗：＜後端 detail＞」＋role="alert"
  expect(await screen.findByRole('alert')).toHaveTextContent('佇列載入失敗：資料庫暫時無法使用，請稍後再試')
  expect(screen.getByRole('tab', { name: /^忠實度低分/ })).toBeInTheDocument()
  fail = false
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  await findRow('問答 qa1')
})

test('記錄人工驗證、已處理後從待處理消失，並可重新打開；結果用 toast，分頁籤的待處理數跟著更新', async () => {
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
  fireEvent.click(await screen.findByRole('tab', { name: /^倒讚/ }))
  await findRow('問答 qa3')
  expect(screen.getByRole('tab', { name: /^倒讚\s*1$/ })).toBeInTheDocument()
  fireEvent.click(seg('處理狀態', '已處理'))
  fireEvent.click(seg('人工驗證', '通過'))
  fireEvent.change(screen.getByLabelText(/處理註記/), { target: { value: '已核對原文' } })
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已儲存：問答 qa3・已處理・通過')
  await waitFor(() => expect(screen.queryByRole('list', { name: '待複核清單' })).not.toBeInTheDocument())
  // 外殼導覽與分頁籤共用 ['review-queue-count']：存完就重取
  expect(await screen.findByRole('tab', { name: /^倒讚\s*0$/ })).toBeInTheDocument()
  fireEvent.click(seg('複核狀態', '已處理'))
  await findRow('問答 qa3')
  expect(queue().getByText('已處理')).toBeInTheDocument()
  expect(screen.getByLabelText(/處理註記/)).toHaveValue('已核對原文')
  expect(seg('人工驗證', '通過')).toHaveAttribute('aria-pressed', 'true')
  expect(seg('處理狀態', '已處理')).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(seg('處理狀態', '待處理'))
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.queryByRole('list', { name: '待複核清單' })).not.toBeInTheDocument())
  expect(await screen.findByRole('status')).toHaveTextContent('已儲存：問答 qa3・待處理・通過')
  fireEvent.click(seg('複核狀態', '待處理'))
  await findRow('問答 qa3')
  expect(await screen.findByRole('tab', { name: /^倒讚\s*1$/ })).toBeInTheDocument()
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')
  expect(writes).toHaveLength(2)
  expect(parse(writes[0][0]).pathname).toBe('/api/review/feedback/qa3')
  expect(JSON.parse(writes[0][1]!.body as string)).toEqual({
    status: 'resolved', note: '已核對原文', verification: 'passed',
  })
  expect(JSON.parse(writes[1][1]!.body as string).status).toBe('open')
})

test('沒改動時「儲存」停用；註記有字數計數、上限 1000 字', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1, { review_note: '舊註記' })]) }))
  await findRow('問答 qa1')
  const saveBtn = screen.getByRole('button', { name: '儲存' })
  const note = screen.getByLabelText(/處理註記/)
  expect(saveBtn).toBeDisabled()
  expect(note).toHaveAttribute('maxlength', '1000')
  expect(screen.getByText('3／1000')).toBeInTheDocument()
  fireEvent.change(note, { target: { value: '舊註記，補一句' } })
  expect(screen.getByText('7／1000')).toBeInTheDocument()
  expect(saveBtn).toBeEnabled()
  // 改回原樣就又沒有改動
  fireEvent.change(note, { target: { value: '舊註記' } })
  expect(saveBtn).toBeDisabled()
  fireEvent.click(seg('人工驗證', '未通過'))
  expect(saveBtn).toBeEnabled()
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
  await findRow('問答 qa1')
  for (let i = 1; i <= 4; i++) {
    fireEvent.click(screen.getByRole('button', { name: '載入更多（共 41 筆）' }))
    await findRow(`問答 qa${i * 10 + 1}`)
  }
  await waitFor(() => expect(screen.queryByRole('button', { name: /載入更多/ })).not.toBeInTheDocument())
  fireEvent.click(queue().getByText('問答 qa11'))
  expect(detail().getByRole('heading', { name: '問答 qa11' })).toBeInTheDocument()
  expect(search()).toBe('?review=qa11')
  fetchMock.mockClear()
  // 每次 DOM 變動都記下 toast 出現時 URL 指著哪一筆。取消選取若退回 transition，toast 先 commit、URL 晚到，
  // 這段空檔裡單筆查詢會為剛存好的那筆多打一次；觀察者在每次 commit 後就跑，不必等主機忙才抓得到。
  const urlsWithToast: (string | null)[] = []
  const probe = new MutationObserver(() => {
    if (screen.queryByText(/^已儲存：/)) urlsWithToast.push(search())
  })
  probe.observe(document.body, { childList: true, subtree: true, characterData: true })
  fireEvent.click(seg('處理狀態', '已處理'))
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  const more = await screen.findByRole('button', { name: '載入更多（共 40 筆）' })
  expect(await screen.findByRole('status')).toHaveTextContent('已儲存：問答 qa11・已處理・未驗證')
  probe.disconnect()
  expect(urlsWithToast.length).toBeGreaterThan(0)
  expect(urlsWithToast).not.toContain('?review=qa11')
  expect(listReads(fetchMock).map(p => p.get('offset'))).toEqual(['0'])
  expect(queue().getAllByText(/^問答 qa/)).toHaveLength(10)
  // 存完的那筆已不在待處理：不再指著它，詳情回到第一筆
  expect(search()).toBe('')
  expect(detail().getByRole('heading', { name: '問答 qa1' })).toBeInTheDocument()
  fireEvent.click(more)
  await findRow('問答 qa12')
  await findRow('問答 qa21')
  expect(screen.queryByText('問答 qa11')).not.toBeInTheDocument()
})

test('儲存失敗：toast 說出來，保留已展開的頁面與草稿，不重新查詢清單', async () => {
  const fetchMock = mount((url, init) => {
    if (init?.method === 'PUT') return { status: 500, body: {} }
    const offset = Number(url.searchParams.get('offset'))
    return { body: page('faithfulness', [qa(offset + 1)], {
      total: 2, offset, has_more: offset === 0, next_offset: offset === 0 ? 1 : null,
    }) }
  })
  fireEvent.click(await screen.findByRole('button', { name: '載入更多（共 2 筆）' }))
  await findRow('問答 qa2')
  fetchMock.mockClear()
  fireEvent.click(seg('處理狀態', '略過'))
  fireEvent.change(screen.getByLabelText(/處理註記/), { target: { value: '重複的提問' } })
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  const toast = await screen.findByRole('status')
  expect(toast).toHaveTextContent('儲存失敗，請重試')
  expect(toast).toHaveAttribute('data-tone', 'bad')
  expect(queue().getByText('問答 qa1')).toBeInTheDocument()
  expect(queue().getByText('問答 qa2')).toBeInTheDocument()
  expect(fetchMock.mock.calls).toHaveLength(1)
  expect(fetchMock.mock.calls[0][1]?.method).toBe('PUT')
  // 草稿還在，可以直接再按一次
  expect(seg('處理狀態', '略過')).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText(/處理註記/)).toHaveValue('重複的提問')
  await waitFor(() => expect(screen.getByRole('button', { name: '儲存' })).toBeEnabled())
})

test('原因帶著量到的值：分數不低的研報看得出為什麼在這裡', () => {
  const item = { quality_score: 0.93, quality_flags: { layout_coverage: 0.12, garbled_ratio: 0.034 }, pages_failed: null }
  expect(reasonText(item, 'low_coverage')).toBe('版面覆蓋率 12%')
  expect(reasonText(item, 'garbled')).toBe('亂碼率 3.4%')
  // 旗標缺值時不編數字；後端新增的未知原因原樣顯示，不讓整列消失。
  expect(reasonText({ quality_flags: null }, 'low_coverage')).toBe('版面覆蓋率過低')
  expect(reasonText({}, 'something_new')).toBe('something_new')
})

test('問答顯示提問者代號與處理人；共用帳號時期的舊列標「共用帳號」、沒處理過不印處理人', async () => {
  mount(() => ({ body: page('faithfulness', [
    qa(1, { asker_code: '3fa9c2d1', reviewer: 'root', reviewed_at: '2026-10-04T08:00:00Z', review_status: 'resolved' }),
    qa(2, { asker_code: null, reviewer: null }),
  ]) }))
  await findRow('問答 qa1')
  expect(queue().getByText('提問者 #3fa9c2d1・2026-09-20')).toBeInTheDocument()
  expect(queue().getByText('提問者 共用帳號・2026-09-20')).toBeInTheDocument()
  expect(metaValue('提問者')).toBe('#3fa9c2d1')
  expect(metaValue('處理人')).toBe('root（2026-10-04）')
  fireEvent.click(queue().getByText('問答 qa2'))
  expect(metaValue('提問者')).toBe('共用帳號')
  expect(metaValue('處理人')).toBeNull()
})

test('研報的詳情也顯示處理人', async () => {
  mount(extractionOnly([extractionItem({ reviewer: 'bob', reviewed_at: '2026-10-03T01:00:00Z' })]))
  fireEvent.click(await screen.findByRole('tab', { name: /^抽取品質/ }))
  await findRow('z.pdf')
  expect(metaValue('處理人')).toBe('bob（2026-10-03）')
})

test('以現行門檻已不需複核的研報要說出來，不留一格空白', async () => {
  mount(extractionOnly([extractionItem()]))
  fireEvent.click(await screen.findByRole('tab', { name: /^抽取品質/ }))
  await findRow('z.pdf')
  expect(queue().getByText('現行門檻下已達標')).toBeInTheDocument()
  expect(detail().getByText('現行門檻下已達標')).toHaveAttribute('title', '以現行門檻已不需複核；重跑該篇回填即會解除標記')
})

// /api/review/judge-scale 的回應（`/api/progress` 的 evaluation.qa 子集）
const scaleBase: JudgeScale = { judge_model: 'deepseek-flash', judge_since: '2026-09-25', other_judge_checked: 6 }

test('判定尺剛換成 DeepSeek、窗期內還有舊尺 → 忠實度分頁比照監控卡標新量尺', async () => {
  mount(() => ({ body: page('faithfulness', [qa(1)]) }), { scale: scaleBase })
  await findRow('問答 qa1')
  expect(screen.getByText(/判定尺 deepseek-flash 是新量尺（自 2026-09-25 起，DeepSeek）/)).toBeInTheDocument()
})

test('新尺尚無查核 → 新量尺但不編日期', async () => {
  mount(() => ({ body: page('faithfulness', []) }), { scale: { ...scaleBase, judge_since: null } })
  await screen.findByText('目前沒有項目')
  expect(screen.getByText(/新量尺（尚無查核，DeepSeek）/)).toBeInTheDocument()
})

test('窗期內已全是新尺、或沒有量尺資料 → 不標新量尺', async () => {
  mount(() => ({ body: page('faithfulness', []) }), { scale: { ...scaleBase, other_judge_checked: 0 } })
  await screen.findByText('目前沒有項目')
  expect(screen.queryByText(/新量尺/)).not.toBeInTheDocument()
})

test('新量尺標示只在忠實度分頁：切到倒讚就不印', async () => {
  mount(url => ({
    body: url.searchParams.get('kind') === 'feedback' ? page('feedback', []) : page('faithfulness', []),
  }), { scale: scaleBase })
  expect(await screen.findByText(/新量尺/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: /^倒讚/ }))
  await waitFor(() => expect(screen.queryByText(/新量尺/)).not.toBeInTheDocument())
})

test('沒有 qa_content.read：「查看內容」停用並說明缺什麼，不會打讀取端點', async () => {
  const fetchMock = mount(() => ({ body: page('faithfulness', [qa(1)]) }))
  await findRow('問答 qa1')
  const btn = screen.getByRole('button', { name: '查看內容' })
  expect(btn).toBeDisabled()
  expect(btn).toHaveAttribute('title', '需要 qa_content.read 權限')
  expect(detail().getByText('需要 qa_content.read 權限')).toBeInTheDocument()
  fireEvent.click(btn)
  expect(accessCalls(fetchMock)).toHaveLength(0)
})

test('有 qa_content.read：按下才 POST 取這一筆並提示已記入稽核，收起即丟掉、再看要再讀一次', async () => {
  const fetchMock = mount((url, init) => {
    if (url.pathname === '/api/review/qa/qa1/access') {
      expect(init?.method).toBe('POST')
      return { body: { qa_id: 'qa1', kinds: ['faithfulness'], created_at: null, question: '台積電目標價？', answer: '1,500 元 [1]' } }
    }
    return { body: page('faithfulness', [qa(1)]) }
  }, { canReadContent: true })
  await findRow('問答 qa1')
  // 載入佇列時不預先讀內容
  expect(accessCalls(fetchMock)).toHaveLength(0)
  expect(screen.getByText('每次查看都會記入稽核')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '查看內容' }))
  expect(await screen.findByText('台積電目標價？')).toBeInTheDocument()
  expect(screen.getByText('1,500 元 [1]')).toBeInTheDocument()
  expect(screen.getByText('提問與回答（這次查看已記入稽核）')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '收起' }))
  expect(screen.queryByText('台積電目標價？')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '查看內容' }))
  await screen.findByText('台積電目標價？')
  expect(accessCalls(fetchMock)).toHaveLength(2)
})

test('切到別筆原文就丟掉；切回來要重新按「查看內容」（又一次有稽核的讀取）', async () => {
  const fetchMock = mount(url => {
    const m = /^\/api\/review\/qa\/(qa\d)\/access$/.exec(url.pathname)
    if (m) return { body: { qa_id: m[1], kinds: ['faithfulness'], created_at: null, question: `${m[1]} 的提問`, answer: '回答' } }
    return { body: page('faithfulness', [qa(1), qa(2)]) }
  }, { canReadContent: true })
  await findRow('問答 qa1')
  fireEvent.click(screen.getByRole('button', { name: '查看內容' }))
  await screen.findByText('qa1 的提問')
  fireEvent.click(queue().getByText('問答 qa2'))
  expect(screen.queryByText('qa1 的提問')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '查看內容' })).toBeEnabled()
  fireEvent.click(queue().getByText('問答 qa1'))
  expect(screen.queryByText('qa1 的提問')).not.toBeInTheDocument()
  expect(accessCalls(fetchMock)).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '查看內容' }))
  await screen.findByText('qa1 的提問')
  expect(accessCalls(fetchMock).map(([u]) => parse(u).pathname)).toEqual([
    '/api/review/qa/qa1/access', '/api/review/qa/qa1/access',
  ])
})

test('讀取端點回 404（已不在佇列）時說出來，不顯示任何內容', async () => {
  mount(url => (
    url.pathname.endsWith('/access')
      ? { status: 404, body: { detail: '待複核項目不存在', code: 'not_found' } }
      : { body: page('feedback', [qa(3, { feedback: 'dislike' })]) }
  ), { canReadContent: true })
  fireEvent.click(await screen.findByRole('tab', { name: /^倒讚/ }))
  fireEvent.click(await screen.findByRole('button', { name: '查看內容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('已不在待複核佇列')
  expect(screen.queryByText('提問')).not.toBeInTheDocument()
})

test('抽取品質的詳情不會有「查看內容」', async () => {
  mount(extractionOnly([extractionItem()]), { canReadContent: true })
  fireEvent.click(await screen.findByRole('tab', { name: /^抽取品質/ }))
  await findRow('z.pdf')
  expect(detail().getByRole('link', { name: '開啟閱讀頁' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '查看內容' })).not.toBeInTheDocument()
})

test('分頁、狀態與選中的那筆來自 URL query：連結打開就是同一個畫面', async () => {
  const fetchMock = mount(url => ({
    body: url.searchParams.get('kind') === 'feedback'
      ? page('feedback', [qa(4, { review_status: 'resolved' }), qa(5, { review_status: 'resolved' })])
      : page(url.searchParams.get('kind')!, []),
  }), { entry: '/admin/reviews?kind=feedback&status=resolved&review=qa5' })
  await findRow('問答 qa5')
  expect(screen.getByRole('tab', { name: /^倒讚/ })).toHaveAttribute('aria-selected', 'true')
  expect(seg('複核狀態', '已處理')).toHaveAttribute('aria-pressed', 'true')
  expect(rowButton('問答 qa5')).toHaveAttribute('aria-current', 'true')
  expect(rowButton('問答 qa4')).not.toHaveAttribute('aria-current')
  expect(detail().getByRole('heading', { name: '問答 qa5' })).toBeInTheDocument()
  const reads = listReads(fetchMock)
  expect(reads).toHaveLength(1)
  expect([reads[0].get('kind'), reads[0].get('status')]).toEqual(['feedback', 'resolved'])
})

test('換選取、換狀態、換分頁都寫回 URL；換狀態或分頁會清掉選取，預設值不寫進 URL', async () => {
  mount(url => ({ body: page(url.searchParams.get('kind')!, url.searchParams.get('kind') === 'extraction' ? [] : [qa(1), qa(2)]) }))
  await findRow('問答 qa1')
  expect(search()).toBe('')
  fireEvent.click(queue().getByText('問答 qa2'))
  expect(search()).toBe('?review=qa2')
  expect(rowButton('問答 qa2')).toHaveAttribute('aria-current', 'true')
  fireEvent.click(seg('複核狀態', '全部'))
  expect(search()).toBe('?status=all')
  await findRow('問答 qa1')
  fireEvent.click(queue().getByText('問答 qa2'))
  expect(search()).toBe('?status=all&review=qa2')
  fireEvent.click(screen.getByRole('tab', { name: /^倒讚/ }))
  expect(search()).toBe('?status=all&kind=feedback')
  fireEvent.click(screen.getByRole('tab', { name: /^忠實度低分/ }))
  expect(search()).toBe('?status=all')
  fireEvent.click(seg('複核狀態', '待處理'))
  expect(search()).toBe('')
})

test('URL 裡認不得的分頁與狀態當成預設；指的那筆不在清單裡就顯示第一筆', async () => {
  const fetchMock = mount(() => ({ body: page('faithfulness', [qa(1), qa(2)]) }),
    { entry: '/admin/reviews?kind=bogus&status=nope&review=gone' })
  await findRow('問答 qa1')
  expect(screen.getByRole('tab', { name: /^忠實度低分/ })).toHaveAttribute('aria-selected', 'true')
  expect(seg('複核狀態', '待處理')).toHaveAttribute('aria-pressed', 'true')
  expect(rowButton('問答 qa1')).toHaveAttribute('aria-current', 'true')
  expect(detail().getByRole('heading', { name: '問答 qa1' })).toBeInTheDocument()
  const reads = listReads(fetchMock)
  expect([reads[0].get('kind'), reads[0].get('status')]).toEqual(['faithfulness', 'open'])
})

test('存完還在目前篩選裡的那筆（全部）維持選取，表單回到存好的值', async () => {
  let saved = { status: 'open', note: '', verification: 'untested', updated_at: null as string | null }
  mount((_url, init) => {
    if (init?.method === 'PUT') {
      saved = { ...JSON.parse(init.body as string), updated_at: '2026-09-21T03:00:00Z' }
      return { body: { kind: 'faithfulness', subject_id: 'qa2', ...saved } }
    }
    return { body: page('faithfulness', [qa(1), qa(2, {
      review_status: saved.status, review_note: saved.note, verification: saved.verification, reviewed_at: saved.updated_at,
    })]) }
  }, { entry: '/admin/reviews?status=all&review=qa2' })
  await findRow('問答 qa2')
  fireEvent.click(seg('處理狀態', '略過'))
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已儲存：問答 qa2・略過・未驗證')
  expect(search()).toBe('?status=all&review=qa2')
  await waitFor(() => expect(queue().getByText('略過')).toBeInTheDocument())
  expect(detail().getByRole('heading', { name: '問答 qa2' })).toBeInTheDocument()
  expect(seg('處理狀態', '略過')).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: '儲存' })).toBeDisabled()
})

// ── `?review=` 指的那筆不在已載入的清單：用單筆端點取 ─────────────────────────────

const PINNED = '3f9a2c1e-0000-4000-8000-000000000001'
const pinnedQa = (over: Record<string, unknown> = {}) => ({
  qa_id: PINNED, created_at: '2026-09-18T03:00:00+00:00', faithfulness_score: 0.12, judge_model: 'deepseek-flash',
  asker_code: 'a1b2c3d4', review_status: 'resolved', review_note: '已看過', verification: 'passed',
  reviewed_at: '2026-09-19T03:00:00Z', reviewer: 'root', ...over,
})
const itemReads = (f: FetchMock) => f.mock.calls
  .filter(([u, init]) => /^\/api\/review\/(faithfulness|feedback|extraction)\/[^/]+$/.test(parse(u).pathname)
    && (init?.method ?? 'GET') === 'GET')
  .map(([u]) => parse(u).pathname)

/** 清單固定是 qa1、qa2；單筆端點由 `single` 決定怎麼回。 */
const withSingle = (single: Handler, list: unknown[] = [qa(1), qa(2)]): Handler => (url, init) => {
  if (url.pathname === '/api/review/queue') return { body: page(url.searchParams.get('kind')!, url.searchParams.get('kind') === 'faithfulness' ? list : []) }
  return single(url, init)
}

test('review 指的那筆不在已載入的清單：用單筆端點取來顯示，清單不動並註明；不顯示原文', async () => {
  // 單筆端點就算被竄改成帶原文，畫面也不印：zod 會把未宣告的鍵丟掉。
  const fetchMock = mount(withSingle(() => ({ body: pinnedQa({ question: '機密提問', answer: '機密回答', asked_by: 'alice' }) })),
    { entry: `/admin/reviews?review=${PINNED}` })
  expect(await detail().findByRole('heading', { level: 2, name: '問答 3f9a2c1e' })).toBeInTheDocument()
  expect(metaValue('忠實度')).toBe('0.120')
  expect(metaValue('提問者')).toBe('#a1b2c3d4')
  expect(metaValue('處理人')).toBe('root（2026-09-19）')
  expect(seg('處理狀態', '已處理')).toHaveAttribute('aria-pressed', 'true')
  expect(screen.queryByText(/機密/)).not.toBeInTheDocument()
  expect(screen.queryByText(/alice/)).not.toBeInTheDocument()
  // 清單照舊兩筆，沒有哪一列被標成目前這筆；註明它不在清單裡。
  expect(queue().getAllByText(/^問答 qa/)).toHaveLength(2)
  expect(rowButton('問答 qa1')).not.toHaveAttribute('aria-current')
  expect(screen.getByText(/連結指的這一筆不在目前載入的清單裡/)).toBeInTheDocument()
  expect(itemReads(fetchMock)).toEqual([`/api/review/faithfulness/${PINNED}`])
  expect(search()).toBe(`?review=${PINNED}`)
})

test('review 指的那筆就在清單裡：不另外打單筆端點', async () => {
  const fetchMock = mount(withSingle(() => ({ status: 500, body: {} }), [qa(1), pinnedQa()]),
    { entry: `/admin/reviews?review=${PINNED}` })
  await findRow('問答 3f9a2c1e')
  expect(rowButton('問答 3f9a2c1e')).toHaveAttribute('aria-current', 'true')
  expect(itemReads(fetchMock)).toEqual([])
})

test('單筆取來的問答：「查看內容」流程不變，切到清單裡的別筆就丟掉', async () => {
  const fetchMock = mount(withSingle((_url, init) => (init?.method === 'POST'
    ? { body: { qa_id: PINNED, kinds: ['faithfulness'], created_at: null, question: '台積電目標價？', answer: '依研報…' } }
    : { body: pinnedQa() })), { entry: `/admin/reviews?review=${PINNED}`, canReadContent: true })
  await detail().findByRole('heading', { name: '問答 3f9a2c1e' })
  expect(accessCalls(fetchMock)).toHaveLength(0)
  fireEvent.click(detail().getByRole('button', { name: '查看內容' }))
  expect(await detail().findByText('台積電目標價？')).toBeInTheDocument()
  expect(detail().getByText('提問與回答（這次查看已記入稽核）')).toBeInTheDocument()
  expect(String(accessCalls(fetchMock)[0][0])).toBe(`/api/review/qa/${PINNED}/access`)
  fireEvent.click(queue().getByText('問答 qa2'))
  expect(search()).toBe('?review=qa2')
  expect(detail().getByRole('heading', { name: '問答 qa2' })).toBeInTheDocument()
  expect(screen.queryByText('台積電目標價？')).not.toBeInTheDocument()
})

test('單筆端點回 404（已不在佇列）：顯示第一筆並說明', async () => {
  const fetchMock = mount(withSingle(() => ({ status: 404, body: { detail: '待複核項目不存在', code: 'not_found' } })),
    { entry: `/admin/reviews?review=${PINNED}` })
  expect(await detail().findByRole('heading', { name: '問答 qa1' })).toBeInTheDocument()
  expect(rowButton('問答 qa1')).toHaveAttribute('aria-current', 'true')
  expect(screen.getByText(/連結指的那一筆已不在這個分頁的佇列裡（或已超過 30 天），右邊顯示第一筆。/)).toBeInTheDocument()
  expect(itemReads(fetchMock)).toHaveLength(1)
})

test('舊版後端沒有單筆端點（405）：照舊顯示第一筆，不多說', async () => {
  mount(withSingle(() => ({ status: 405, body: { detail: 'Method Not Allowed', code: 'method_not_allowed' } })),
    { entry: `/admin/reviews?review=${PINNED}` })
  expect(await detail().findByRole('heading', { name: '問答 qa1' })).toBeInTheDocument()
  expect(rowButton('問答 qa1')).toHaveAttribute('aria-current', 'true')
  expect(screen.queryByText(/連結指的/)).not.toBeInTheDocument()
})

test('單筆取來的那筆可以直接處理：PUT 它的 id，表單回到存好的值，選取不變', async () => {
  let state = { review_status: 'resolved', review_note: '已看過', verification: 'passed', reviewed_at: '2026-09-19T03:00:00Z' }
  const fetchMock = mount(withSingle((_url, init) => {
    if (init?.method === 'PUT') {
      const body = JSON.parse(init.body as string)
      state = { review_status: body.status, review_note: body.note, verification: body.verification, reviewed_at: '2026-09-21T03:00:00Z' }
      return { body: { kind: 'faithfulness', subject_id: PINNED, ...body, updated_at: state.reviewed_at, reviewer: 'root' } }
    }
    return { body: pinnedQa(state) }
  }), { entry: `/admin/reviews?status=all&review=${PINNED}` })
  await detail().findByRole('heading', { name: '問答 3f9a2c1e' })
  fireEvent.click(seg('處理狀態', '略過'))
  fireEvent.click(screen.getByRole('button', { name: '儲存' }))
  expect(await screen.findByText('已儲存：問答 3f9a2c1e・略過・通過')).toBeInTheDocument()
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')!
  expect(String(put[0])).toBe(`/api/review/faithfulness/${PINNED}`)
  expect(search()).toBe(`?status=all&review=${PINNED}`)
  await waitFor(() => expect(seg('處理狀態', '略過')).toHaveAttribute('aria-pressed', 'true'))
  expect(detail().getByRole('heading', { name: '問答 3f9a2c1e' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: '儲存' })).toBeDisabled())
  expect(metaValue('處理人')).toBe('root（2026-09-21）')
})

test('抽取品質分頁同樣取單筆：研報的詳情連到閱讀頁', async () => {
  const fetchMock = mount(url => {
    if (url.pathname === '/api/review/queue') return { body: page(url.searchParams.get('kind')!, []) }
    return { body: extractionItem({ report_id: PINNED, title: '別頁的研報', source: '凱基', quality_score: 0.4,
      review_reasons: ['low_score'] }) }
  }, { entry: `/admin/reviews?kind=extraction&review=${PINNED}` })
  expect(await detail().findByRole('heading', { name: '別頁的研報' })).toBeInTheDocument()
  expect(detail().getByRole('link', { name: '開啟閱讀頁' })).toHaveAttribute('href', `/report/${'a'.repeat(64)}`)
  expect(itemReads(fetchMock)).toEqual([`/api/review/extraction/${PINNED}`])
  expect(screen.getByText('目前沒有項目')).toBeInTheDocument()
})

test('review 不是 UUID：不拿去打單筆端點，照舊顯示第一筆', async () => {
  const fetchMock = mount(withSingle(() => ({ status: 500, body: {} })), { entry: '/admin/reviews?review=gone' })
  expect(await detail().findByRole('heading', { name: '問答 qa1' })).toBeInTheDocument()
  expect(itemReads(fetchMock)).toEqual([])
})
