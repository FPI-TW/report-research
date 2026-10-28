import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { CommandPalette } from './CommandPalette'
import { ShellContext } from './shellContext'

afterEach(() => vi.unstubAllGlobals())

function Where() {
  const { pathname, search } = useLocation()
  return <div data-testid="where">{pathname + search}</div>
}

function wrap(openSettings = vi.fn(), onClose = vi.fn()) {
  const fetchMock = vi.fn(async (input: string) => {
    const q = new URL(input, 'http://x').searchParams.get('q') ?? ''
    const all = [{ conversation_id: 'c1', title: '台積電資本支出' }, { conversation_id: 'c2', title: '先進封裝' }]
    return new Response(JSON.stringify(all.filter(c => !q || c.title.includes(q))), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <ShellContext.Provider value={{ openSearch: vi.fn(), openSettings }}>
        <MemoryRouter initialEntries={['/library']}>
          <Routes><Route path="*" element={<><CommandPalette open onClose={onClose} /><Where /></>} /></Routes>
        </MemoryRouter>
      </ShellContext.Provider>
    </QueryClientProvider>,
  )
  return { fetchMock, onClose, openSettings }
}

test('分「對話」「前往」兩組，輸入框自動取得焦點', async () => {
  wrap()
  const input = screen.getByRole('combobox', { name: '搜尋對話或頁面' })
  expect(input).toHaveFocus()
  expect(await screen.findByRole('option', { name: '台積電資本支出' })).toBeInTheDocument()
  expect(screen.getByText('對話')).toBeInTheDocument()
  expect(screen.getByText('前往')).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '新對話' })).toBeInTheDocument()
})

test('↑↓ 移動、Enter 開啟選取的項目', async () => {
  const { onClose } = wrap()
  await screen.findByRole('option', { name: '台積電資本支出' })
  const input = screen.getByRole('combobox')
  expect(screen.getByRole('option', { name: '台積電資本支出' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(input, { key: 'ArrowDown' })
  expect(screen.getByRole('option', { name: '先進封裝' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(onClose).toHaveBeenCalled()
  expect(screen.getByTestId('where')).toHaveTextContent('/assistant?c=c2')
})

test('輸入時：對話走後端搜尋，前往依名稱比對', async () => {
  const { fetchMock } = wrap()
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '共識' } })
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => new URL(u as string, 'http://x').searchParams.get('q') === '共識')).toBe(true))
  expect(screen.getByRole('option', { name: '券商共識' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: '研報庫' })).not.toBeInTheDocument()
})

test('沒有結果時說出來', async () => {
  wrap()
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzz不存在' } })
  expect(await screen.findByText('沒有符合的結果')).toBeInTheDocument()
})

test('「設定」開設定彈窗', async () => {
  const { openSettings } = wrap()
  fireEvent.click(screen.getByRole('option', { name: '設定' }))
  expect(openSettings).toHaveBeenCalledWith('account')
})
