import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminAuditPage from './AdminAuditPage'
import { actionLabel, auditSummary } from './auditLabels'

afterEach(() => vi.unstubAllGlobals())

const ITEMS = [
  { id: 2, actor_user_id: null, actor_username: null, action: 'user.create', target_type: 'user', target_id: 'u1',
    detail: { username: 'root', role: 'admin', via: 'cli' }, created_at: '2026-10-05T01:00:00Z' },
  { id: 1, actor_user_id: 'me', actor_username: 'root', action: 'user.something_new', target_type: 'user',
    target_id: 'u2', detail: { username: 'alice' }, created_at: '2026-10-05T00:00:00Z' },
]

function mount(role: 'admin' | 'user' = 'admin') {
  const fetchMock = vi.fn(async (path: string) => {
    if (path === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role }))
    const offset = Number(new URL(path, 'http://x').searchParams.get('offset'))
    const body = offset === 0
      ? { total: 21, limit: 20, offset: 0, has_more: true, next_offset: 20, items: ITEMS }
      : { total: 21, limit: 20, offset: 20, has_more: false, next_offset: null, items: [ITEMS[1]] }
    return new Response(JSON.stringify(body))
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/audit']}><AdminAuditPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('操作紀錄：CLI 操作顯示為指令列、未知 action 原樣顯示、可翻頁', async () => {
  const fetchMock = mount()
  expect(await screen.findByRole('heading', { name: '操作紀錄' })).toBeInTheDocument()
  expect(await screen.findByRole('cell', { name: '指令列（CLI）' })).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: 'root・管理員・經指令列' })).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: 'user.something_new' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '上一頁' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '下一頁' }))
  await screen.findByText('第 21–21 筆，共 21 筆')
  expect(fetchMock.mock.calls.some(([p]) => String(p).includes('offset=20'))).toBe(true)
})

test('一般使用者：無權限頁，不打稽核 API', async () => {
  const fetchMock = mount('user')
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin'))).toBe(false)
})

test('查看問答內容的稽核：中文標籤＋對象 qa id＋佇列種類（detail 本來就沒有內容可顯示）', () => {
  const entry = { id: 9, actor_user_id: 'me', actor_username: 'qa', action: 'qa_content.read', target_type: 'qa',
    target_id: 'q-1', detail: { qa_id: 'q-1', kinds: ['faithfulness', 'feedback'] }, created_at: null }
  expect(actionLabel(entry.action)).toBe('查看問答內容')
  expect(auditSummary(entry)).toBe('qa q-1・忠實度低分、倒讚')
})

test('研報隱藏／恢復的稽核：中文標籤＋檔名（原因全文不在 detail 裡）', () => {
  const hide = { id: 10, actor_user_id: 'me', actor_username: 'root', action: 'report.hide', target_type: 'report',
    target_id: 'a'.repeat(64), detail: { hidden: true, previous_hidden: false, has_reason: true, file_name: '台積電.pdf' },
    created_at: null }
  expect(actionLabel(hide.action)).toBe('隱藏研報')
  expect(auditSummary(hide)).toBe('台積電.pdf・已填原因')
  const restore = { ...hide, id: 11, action: 'report.restore', detail: { hidden: false, previous_hidden: true, has_reason: false } }
  expect(actionLabel(restore.action)).toBe('恢復研報')
  expect(auditSummary(restore)).toBe(`研報 ${'a'.repeat(12)}`)
})

test('調整權限的稽核：中文標籤＋授予／收回的 scope 與 super 變化', () => {
  const entry = { id: 12, actor_user_id: 'me', actor_username: 'root', action: 'user.set_privileges', target_type: 'user',
    target_id: 'u2', detail: { username: 'alice', is_super: { from: false, to: true }, scopes_added: ['ops.operate'],
      scopes_removed: ['qa_content.read'], via: 'web' }, created_at: null }
  expect(actionLabel(entry.action)).toBe('調整權限')
  expect(auditSummary(entry)).toBe('alice・設為 super admin・授予 ops.operate・收回 qa_content.read')
})
