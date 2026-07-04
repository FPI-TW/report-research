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
