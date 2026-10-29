import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'

const deleteConversation = vi.fn(async (id: string) => { void id })
vi.mock('../../lib/askApi', () => ({ deleteConversation: (id: string) => deleteConversation(id) }))
import { ConversationList } from './ConversationList'
import { clearPendingConversation, resolvePendingConversation, startPendingConversation } from '../../lib/pendingConversation'

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); localStorage.clear() })

function wrap(entries = ['/assistant']) {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify([{ conversation_id: 'c1', title: 'AI 伺服器供應鏈' }]), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries}><ConversationList /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('「最近」標題列的 ＋ 開新對話，下方是清單', async () => {
  wrap()
  expect(screen.getByRole('link', { name: '新對話' })).toHaveAttribute('href', '/assistant')
  expect(await screen.findByText('AI 伺服器供應鏈')).toBeInTheDocument()
})

test('目前 ?c 對話標示 aria-current', async () => {
  wrap(['/assistant?c=c1'])
  const link = await screen.findByRole('link', { name: 'AI 伺服器供應鏈' })
  expect(link).toHaveAttribute('aria-current', 'page')
})

// 底色疊加缺陷：選取態原本畫在 <a>（.item.active）上，而 <a> 只佔那一列的左半，
// 刪除鈕那一格沒有底色 ⇒ 選取起來的列右端顏色明顯不一樣；hover 更糟，.row 與 .item
// 各塗一層 rgba，標題那半會疊成約兩倍深。修法是底色一律由 .row 畫、兩個子元素都不
// 自帶底色，故這裡斷言的是「該列有底色、兩個子元素都沒有」。
// （jsdom 不算版面，但 getComputedStyle 對 CSS Modules 的 background 是解析得出來的。）
test('選取的對話：底色畫在整列上，標題與刪除鈕都不自帶底色', async () => {
  wrap(['/assistant?c=c1'])
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
// 照樣觸發 ⇒ 快取失效、重取後對話還在，而本元件的 onSuccess 還會 navigate('/assistant')。
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
  wrap(['/assistant?c=c1'])
  const link = await screen.findByRole('link', { name: 'AI 伺服器供應鏈' })
  expect(link).toHaveAttribute('aria-current', 'page')
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  fireEvent.click(screen.getByRole('button', { name: '刪除' }))
  await screen.findByRole('alert')
  // 仍停在 ?c=c1（若被 navigate('/assistant') 帶走，aria-current 會消失）
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

function wrapWith(handler: (url: URL) => unknown[], qc = new QueryClient({ defaultOptions: { queries: { retry: false } } }), path = '/assistant') {
  const fetchMock = vi.fn(async (input: string) =>
    new Response(JSON.stringify(handler(new URL(input, 'http://x'))), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <ConversationList />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const conv = (i: number) => ({ conversation_id: `c${i}`, title: `對話 ${i}` })

test('「最近」標題可收合清單，並記住選擇', async () => {
  wrapWith(() => [conv(1)])
  await screen.findByText('對話 1')
  const heading = screen.getByRole('button', { name: /最近/ })
  expect(heading).toHaveAttribute('aria-expanded', 'true')
  fireEvent.click(heading)
  expect(heading).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('對話 1')).not.toBeInTheDocument()
  expect(localStorage.getItem('tf.sidebar.recents')).toBe('0')
})

test('側欄不再放搜尋框（搜尋對話改在 ⌘K 搜尋彈窗）', async () => {
  wrapWith(() => [conv(1)])
  await screen.findByText('對話 1')
  expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
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

// ── 新對話送出當下就進「最近」（pendingConversation.ts）────────────────────────
// 對話串的 id 要等整輪跑完（done）才有，原本清單也要等那時才重抓：一題跑數十秒，期間側欄看不到
// 自己剛開的對話。

test('新對話送出當下：排在最上面、標為目前對話，還沒有 id 所以沒有刪除鈕', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  wrapWith(() => [conv(1)], qc)
  await screen.findByText('對話 1')
  act(() => startPendingConversation(qc, 't1', '半導體庫存何時見底'))
  const link = await screen.findByRole('link', { name: '半導體庫存何時見底' })
  expect(link).toHaveAttribute('href', '/assistant')
  expect(link).toHaveAttribute('aria-current', 'page')
  // 排在既有對話之前
  expect(link.compareDocumentPosition(screen.getByRole('link', { name: '對話 1' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.getAllByRole('button', { name: '刪除對話' })).toHaveLength(1)  // 只有對話 1 的
})

test('暫存列只在研究助理頁的新對話上標為目前對話', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  wrapWith(() => [conv(1)], qc, '/library')
  await screen.findByText('對話 1')
  act(() => startPendingConversation(qc, 't1', '新題'))
  expect(await screen.findByRole('link', { name: '新題' })).not.toHaveAttribute('aria-current')
})

test('拿到 id 後改連到那一串；清單重抓到真的那一列時只留一列', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let server = [conv(1)]
  wrapWith(() => server, qc)
  await screen.findByText('對話 1')
  act(() => startPendingConversation(qc, 't1', '新題'))
  act(() => resolvePendingConversation(qc, 't1', 'c9'))
  await waitFor(() => expect(screen.getByRole('link', { name: '新題' })).toHaveAttribute('href', '/assistant?c=c9'))
  server = [{ conversation_id: 'c9', title: '新題' }, conv(1)]
  await act(async () => { await qc.invalidateQueries({ queryKey: ['conversations'] }) })
  expect(screen.getAllByRole('link', { name: '新題' })).toHaveLength(1)
  await act(async () => { clearPendingConversation(qc, 't1') })
  expect(screen.getAllByRole('link', { name: '新題' })).toHaveLength(1)
  expect(screen.getByRole('link', { name: '新題' })).toHaveAttribute('href', '/assistant?c=c9')
})

test('切走後還在補存的舊一串與剛送出的新一串並存：兩列都在，只有新的那列是目前對話', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  wrapWith(() => [conv(1)], qc)
  await screen.findByText('對話 1')
  act(() => startPendingConversation(qc, 't1', '舊題'))
  act(() => startPendingConversation(qc, 't2', '新題'))
  const fresh = await screen.findByRole('link', { name: '新題' })
  const old = screen.getByRole('link', { name: '舊題' })
  expect(fresh).toHaveAttribute('aria-current', 'page')
  expect(old).not.toHaveAttribute('aria-current')
  expect(fresh.compareDocumentPosition(old) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  // 舊的補存完成：改連到它自己的那一串
  act(() => resolvePendingConversation(qc, 't1', 'c7'))
  await waitFor(() => expect(screen.getByRole('link', { name: '舊題' })).toHaveAttribute('href', '/assistant?c=c7'))
})
