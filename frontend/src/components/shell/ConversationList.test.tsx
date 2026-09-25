import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'

const deleteConversation = vi.fn(async (id: string) => { void id })
vi.mock('../../lib/askApi', () => ({ deleteConversation: (id: string) => deleteConversation(id) }))
import { ConversationList } from './ConversationList'

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

function wrap(entries = ['/ask']) {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify([{ conversation_id: 'c1', title: 'AI 伺服器供應鏈' }]), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries}><ConversationList /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('渲染新對話鈕與清單', async () => {
  wrap()
  expect(screen.getByRole('link', { name: '新對話' })).toHaveAttribute('href', '/ask')
  expect(await screen.findByText('AI 伺服器供應鏈')).toBeInTheDocument()
})

test('目前 ?c 對話標示 aria-current', async () => {
  wrap(['/ask?c=c1'])
  const link = await screen.findByRole('link', { name: 'AI 伺服器供應鏈' })
  expect(link).toHaveAttribute('aria-current', 'page')
})

// 底色疊加缺陷：選取態原本畫在 <a>（.item.active）上，而 <a> 只佔那一列的左半，
// 刪除鈕那一格沒有底色 ⇒ 選取起來的列右端顏色明顯不一樣；hover 更糟，.row 與 .item
// 各塗一層 rgba，標題那半會疊成約兩倍深。修法是底色一律由 .row 畫、兩個子元素都不
// 自帶底色，故這裡斷言的是「該列有底色、兩個子元素都沒有」。
// （jsdom 不算版面，但 getComputedStyle 對 CSS Modules 的 background 是解析得出來的。）
test('選取的對話：底色畫在整列上，標題與刪除鈕都不自帶底色', async () => {
  wrap(['/ask?c=c1'])
  const link = await screen.findByRole('link', { name: 'AI 伺服器供應鏈' })
  const row = link.parentElement!
  const del = screen.getByRole('button', { name: '刪除對話' })
  expect(getComputedStyle(row).backgroundColor).not.toBe('rgba(0, 0, 0, 0)')
  expect(getComputedStyle(link).backgroundColor).toBe('rgba(0, 0, 0, 0)')
  expect(getComputedStyle(del).backgroundColor).toBe('rgba(0, 0, 0, 0)')
  // 刪除鈕確實與標題同列（不是碰巧兩個都透明）
  expect(row.contains(del)).toBe(true)
})

test('刪除：確認→呼叫 deleteConversation；取消→不呼叫', async () => {
  wrap()
  await screen.findByText('AI 伺服器供應鏈')
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  // 確認對話框
  expect(screen.getByText('刪除此對話？')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(deleteConversation).not.toHaveBeenCalled()
  // 再次開啟 → 確認
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  fireEvent.click(screen.getByRole('button', { name: '刪除' }))
  // react-query 的 mutationFn 為非同步派發（非 click 當下同步呼叫），故用 waitFor
  await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith('c1'))
})

// ── 刪除失敗的整條鏈 ────────────────────────────────────────────────────────
// 缺陷原本是：deleteConversation 從不讀回應、從不 reject ⇒ useMutation 的 onSuccess
// 照樣觸發 ⇒ 快取失效、重取後對話還在，而本元件的 onSuccess 還會 navigate('/ask')。
// 使用者被送離當前對話、清單裡那一列還在、沒有任何錯誤訊息——下一步當然是再按一次。
//
// 這幾條刻意在**元件層**驗而不是單測 hook：要證明的是「API throw 之後，畫面確實
// 收得到」，而那條鏈包含 useMutation 的錯誤傳遞與 onSuccess 的抑制，單測 hook 看不到
// 後者。

test('刪除失敗：顯示錯誤訊息，且該列仍在清單裡', async () => {
  deleteConversation.mockRejectedValueOnce(new Error('刪除對話失敗：找不到該對話串'))
  wrap()
  await screen.findByText('AI 伺服器供應鏈')
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  fireEvent.click(screen.getByRole('button', { name: '刪除' }))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('找不到該對話串')
  // 失敗的意思正是「這一列還在」——訊息與清單要同時成立才算真的講清楚了
  expect(screen.getByText('AI 伺服器供應鏈')).toBeInTheDocument()
})

test('刪除失敗：不得導航離開當前對話', async () => {
  deleteConversation.mockRejectedValueOnce(new Error('boom'))
  wrap(['/ask?c=c1'])
  const link = await screen.findByRole('link', { name: 'AI 伺服器供應鏈' })
  expect(link).toHaveAttribute('aria-current', 'page')
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  fireEvent.click(screen.getByRole('button', { name: '刪除' }))
  await screen.findByRole('alert')
  // 仍停在 ?c=c1（若被 navigate('/ask') 帶走，aria-current 會消失）
  expect(screen.getByRole('link', { name: 'AI 伺服器供應鏈' }))
    .toHaveAttribute('aria-current', 'page')
})

test('刪除成功：不顯示錯誤訊息', async () => {
  wrap()
  await screen.findByText('AI 伺服器供應鏈')
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  fireEvent.click(screen.getByRole('button', { name: '刪除' }))
  await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith('c1'))
  expect(screen.queryByRole('alert')).toBeNull()
})

function wrapWith(handler: (url: URL) => unknown[]) {
  const fetchMock = vi.fn(async (input: string) =>
    new Response(JSON.stringify(handler(new URL(input, 'http://x'))), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/ask']}><ConversationList /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const conv = (i: number) => ({ conversation_id: `c${i}`, title: `對話 ${i}` })

test('搜尋：停止輸入後才帶 q 查詢，且從第一頁開始', async () => {
  const fetchMock = wrapWith(url => (url.searchParams.get('q') === '先進封裝' ? [conv(7)] : [conv(1)]))
  await screen.findByText('對話 1')
  fireEvent.change(screen.getByRole('searchbox', { name: '搜尋歷史對話' }), { target: { value: '先進封裝' } })
  expect(await screen.findByText('對話 7')).toBeInTheDocument()
  // 舊的那一列由 AnimatePresence 播完退場才離開 DOM，所以要等。
  await waitFor(() => expect(screen.queryByText('對話 1')).not.toBeInTheDocument())
  const last = new URL(fetchMock.mock.calls.at(-1)![0] as string, 'http://x')
  expect(last.searchParams.get('q')).toBe('先進封裝')
  expect(last.searchParams.get('offset')).toBe('0')
  // debounce：逐字輸入不該每個字各打一次——這裡只有「初次載入」與「搜尋」兩次。
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('搜尋沒有結果要說出來，而不是一片空白', async () => {
  wrapWith(url => (url.searchParams.get('q') ? [] : [conv(1)]))
  await screen.findByText('對話 1')
  fireEvent.change(screen.getByRole('searchbox', { name: '搜尋歷史對話' }), { target: { value: '不存在的詞' } })
  expect(await screen.findByRole('status')).toHaveTextContent('沒有提問包含「不存在的詞」的對話')
})

test('這一頁是滿的才出現「載入更多」，按下後以 offset 接續並累加', async () => {
  const fetchMock = wrapWith(url => {
    const offset = Number(url.searchParams.get('offset'))
    return offset === 0 ? Array.from({ length: 50 }, (_, i) => conv(i)) : [conv(50), conv(51)]
  })
  await screen.findByText('對話 0')
  fireEvent.click(screen.getByRole('button', { name: '載入更多' }))
  expect(await screen.findByText('對話 51')).toBeInTheDocument()
  expect(screen.getByText('對話 0')).toBeInTheDocument() // 累加，不是換頁
  expect(new URL(fetchMock.mock.calls.at(-1)![0] as string, 'http://x').searchParams.get('offset')).toBe('50')
  // 第二頁沒滿 → 沒有下一頁了
  await waitFor(() => expect(screen.queryByRole('button', { name: '載入更多' })).not.toBeInTheDocument())
})

test('不滿一頁時沒有「載入更多」', async () => {
  wrapWith(() => [conv(1), conv(2)])
  await screen.findByText('對話 1')
  expect(screen.queryByRole('button', { name: '載入更多' })).not.toBeInTheDocument()
})
