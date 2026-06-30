import { describe, expect, test, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../theme'
import AskPage from './AskPage'

vi.mock('./api', () => ({
  getConversations: vi.fn().mockResolvedValue([]),
  getConversation: vi.fn().mockResolvedValue([]),
  deleteConversation: vi.fn(),
  sendFeedback: vi.fn(),
}))

afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={['/ask']}>
      <QueryClientProvider client={qc}>
        <MantineProvider theme={theme}>
          <AskPage />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('AskPage', () => {
  test('渲染輸入框與新對話鈕', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('ask-input')).toBeInTheDocument())
    expect(screen.getByTestId('ask-new')).toBeInTheDocument()
  })
})
