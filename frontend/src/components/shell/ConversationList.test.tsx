import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { ConversationList } from './ConversationList'

afterEach(() => vi.unstubAllGlobals())

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/search']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

test('渲染新對話鈕與對話清單', async () => {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify([
      { conversation_id: 'c1', title: 'AI 伺服器供應鏈' },
    ]), { status: 200 }),
  ))
  wrap(<ConversationList />)
  expect(screen.getByRole('link', { name: '新對話' })).toHaveAttribute('href', '/ask')
  expect(await screen.findByText('AI 伺服器供應鏈')).toBeInTheDocument()
})
